import Foundation

/// Everything on the phone, or one filter's share of it, cut every way the Library charts it:
/// by source, by kind, by Drive status, by year and by month.
public struct Breakdown: Sendable {
    public struct Slice: Identifiable, Hashable, Sendable {
        public let key: String
        public let title: String
        public let tally: Tally
        public var id: String { key }
    }
    /// One period with what of it is already in Drive.
    public struct Period: Identifiable, Hashable, Sendable {
        public let period: String
        public let total: Tally
        public let inDrive: Tally
        public var id: String { period }
        public var notInDrive: Tally { Tally(count: total.count - inDrive.count, bytes: total.bytes - inDrive.bytes) }
    }

    public var total = Tally()
    public var bySource: [Slice] = []
    public var byKind: [Slice] = []
    /// In Drive and can go, in Drive but kept by a safety rule, not in Drive yet.
    public var byStatus: [Slice] = []
    public var byYear: [Period] = []
    /// The last 24 months, oldest first.
    public var byMonth: [Period] = []
    public var oldest: String?
    public var newest: String?
    public var averageBytes: Int64 { total.count == 0 ? 0 : total.bytes / Int64(total.count) }

    public init() {}
}

extension Ledger {
    /// `filter` nil means everything on the phone.
    public static func breakdown(_ db: SQLiteReader, filter: Filter? = nil, now: Date = Date()) throws -> Breakdown {
        var b = Breakdown()
        let (w, args) = filter?.whereClause(now: now) ?? ("1 = 1", [])
        let base = "FROM assets a WHERE a.present_on_phone = 1 AND \(w)"
        let verified = "a.cloud_status = 'CLOUD_VERIFIED'"
        let tolerance = filter?.proxyTolerance ?? 0
        let keep = "(a.is_favourite = 1 OR a.proxy_suspicion > \(tolerance) OR COALESCE(a.sha256, '') = '' OR COALESCE(a.cloud_path, '') = '')"
        func tally(_ r: [Any?], _ i: Int) -> Tally { Tally(count: r.int(i), bytes: Int64(r.int(i + 1))) }

        let t = try db.rows("SELECT COUNT(*), COALESCE(SUM(a.size_bytes), 0), MIN(a.created_at_device), MAX(a.created_at_device) \(base)", args)[0]
        b.total = tally(t, 0)
        b.oldest = t[2] as? String
        b.newest = t[3] as? String

        b.bySource = try db.rows("SELECT \(Channel.sql) ch, COUNT(*), COALESCE(SUM(a.size_bytes), 0) \(base) GROUP BY ch ORDER BY 3 DESC, 1", args)
            .map { r in Breakdown.Slice(key: r.str(0), title: Channel(rawValue: r.str(0))?.title ?? r.str(0), tally: tally(r, 1)) }

        let kind = """
            CASE WHEN COALESCE(a.subtypes, '') LIKE '%screenshot%' THEN 'screenshot'
                 WHEN a.media_type = 'VIDEO' THEN 'video'
                 WHEN COALESCE(a.subtypes, '') LIKE '%live%' THEN 'live'
                 ELSE 'photo' END
            """
        let kindTitles = ["photo": "Photos", "live": "Live Photos", "video": "Videos", "screenshot": "Screenshots"]
        b.byKind = try db.rows("SELECT \(kind) k, COUNT(*), COALESCE(SUM(a.size_bytes), 0) \(base) GROUP BY k ORDER BY 3 DESC, 1", args)
            .map { r in Breakdown.Slice(key: r.str(0), title: kindTitles[r.str(0)] ?? r.str(0), tally: tally(r, 1)) }

        let s = try db.rows("""
            SELECT SUM(\(verified) AND NOT \(keep)), COALESCE(SUM(CASE WHEN \(verified) AND NOT \(keep) THEN a.size_bytes END), 0),
                   SUM(\(verified) AND \(keep)), COALESCE(SUM(CASE WHEN \(verified) AND \(keep) THEN a.size_bytes END), 0),
                   SUM(NOT \(verified)), COALESCE(SUM(CASE WHEN NOT \(verified) THEN a.size_bytes END), 0)
            \(base)
            """, args)[0]
        b.byStatus = [
            .init(key: "canGo", title: "In Drive, can go", tally: tally(s, 0)),
            .init(key: "kept", title: "In Drive, kept for safety", tally: tally(s, 2)),
            .init(key: "notInDrive", title: "Not in Drive yet", tally: tally(s, 4)),
        ].filter { $0.tally.count > 0 }

        func periods(_ expr: String, extra: String = "", _ more: [Any?] = []) throws -> [Breakdown.Period] {
            try db.rows("""
                SELECT \(expr) p, COUNT(*), COALESCE(SUM(a.size_bytes), 0),
                       SUM(\(verified)), COALESCE(SUM(CASE WHEN \(verified) THEN a.size_bytes END), 0)
                \(base) AND a.created_at_device IS NOT NULL \(extra) GROUP BY p ORDER BY p
                """, args + more)
                .map { r in Breakdown.Period(period: r.str(0), total: tally(r, 1), inDrive: tally(r, 3)) }
        }
        b.byYear = try periods("substr(a.created_at_device, 1, 4)")
        b.byMonth = try periods("substr(a.created_at_device, 1, 7)", extra: "AND a.created_at_device >= ?",
                                [Age(24, "m").cutoff(now: now)])
        return b
    }
}
