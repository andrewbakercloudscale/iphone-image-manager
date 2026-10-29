import Foundation

/// How one Checkup number is made: the rule behind it, what it is part of, and the same
/// items cut by source, by kind and over time.
public struct StatDetail: Sendable {
    public enum Stat: String, Identifiable, Sendable {
        case verified, freed, recentlyDeleted
        public var id: String { rawValue }
    }

    /// One bar of parts that add up to a whole, e.g. on the phone = checked safe + not yet.
    public struct Parts: Identifiable, Sendable {
        public let title: String
        public let slices: [Breakdown.Slice]
        public var id: String { title }
        public var total: Tally { slices.reduce(Tally()) { Tally(count: $0.count + $1.tally.count, bytes: $0.bytes + $1.tally.bytes) } }
    }

    public var stat: Stat
    public var total = Tally()
    public var parts: [Parts] = []
    public var bySource: [Breakdown.Slice] = []
    public var byKind: [Breakdown.Slice] = []
    /// Oldest first, keyed "2026-10" (by month) or "2026-10-28" (by day).
    public var overTime: [Breakdown.Slice] = []
    /// Recently Deleted only: when the first of these leaves it for good.
    public var firstExpiry: String?

    public init(stat: Stat) { self.stat = stat }
}

extension Ledger {
    static let kindSQL = """
        CASE WHEN COALESCE(a.subtypes, '') LIKE '%screenshot%' THEN 'screenshot'
             WHEN a.media_type = 'VIDEO' THEN 'video'
             WHEN COALESCE(a.subtypes, '') LIKE '%live%' THEN 'live'
             ELSE 'photo' END
        """
    static let kindTitles = ["photo": "Photos", "live": "Live Photos", "video": "Videos", "screenshot": "Screenshots"]

    /// The same queries the Checkup tiles come from (Ledger.read), cut further.
    public static func statDetail(_ db: SQLiteReader, _ stat: StatDetail.Stat, now: Date = Date()) throws -> StatDetail {
        var d = StatDetail(stat: stat)
        let monthAgo = Age(30, "d").cutoff(now: now)
        func tally(_ r: [Any?], _ i: Int) -> Tally { Tally(count: r.int(i), bytes: Int64(r.int(i + 1))) }
        func slice(_ key: String, _ title: String, _ t: Tally) -> Breakdown.Slice { .init(key: key, title: title, tally: t) }

        let base: String, size: String, when: String, args: [Any?]
        switch stat {
        case .verified:
            base = "FROM assets a WHERE a.present_on_phone = 1 AND a.cloud_status = 'CLOUD_VERIFIED'"
            size = "a.size_bytes"; when = "substr(COALESCE(a.cloud_verified_at, ''), 1, 7)"; args = []
        case .freed:
            base = "FROM deletion_events d JOIN assets a ON a.id = d.asset_id WHERE d.status = 'REMOVED'"
            size = "d.size_bytes"; when = "substr(COALESCE(d.completed_at, ''), 1, 7)"; args = []
        case .recentlyDeleted:
            base = "FROM deletion_events d JOIN assets a ON a.id = d.asset_id WHERE d.status = 'REMOVED' AND d.completed_at >= ?"
            size = "d.size_bytes"; when = "substr(d.completed_at, 1, 10)"; args = [monthAgo]
        }

        d.total = tally(try db.rows("SELECT COUNT(*), COALESCE(SUM(\(size)), 0) \(base)", args)[0], 0)
        d.bySource = try db.rows("SELECT \(Channel.sql) ch, COUNT(*), COALESCE(SUM(\(size)), 0) \(base) GROUP BY ch ORDER BY 3 DESC, 1", args)
            .map { slice($0.str(0), Channel(rawValue: $0.str(0))?.title ?? $0.str(0), tally($0, 1)) }
        d.byKind = try db.rows("SELECT \(kindSQL) k, COUNT(*), COALESCE(SUM(\(size)), 0) \(base) GROUP BY k ORDER BY 3 DESC, 1", args)
            .map { slice($0.str(0), kindTitles[$0.str(0)] ?? $0.str(0), tally($0, 1)) }
        d.overTime = try db.rows("SELECT \(when) p, COUNT(*), COALESCE(SUM(\(size)), 0) \(base) GROUP BY p ORDER BY p", args)
            .map { slice($0.str(0), $0.str(0).isEmpty ? "Unknown" : $0.str(0), tally($0, 1)) }

        switch stat {
        case .verified:
            let p = try db.rows("""
                SELECT SUM(a.present_on_phone = 1 AND a.cloud_status = 'CLOUD_VERIFIED'),
                       COALESCE(SUM(CASE WHEN a.present_on_phone = 1 AND a.cloud_status = 'CLOUD_VERIFIED' THEN a.size_bytes END), 0),
                       SUM(a.present_on_phone = 1 AND a.cloud_status <> 'CLOUD_VERIFIED'),
                       COALESCE(SUM(CASE WHEN a.present_on_phone = 1 AND a.cloud_status <> 'CLOUD_VERIFIED' THEN a.size_bytes END), 0),
                       SUM(a.present_on_phone = 0 AND a.cloud_status = 'CLOUD_VERIFIED'),
                       COALESCE(SUM(CASE WHEN a.present_on_phone = 0 AND a.cloud_status = 'CLOUD_VERIFIED' THEN a.size_bytes END), 0)
                FROM assets a
                """)[0]
            d.parts = [
                .init(title: "Your iPhone", slices: [slice("this", "Checked safe in Google Drive", tally(p, 0)),
                                                     slice("todo", "Not in Google Drive yet", tally(p, 2))]),
                .init(title: "Everything checked in Google Drive", slices: [slice("this", "Still on your iPhone", tally(p, 0)),
                                                                            slice("gone", "Already removed from your iPhone", tally(p, 4))]),
            ]
        case .freed, .recentlyDeleted:
            let p = try db.rows("""
                SELECT SUM(completed_at >= ?), COALESCE(SUM(CASE WHEN completed_at >= ? THEN size_bytes END), 0),
                       SUM(completed_at < ?), COALESCE(SUM(CASE WHEN completed_at < ? THEN size_bytes END), 0)
                FROM deletion_events WHERE status = 'REMOVED'
                """, [monthAgo, monthAgo, monthAgo, monthAgo])[0]
            let recent = slice(stat == .recentlyDeleted ? "this" : "recent", "In Recently Deleted (last 30 days)", tally(p, 0))
            let older = slice(stat == .freed ? "this" : "gone", "Gone for good (over 30 days ago)", tally(p, 2))
            d.parts = [.init(title: "Everything removed from your iPhone", slices: [recent, older])]
            if stat == .recentlyDeleted, let first = try db.rows(
                "SELECT MIN(completed_at) FROM deletion_events WHERE status = 'REMOVED' AND completed_at >= ?", [monthAgo])[0][0] as? String,
               let at = Fmt.date(first) {
                d.firstExpiry = ISO8601DateFormatter().string(from: at.addingTimeInterval(30 * 86400))
            }
        }
        return d
    }
}
