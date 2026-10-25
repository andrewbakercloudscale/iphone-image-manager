import Foundation

/// Where an asset came from, as the Python selector's `channel_of` decides it.
///
/// The SQL in `Channel.sql` is the same rule as `selector.py`: the bundle id when Photos
/// recorded one, else `IMG_` filenames that are not screenshots are the camera (iOS only
/// began attributing the camera around 2024-08), else unattributed. If the two ever
/// disagree, the app and the CLI count different sets, so change both together.
public enum Channel: String, CaseIterable, Identifiable, Sendable {
    case camera, whatsapp, screenshot, screenRecording, otherApps, unattributed
    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .camera: "Camera"
        case .whatsapp: "WhatsApp"
        case .screenshot: "Screenshots"
        case .screenRecording: "Screen recordings"
        case .otherApps: "Other apps"
        case .unattributed: "No app recorded"
        }
    }

    /// The `--source` name the CLI and cycle.sh use.
    public var cliSource: String? {
        switch self {
        case .camera: "camera"
        case .whatsapp: "whatsapp"
        case .screenshot: "screenshot"
        case .screenRecording: "screenrecording"
        case .otherApps: nil
        case .unattributed: "unattributed"
        }
    }

    static let sql = """
    CASE
      WHEN a.source_bundle_id = 'com.apple.camera' THEN 'camera'
      WHEN a.source_bundle_id = 'com.apple.springboard' THEN 'screenshot'
      WHEN a.source_bundle_id = 'com.apple.replayd' THEN 'screenRecording'
      WHEN a.source_bundle_id = 'net.whatsapp.WhatsApp' THEN 'whatsapp'
      WHEN COALESCE(a.source_bundle_id, '') <> '' THEN 'otherApps'
      WHEN COALESCE(a.filename, '') LIKE 'IMG\\_%' ESCAPE '\\'
           AND COALESCE(a.subtypes, '') NOT LIKE '%screenshot%' THEN 'camera'
      ELSE 'unattributed'
    END
    """
}

/// An age such as 18m, written the way the CLI takes it. Units as `retention.py`:
/// d = 1, w = 7, m = 30, y = 365 days.
public struct Age: Hashable, Sendable, CustomStringConvertible {
    public let amount: Int
    public let unit: Character

    public init(_ amount: Int, _ unit: Character) {
        self.amount = amount
        self.unit = unit
    }

    public init?(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespaces).lowercased()
        guard let last = t.last else { return nil }
        let hasUnit = "dwmy".contains(last)
        guard let n = Int(hasUnit ? String(t.dropLast()) : t), n >= 0 else { return nil }
        self.init(n, hasUnit ? last : "d")
    }

    public var days: Int {
        switch unit {
        case "w": amount * 7
        case "m": amount * 30
        case "y": amount * 365
        default: amount
        }
    }

    /// The CLI form: "18m".
    public var description: String { "\(amount)\(unit)" }

    /// "18 months".
    public var words: String {
        let name: String
        switch unit {
        case "w": name = "week"
        case "m": name = "month"
        case "y": name = "year"
        default: name = "day"
        }
        return amount == 1 ? "1 \(name)" : "\(amount) \(name)s"
    }

    /// The ISO timestamp an asset must be older than; `created_at_device` is stored as
    /// `2019-01-03T08:09:15Z`, so string comparison orders correctly.
    public func cutoff(now: Date = Date()) -> String {
        let d = now.addingTimeInterval(-Double(days) * 86_400)
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.string(from: d)
    }
}

/// A clean-up the app offers. Each maps onto a selector the CLI already understands.
public struct Recipe: Identifiable, Hashable, Sendable {
    public enum Kind: String, Sendable { case oldVideos, whatsapp, screenshots, screenRecordings, largeVideos }
    public let kind: Kind
    public var age: Age?
    public var minBytes: Int64?
    public var id: String { kind.rawValue }

    public init(kind: Kind, age: Age? = nil, minBytes: Int64? = nil) {
        self.kind = kind
        self.age = age
        self.minBytes = minBytes
    }

    public static let defaults: [Recipe] = [
        Recipe(kind: .oldVideos, age: Age(1, "y")),
        Recipe(kind: .whatsapp, age: Age(18, "m")),
        Recipe(kind: .screenshots, age: Age(1, "y")),
        Recipe(kind: .screenRecordings, age: Age(30, "d")),
        Recipe(kind: .largeVideos, minBytes: 500_000_000),
    ]

    public var title: String {
        switch kind {
        case .oldVideos: "Move old videos to Drive"
        case .whatsapp: "WhatsApp clean-up"
        case .screenshots: "Clear old screenshots"
        case .screenRecordings: "Screen recordings"
        case .largeVideos: "Large videos"
        }
    }

    public var detail: String {
        switch kind {
        case .oldVideos: "Camera videos older than \(age?.words ?? "any age")."
        case .whatsapp: "Older than \(age?.words ?? "any age"). Keeps favourites and your own albums."
        case .screenshots: "Screenshots older than \(age?.words ?? "any age")."
        case .screenRecordings: "Older than \(age?.words ?? "any age")."
        case .largeVideos: "Any age, bigger than \(Fmt.bytes(minBytes ?? 0))."
        }
    }

    public var symbol: String {
        switch kind {
        case .oldVideos: "video.fill"
        case .whatsapp: "bubble.left.and.bubble.right.fill"
        case .screenshots: "iphone"
        case .screenRecordings: "record.circle"
        case .largeVideos: "arrow.up.left.and.arrow.down.right"
        }
    }

    /// The matching CLI command, so the owner can run it today (Phase A runs nothing itself).
    public var command: String {
        var parts = ["iphone-image remove-from-iphone"]
        switch kind {
        case .oldVideos: parts += ["--source camera", "--type video"]
        case .whatsapp: parts += ["--source whatsapp"]
        case .screenshots: parts += ["--source screenshot"]
        case .screenRecordings: parts += ["--source screenrecording"]
        case .largeVideos: parts += ["--type video"]
        }
        if let age { parts.append("--older-than \(age)") }
        if let minBytes { parts.append("--min-size \(minBytes / 1_000_000)MB") }
        return parts.joined(separator: " ")
    }

    /// SQL over `assets a` choosing this recipe's assets, on the phone or not.
    func whereClause(now: Date) -> (String, [Any?]) {
        var c: [String] = []
        var args: [Any?] = []
        switch kind {
        case .oldVideos: c += ["(\(Channel.sql)) = 'camera'", "a.media_type = 'VIDEO'"]
        case .whatsapp: c.append("(\(Channel.sql)) = 'whatsapp'")
        case .screenshots: c.append("(\(Channel.sql)) = 'screenshot'")
        case .screenRecordings: c.append("(\(Channel.sql)) = 'screenRecording'")
        case .largeVideos: c.append("a.media_type = 'VIDEO'")
        }
        if let age {
            c.append("a.created_at_device < ?")
            args.append(age.cutoff(now: now))
        }
        if let minBytes {
            c.append("a.size_bytes >= ?")
            args.append(Int(minBytes))
        }
        return (c.joined(separator: " AND "), args)
    }
}

public struct Tally: Hashable, Sendable {
    public var count = 0
    public var bytes: Int64 = 0
    public init(count: Int = 0, bytes: Int64 = 0) {
        self.count = count
        self.bytes = bytes
    }
}

public struct ChannelStats: Identifiable, Hashable, Sendable {
    public let channel: Channel
    public var photos = Tally()
    public var videos = Tally()
    public var verified = Tally()
    public var id: String { channel.rawValue }
    public var total: Tally { Tally(count: photos.count + videos.count, bytes: photos.bytes + videos.bytes) }
    /// Share of bytes on the phone already verified in Drive.
    public var coverage: Double { total.bytes == 0 ? 1 : Double(verified.bytes) / Double(total.bytes) }
}

public struct RecipeStats: Identifiable, Hashable, Sendable {
    public let recipe: Recipe
    /// Everything the recipe selects that is still on the phone.
    public var selected = Tally()
    /// Of those, verified in Drive and not held back by a safety rule.
    public var removableNow = Tally()
    /// Selected but with no verified Drive copy yet.
    public var notBackedUp = Tally()
    /// Kept for safety: favourites, suspected proxies, Live Photos.
    public var kept = Tally()
    public var id: String { recipe.id }
}

public struct BigItem: Identifiable, Hashable, Sendable {
    public let id: Int
    public let filename: String
    public let created: String
    public let bytes: Int64
    public let channel: Channel
    public let inDrive: Bool
}

public struct Removal: Hashable, Sendable {
    public var total = Tally()
    /// Removed in the last 30 days: still in the phone's Recently Deleted.
    public var recent = Tally()
    public var lastAt: String?
}

/// Everything the Library, Checkup and Backup tabs show, read in one pass.
public struct LibrarySnapshot: Sendable {
    public var onPhone = Tally()
    public var verified = Tally()
    public var channels: [ChannelStats] = []
    public var recipes: [RecipeStats] = []
    /// Bytes on the phone per capture year, oldest first.
    public var years: [(year: String, bytes: Int64, count: Int)] = []
    /// Bytes captured per month, last 12 months, oldest first.
    public var months: [(month: String, bytes: Int64)] = []
    public var biggestVideos: [BigItem] = []
    public var proxies = 0
    public var favourites = 0
    public var live = 0
    public var apps: [(bundle: String, tally: Tally)] = []
    public var removed = Removal()
    public var unfinishedOperations: [Operation] = []
    public var readAt = Date()

    public init() {}

    public var coverage: Double { onPhone.bytes == 0 ? 1 : Double(verified.bytes) / Double(onPhone.bytes) }
    public var couldFree: Int64 { recipes.filter { $0.recipe.kind != .largeVideos }.reduce(0) { $0 + $1.selected.bytes } }
    /// Bytes added per week, averaged over the last 3 full months.
    public var weeklyGrowth: Int64 {
        let recent = months.dropLast().suffix(3)
        guard !recent.isEmpty else { return 0 }
        return recent.reduce(0) { $0 + $1.bytes } / Int64(recent.count) * 7 / 30
    }
}

/// A journal entry that started and never finished.
public struct Operation: Identifiable, Hashable, Sendable {
    public let id: Int
    public let operation: String
    public let command: String
    public let startedAt: String

    /// A removal never resumes on its own; fetch, upload and verify steps are idempotent.
    public var needsPerson: Bool { operation.hasPrefix("REMOVE") }

    public var plain: String {
        switch operation {
        case "DOWNLOAD": "Copy from Photos"
        case "CLOUD_UPLOAD": "Upload to Drive"
        case "REMOVE": "Remove from iPhone"
        default: operation.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

public enum Ledger {
    public static let defaultPath = NSString(string: "~/Desktop/iphone/iphone-image.sqlite").expandingTildeInPath

    /// Reads the snapshot. Safe while the Python engine is writing: read-only, WAL.
    public static func read(_ db: SQLiteReader, recipes: [Recipe] = Recipe.defaults, now: Date = Date()) throws -> LibrarySnapshot {
        var s = LibrarySnapshot()
        s.readAt = now
        let onPhone = "a.present_on_phone = 1"
        let verified = "a.cloud_status = 'CLOUD_VERIFIED'"

        for r in try db.rows("""
            SELECT \(Channel.sql) ch, a.media_type, COUNT(*), COALESCE(SUM(a.size_bytes), 0),
                   SUM(\(verified)), COALESCE(SUM(CASE WHEN \(verified) THEN a.size_bytes END), 0)
            FROM assets a WHERE \(onPhone) GROUP BY ch, a.media_type
            """) {
            guard let ch = Channel(rawValue: r.str(0)) else { continue }
            var stats = s.channels.first { $0.channel == ch } ?? ChannelStats(channel: ch)
            let t = Tally(count: r.int(2), bytes: Int64(r.int(3)))
            if r.str(1) == "VIDEO" { stats.videos = add(stats.videos, t) } else { stats.photos = add(stats.photos, t) }
            stats.verified = add(stats.verified, Tally(count: r.int(4), bytes: Int64(r.int(5))))
            s.channels.removeAll { $0.channel == ch }
            s.channels.append(stats)
            s.onPhone = add(s.onPhone, t)
            s.verified = add(s.verified, Tally(count: r.int(4), bytes: Int64(r.int(5))))
        }
        s.channels.sort { $0.total.bytes > $1.total.bytes }

        let keep = "(a.is_favourite = 1 OR a.proxy_suspicion >= 0.5 OR COALESCE(a.subtypes, '') LIKE '%live%')"
        for recipe in recipes {
            let (w, args) = recipe.whereClause(now: now)
            let r = try db.rows("""
                SELECT COUNT(*), COALESCE(SUM(a.size_bytes), 0),
                       SUM(\(verified) AND NOT \(keep)), COALESCE(SUM(CASE WHEN \(verified) AND NOT \(keep) THEN a.size_bytes END), 0),
                       SUM(NOT \(verified)), COALESCE(SUM(CASE WHEN NOT \(verified) THEN a.size_bytes END), 0),
                       SUM(\(keep)), COALESCE(SUM(CASE WHEN \(keep) THEN a.size_bytes END), 0)
                FROM assets a WHERE \(onPhone) AND \(w)
                """, args)[0]
            var rs = RecipeStats(recipe: recipe)
            rs.selected = Tally(count: r.int(0), bytes: Int64(r.int(1)))
            rs.removableNow = Tally(count: r.int(2), bytes: Int64(r.int(3)))
            rs.notBackedUp = Tally(count: r.int(4), bytes: Int64(r.int(5)))
            rs.kept = Tally(count: r.int(6), bytes: Int64(r.int(7)))
            s.recipes.append(rs)
        }

        s.years = try db.rows("""
            SELECT substr(a.created_at_device, 1, 4) y, COALESCE(SUM(a.size_bytes), 0), COUNT(*)
            FROM assets a WHERE \(onPhone) AND a.created_at_device IS NOT NULL GROUP BY y ORDER BY y
            """).map { ($0.str(0), Int64($0.int(1)), $0.int(2)) }

        let yearAgo = Age(13, "m").cutoff(now: now)
        s.months = try db.rows("""
            SELECT substr(a.created_at_device, 1, 7) m, COALESCE(SUM(a.size_bytes), 0)
            FROM assets a WHERE a.created_at_device >= ? GROUP BY m ORDER BY m
            """, [yearAgo]).map { ($0.str(0), Int64($0.int(1))) }.suffix(12).map { $0 }

        s.biggestVideos = try db.rows("""
            SELECT a.id, COALESCE(a.filename, ''), COALESCE(a.created_at_device, ''), a.size_bytes, \(Channel.sql), \(verified)
            FROM assets a WHERE \(onPhone) AND a.media_type = 'VIDEO' ORDER BY a.size_bytes DESC LIMIT 20
            """).map {
            BigItem(id: $0.int(0), filename: $0.str(1), created: $0.str(2), bytes: Int64($0.int(3)),
                    channel: Channel(rawValue: $0.str(4)) ?? .unattributed, inDrive: $0.int(5) == 1)
        }

        let safety = try db.rows("""
            SELECT SUM(a.proxy_suspicion >= 0.5), SUM(a.is_favourite = 1), SUM(COALESCE(a.subtypes, '') LIKE '%live%')
            FROM assets a WHERE \(onPhone)
            """)[0]
        s.proxies = safety.int(0)
        s.favourites = safety.int(1)
        s.live = safety.int(2)

        s.apps = try db.rows("""
            SELECT a.source_bundle_id, COUNT(*), COALESCE(SUM(a.size_bytes), 0) FROM assets a
            WHERE \(onPhone) AND COALESCE(a.source_bundle_id, '') <> '' AND a.source_bundle_id <> 'com.apple.camera'
            GROUP BY a.source_bundle_id ORDER BY 3 DESC LIMIT 8
            """).map { ($0.str(0), Tally(count: $0.int(1), bytes: Int64($0.int(2)))) }

        let monthAgo = Age(30, "d").cutoff(now: now)
        let rm = try db.rows("""
            SELECT COUNT(*), COALESCE(SUM(size_bytes), 0),
                   SUM(completed_at >= ?), COALESCE(SUM(CASE WHEN completed_at >= ? THEN size_bytes END), 0),
                   MAX(completed_at)
            FROM deletion_events WHERE status = 'REMOVED'
            """, [monthAgo, monthAgo])[0]
        s.removed = Removal(total: Tally(count: rm.int(0), bytes: Int64(rm.int(1))),
                            recent: Tally(count: rm.int(2), bytes: Int64(rm.int(3))), lastAt: rm[4] as? String)

        s.unfinishedOperations = try db.rows("""
            SELECT id, operation, COALESCE(command, ''), started_at FROM operations
            WHERE status = 'STARTED' AND finished_at IS NULL ORDER BY started_at DESC
            """).map { Operation(id: $0.int(0), operation: $0.str(1), command: $0.str(2), startedAt: $0.str(3)) }
        return s
    }

    private static func add(_ a: Tally, _ b: Tally) -> Tally { Tally(count: a.count + b.count, bytes: a.bytes + b.bytes) }
}

public enum Fmt {
    public static func bytes(_ b: Int64) -> String {
        let gb = Double(b) / 1e9
        if gb >= 100 { return "\(Int(gb.rounded())) GB" }
        if gb >= 1 { return String(format: "%.1f GB", gb) }
        let mb = Double(b) / 1e6
        return mb >= 1 ? "\(Int(mb.rounded())) MB" : "\(Int((Double(b) / 1e3).rounded())) KB"
    }

    public static func count(_ n: Int) -> String { n.formatted(.number) }

    public static func pct(_ v: Double) -> String { "\(Int((v * 100).rounded()))%" }

    /// "net.whatsapp.WhatsApp" to "WhatsApp", for the bundles the CLI has names for.
    public static func app(_ bundle: String) -> String {
        let names = [
            "net.whatsapp.WhatsApp": "WhatsApp", "com.apple.springboard": "Screenshots",
            "com.apple.replayd": "Screen recordings", "com.apple.sharingd": "AirDrop",
            "com.toyopagroup.picaboo": "Snapchat", "com.apple.mobilesafari": "Safari",
            "com.google.chrome.ios": "Chrome", "com.apple.MobileSMS": "Messages",
            "com.atebits.Tweetie2": "X", "com.burbn.instagram": "Instagram",
            "com.facebook.Facebook": "Facebook", "ph.telegra.Telegraph": "Telegram",
            "com.google.Gmail": "Gmail", "com.google.Drive": "Google Drive",
            "com.openai.chat": "ChatGPT", "ai.x.GrokApp": "Grok",
        ]
        return names[bundle] ?? bundle.split(separator: ".").last.map(String.init) ?? bundle
    }
}
