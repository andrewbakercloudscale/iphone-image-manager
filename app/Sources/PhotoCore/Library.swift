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

/// A clean-up the app offers: a preset, or a filter the owner built. Every one is a
/// `Filter`, which maps onto a selector the CLI already understands.
public struct Recipe: Identifiable, Hashable, Sendable {
    public enum Kind: String, Sendable { case oldVideos, cameraPhotos, whatsapp, screenshots, screenRecordings, largeVideos, custom }
    public let kind: Kind
    public var age: Age?
    public var minBytes: Int64?
    /// Set for `.custom`; presets derive theirs.
    private var customFilter: Filter?

    public init(kind: Kind, age: Age? = nil, minBytes: Int64? = nil) {
        self.kind = kind
        self.age = age
        self.minBytes = minBytes
    }

    public init(custom filter: Filter) {
        kind = .custom
        customFilter = filter
    }

    public var id: String { kind == .custom ? "custom-\(filter.key)" : kind.rawValue }

    public static let defaults: [Recipe] = [
        Recipe(kind: .oldVideos, age: Age(1, "y")),
        Recipe(kind: .whatsapp, age: Age(18, "m")),
        Recipe(kind: .screenshots, age: Age(1, "y")),
        Recipe(kind: .screenRecordings, age: Age(30, "d")),
        Recipe(kind: .largeVideos, minBytes: 500 * Filter.mb),
    ]

    public var filter: Filter {
        if let customFilter { return customFilter }
        var f = Filter()
        f.olderThan = age?.description
        switch kind {
        case .oldVideos: f.source = "camera"; f.type = "video"
        case .cameraPhotos: f.source = "camera"; f.type = "photo"
        case .whatsapp: f.source = "whatsapp"
        case .screenshots: f.source = "screenshot"
        case .screenRecordings: f.source = "screenrecording"
        case .largeVideos: f.type = "video"; f.minMB = Int((minBytes ?? 0) / Filter.mb)
        case .custom: break
        }
        return f
    }

    public var title: String {
        switch kind {
        case .oldVideos: "Old camera videos"
        case .cameraPhotos: "Old camera photos"
        case .whatsapp: "Old WhatsApp media"
        case .screenshots: "Old screenshots"
        case .screenRecordings: "Old screen recordings"
        case .largeVideos: "Large videos"
        case .custom: filter.name.isEmpty ? filter.summary : filter.name
        }
    }

    public var detail: String {
        switch kind {
        case .oldVideos: "Camera videos older than \(age?.words ?? "any age")."
        case .cameraPhotos: "Camera photos older than \(age?.words ?? "any age"). Keeps favourites and your own albums."
        case .whatsapp: "Older than \(age?.words ?? "any age"). Keeps favourites and your own albums."
        case .screenshots: "Screenshots older than \(age?.words ?? "any age")."
        case .screenRecordings: "Older than \(age?.words ?? "any age")."
        case .largeVideos: "Any age, bigger than \(Filter.size(Int((minBytes ?? 0) / Filter.mb)))."
        case .custom: filter.summary + "."
        }
    }

    public var symbol: String {
        switch kind {
        case .oldVideos: "video.fill"
        case .cameraPhotos: "photo.fill"
        case .whatsapp: "bubble.left.and.bubble.right.fill"
        case .screenshots: "iphone"
        case .screenRecordings: "record.circle"
        case .largeVideos: "arrow.up.left.and.arrow.down.right"
        case .custom: "line.3.horizontal.decrease.circle.fill"
        }
    }

    /// The selector arguments the CLI takes for this recipe.
    public var selectorArgs: [String] { filter.selectorArgs }
    /// The arguments for `remove-from-iphone`: the selector plus the low-res tolerance.
    public var removalArgs: [String] { filter.removalArgs }
    public var backups: [(type: String, source: String, older: String?)] { filter.backups }
    public var command: String { "iphone-image remove-from-iphone " + removalArgs.joined(separator: " ") }

    /// SQL over `assets a` choosing this recipe's assets, on the phone or not.
    func whereClause(now: Date) -> (String, [Any?]) { filter.whereClause(now: now) }
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
    /// When the oldest and newest items with no Drive copy were taken (ledger timestamps).
    public var notBackedUpOldest: String?
    public var notBackedUpNewest: String?
    /// "taken between 3 weeks and 2 years ago", for what has no Drive copy.
    public var notBackedUpAge: String? {
        guard let o = notBackedUpOldest, let n = notBackedUpNewest else { return nil }
        return Fmt.takenBetween(o, n)
    }
    /// What has no Drive copy, split by kind and, for Other apps, by app, largest first.
    public var notBackedUpPhotos = Tally()
    public var notBackedUpVideos = Tally()
    public struct AppTally: Hashable, Sendable { public let app: String; public var tally: Tally }
    public var notBackedUpByApp: [AppTally] = []

    /// "580 photos (1.2 GB) and 834 videos (37 GB)".
    public var notBackedUpKinds: String? {
        func part(_ t: Tally, _ one: String) -> String? {
            t.count == 0 ? nil : "\(Fmt.count(t.count)) \(one)\(t.count == 1 ? "" : "s") (\(Fmt.bytes(t.bytes)))"
        }
        let parts = [part(notBackedUpPhotos, "photo"), part(notBackedUpVideos, "video")].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " and ")
    }

    /// "Chrome 653 (109 MB) · Snapchat 284 (2.4 GB) · …", the five biggest by size.
    public var notBackedUpApps: String? {
        guard notBackedUpByApp.count > 1 else { return nil }
        let top = notBackedUpByApp.prefix(5).map { "\($0.app) \(Fmt.count($0.tally.count)) (\(Fmt.bytes($0.tally.bytes)))" }
        let rest = notBackedUpByApp.dropFirst(5).reduce(0) { $0 + $1.tally.count }
        return top.joined(separator: " · ") + (rest > 0 ? " · \(Fmt.count(rest)) from \(notBackedUpByApp.count - 5) more apps" : "")
    }
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
    /// In Drive but held back by a removal rule: favourites, suspected proxies, no hash or no Drive path.
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
    /// On-phone items by where they were taken, biggest first. Empty until `places` has run.
    public var places: [PlaceStats] = []
    /// On the phone with no recorded place: no GPS, or `places` has not seen them yet.
    public var unplaced = Tally()
    /// When `places` last wrote the ledger, ISO; nil if never.
    public var placesAt: String?
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

/// One country, with its biggest cities.
public struct PlaceStats: Identifiable, Hashable, Sendable {
    public let country: String
    public var total = Tally()
    public var verified = Tally()
    public var cities: [CityStats] = []
    public var id: String { country }
    public var coverage: Double { total.bytes == 0 ? 1 : Double(verified.bytes) / Double(total.bytes) }
}

public struct CityStats: Identifiable, Hashable, Sendable {
    public let city: String
    public let total: Tally
    public let verified: Tally
    public var id: String { city }
    public var coverage: Double { total.bytes == 0 ? 1 : Double(verified.bytes) / Double(total.bytes) }
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
        case "CLOUD_UPLOAD": "Upload to Google Drive"
        case "REMOVE": "Remove from iPhone"
        case "GPHOTOS_SEND": "Send to Google Photos"
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
        for r in try db.rows("""
            SELECT \(Channel.sql) ch, MIN(a.created_at_device), MAX(a.created_at_device)
            FROM assets a WHERE \(onPhone) AND NOT \(verified) AND a.created_at_device IS NOT NULL GROUP BY ch
            """) {
            if let i = s.channels.firstIndex(where: { $0.channel.rawValue == r.str(0) }) {
                s.channels[i].notBackedUpOldest = r.str(1)
                s.channels[i].notBackedUpNewest = r.str(2)
            }
        }
        for r in try db.rows("""
            SELECT \(Channel.sql) ch, a.media_type, COALESCE(a.source_bundle_id, ''), COUNT(*), COALESCE(SUM(a.size_bytes), 0)
            FROM assets a WHERE \(onPhone) AND NOT \(verified) GROUP BY ch, a.media_type, a.source_bundle_id
            """) {
            guard let i = s.channels.firstIndex(where: { $0.channel.rawValue == r.str(0) }) else { continue }
            let t = Tally(count: r.int(3), bytes: Int64(r.int(4)))
            if r.str(1) == "VIDEO" { s.channels[i].notBackedUpVideos = add(s.channels[i].notBackedUpVideos, t) }
            else { s.channels[i].notBackedUpPhotos = add(s.channels[i].notBackedUpPhotos, t) }
            guard s.channels[i].channel == .otherApps else { continue }
            let app = Fmt.app(r.str(2))
            if let j = s.channels[i].notBackedUpByApp.firstIndex(where: { $0.app == app }) {
                s.channels[i].notBackedUpByApp[j].tally = add(s.channels[i].notBackedUpByApp[j].tally, t)
            } else {
                s.channels[i].notBackedUpByApp.append(.init(app: app, tally: t))
            }
        }
        for i in s.channels.indices { s.channels[i].notBackedUpByApp.sort { $0.tally.bytes > $1.tally.bytes } }

        // The per-asset rules of remove.py's `plan`, so the estimate matches the plan. Not
        // mirrored: user albums and incomplete Live Photo or burst groups, which the plan
        // still blocks, so this stays an estimate and the plan stays the truth.
        // A suspected low-res stand-in is not kept here: the plan lets it go when Drive's
        // copy is exactly the original's size, which only the plan can see.
        for recipe in recipes {
            s.recipes.append(try stats(db, recipe, now: now))
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
            SELECT SUM(a.proxy_suspicion > 0), SUM(a.is_favourite = 1), SUM(COALESCE(a.subtypes, '') LIKE '%live%')
            FROM assets a WHERE \(onPhone)
            """)[0]
        s.proxies = safety.int(0)
        s.favourites = safety.int(1)
        s.live = safety.int(2)

        // A ledger from before places were added has no location_id: it simply has no places yet.
        try? readPlaces(db, into: &s)

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

    /// By country, then city, from `locations` (written by `iphone-image places`).
    static func readPlaces(_ db: SQLiteReader, into s: inout LibrarySnapshot) throws {
        let verified = "a.cloud_status = 'CLOUD_VERIFIED'"
        let cols = """
            COUNT(*), COALESCE(SUM(a.size_bytes), 0), SUM(\(verified)),
            COALESCE(SUM(CASE WHEN \(verified) THEN a.size_bytes END), 0)
            """
        let from = "FROM assets a LEFT JOIN locations l ON l.id = a.location_id WHERE a.present_on_phone = 1"
        func tallies(_ r: [Any?], _ i: Int) -> (Tally, Tally) {
            (Tally(count: r.int(i), bytes: Int64(r.int(i + 1))), Tally(count: r.int(i + 2), bytes: Int64(r.int(i + 3))))
        }
        var byCountry: [String: PlaceStats] = [:]
        for r in try db.rows("SELECT l.country, \(cols) \(from) AND l.country IS NOT NULL GROUP BY l.country") {
            let (t, v) = tallies(r, 1)
            byCountry[r.str(0)] = PlaceStats(country: r.str(0), total: t, verified: v)
        }
        for r in try db.rows("SELECT l.country, l.city, \(cols) \(from) AND l.city IS NOT NULL GROUP BY l.country, l.city") {
            let (t, v) = tallies(r, 2)
            byCountry[r.str(0)]?.cities.append(CityStats(city: r.str(1), total: t, verified: v))
        }
        s.places = byCountry.values.map { p in
            var p = p
            p.cities.sort { $0.total.bytes > $1.total.bytes }
            return p
        }
        .sorted { $0.total.bytes > $1.total.bytes }
        let u = try db.rows("SELECT \(cols) \(from) AND l.id IS NULL")[0]
        s.unplaced = tallies(u, 0).0
        s.placesAt = try db.rows("SELECT MAX(looked_up_at) FROM locations")[0][0] as? String
    }

    /// What one recipe or filter selects on the phone, split the way removal would split it.
    /// The per-asset rules of remove.py's `plan`, so the estimate matches the plan. Not
    /// mirrored: user albums and incomplete Live Photo or burst groups, which the plan
    /// still blocks, so this stays an estimate and the plan stays the truth.
    public static func stats(_ db: SQLiteReader, _ recipe: Recipe, now: Date = Date()) throws -> RecipeStats {
        let verified = "a.cloud_status = 'CLOUD_VERIFIED'"
        let keep = "(a.is_favourite = 1 OR COALESCE(a.sha256, '') = '' OR COALESCE(a.cloud_path, '') = '')"
        let (w, args) = recipe.whereClause(now: now)
        let r = try db.rows("""
            SELECT COUNT(*), COALESCE(SUM(a.size_bytes), 0),
                   SUM(\(verified) AND NOT \(keep)), COALESCE(SUM(CASE WHEN \(verified) AND NOT \(keep) THEN a.size_bytes END), 0),
                   SUM(NOT \(verified)), COALESCE(SUM(CASE WHEN NOT \(verified) THEN a.size_bytes END), 0),
                   SUM(\(verified) AND \(keep)), COALESCE(SUM(CASE WHEN \(verified) AND \(keep) THEN a.size_bytes END), 0)
            FROM assets a WHERE a.present_on_phone = 1 AND \(w)
            """, args)[0]
        var rs = RecipeStats(recipe: recipe)
        rs.selected = Tally(count: r.int(0), bytes: Int64(r.int(1)))
        rs.removableNow = Tally(count: r.int(2), bytes: Int64(r.int(3)))
        rs.notBackedUp = Tally(count: r.int(4), bytes: Int64(r.int(5)))
        rs.kept = Tally(count: r.int(6), bytes: Int64(r.int(7)))
        return rs
    }

    private static func add(_ a: Tally, _ b: Tally) -> Tally { Tally(count: a.count + b.count, bytes: a.bytes + b.bytes) }
}

public enum Fmt {
    /// "between 3 weeks and 2 years ago", from two ledger timestamps.
    public static func takenBetween(_ oldest: String, _ newest: String, now: Date = Date()) -> String? {
        guard let o = date(oldest), let n = date(newest) else { return nil }
        let a = ago(n, now: now), b = ago(o, now: now)
        return a == b ? "taken \(a) ago" : "taken between \(a) and \(b) ago"
    }

    /// The age in the largest whole unit: "today", "5 days", "3 weeks", "4 months", "2 years".
    public static func ago(_ d: Date, now: Date = Date()) -> String {
        let days = Int(now.timeIntervalSince(d) / 86_400)
        func n(_ v: Int, _ u: String) -> String { "\(v) \(u)\(v == 1 ? "" : "s")" }
        if days < 1 { return "less than a day" }
        if days < 14 { return n(days, "day") }
        if days < 60 { return n(days / 7, "week") }
        if days < 730 { return n(days / 30, "month") }
        return n(days / 365, "year")
    }

    /// Ledger timestamps are ISO 8601, with or without fractional seconds or a zone.
    static func date(_ s: String) -> Date? {
        let f = ISO8601DateFormatter()
        if let d = f.date(from: s) { return d }
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = f.date(from: s) { return d }
        f.formatOptions = [.withFullDate]
        return f.date(from: String(s.prefix(10)))
    }

    /// "27 Nov 2026", for a ledger timestamp.
    public static func dateOnly(_ s: String) -> String? {
        date(s).map { $0.formatted(.dateTime.day().month(.abbreviated).year()) }
    }

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

extension Ledger {
    /// Everything from one source still on the phone with no verified Drive copy, for
    /// sources the backup jobs cannot select by name (Other apps spans many apps).
    public static func notBackedUpIds(_ db: SQLiteReader, channel: Channel) throws -> [Int] {
        try db.rows("""
            SELECT a.id FROM assets a
            WHERE a.present_on_phone = 1 AND a.cloud_status <> 'CLOUD_VERIFIED' AND \(Channel.sql) = ?
            ORDER BY a.created_at_device
            """, [channel.rawValue]).map { $0.int(0) }
    }
}
