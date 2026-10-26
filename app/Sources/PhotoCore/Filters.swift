import Foundation

/// A selection of the library the owner builds: "WhatsApp videos over 50 MB, older than
/// 6 months". Each field narrows, exactly as `selector.py`'s `Selector` does, and each maps
/// onto one of its flags, so what the app counts is what the CLI acts on.
public struct Filter: Codable, Hashable, Sendable {
    /// Bytes in the CLI's "MB": `parse_size` counts in powers of 1024.
    public static let mb: Int64 = 1_048_576

    public var name = ""
    /// A `--source` name (camera, whatsapp, screenshot, screenrecording, unattributed) or
    /// an app's bundle identifier.
    public var source: String?
    /// photo, video, live, screenshot, burst or raw: `selector.py`'s MEDIA_TYPES.
    public var type: String?
    public var olderThan: String?
    public var newerThan: String?
    public var year: Int?
    public var minMB: Int?
    public var maxMB: Int?
    /// Where it was taken, as `iphone-image places` recorded it.
    public var country: String?
    public var city: String?
    /// Items picked one by one (`--id`), e.g. from Biggest videos.
    public var ids: [Int] = []
    /// How much proxy suspicion a removal tolerates, 0 to 1 (`--proxy-threshold`).
    public var proxyTolerance: Double = 0

    public init(name: String = "", source: String? = nil, type: String? = nil, olderThan: String? = nil,
                newerThan: String? = nil, year: Int? = nil, minMB: Int? = nil, maxMB: Int? = nil, country: String? = nil, city: String? = nil, ids: [Int] = [], proxyTolerance: Double = 0) {
        self.ids = ids
        self.country = country
        self.city = city
        self.name = name
        self.source = source
        self.type = type
        self.olderThan = olderThan
        self.newerThan = newerThan
        self.year = year
        self.minMB = minMB
        self.maxMB = maxMB
        self.proxyTolerance = proxyTolerance
    }

    /// Everything but the name: two filters selecting the same set are the same clean-up.
    public var key: String { removalArgs.joined(separator: " ") }

    public var isEmpty: Bool { selectorArgs.isEmpty }

    public var selectorArgs: [String] {
        var a: [String] = []
        if let source { a += ["--source", source] }
        if let type { a += ["--type", type] }
        if let olderThan { a += ["--older-than", olderThan] }
        if let newerThan { a += ["--newer-than", newerThan] }
        if let year { a += ["--year", String(year)] }
        if let minMB { a += ["--min-size", "\(minMB)MB"] }
        if let maxMB { a += ["--max-size", "\(maxMB)MB"] }
        if let country { a += ["--country", country] }
        if let city { a += ["--city", city] }
        for id in ids { a += ["--id", String(id)] }
        return a
    }

    public var removalArgs: [String] {
        selectorArgs + (proxyTolerance > 0 ? ["--proxy-threshold", String(format: "%.2f", proxyTolerance)] : [])
    }

    /// The cycle.sh jobs that back this selection up. cycle.sh takes a source, photo or
    /// video, and an age; size and year it ignores, so it backs up a little more than the
    /// filter, which costs only time. No source, no job: cycle.sh would read it as camera.
    public var backups: [(type: String, source: String, older: String?)] {
        guard let source, ids.isEmpty else { return [] }
        let types: [String]
        switch type {
        case "video": types = ["video"]
        case nil: types = ["photo", "video"]
        default: types = ["photo"]
        }
        return types.map { ($0, source, olderThan) }
    }

    /// SQL over `assets a`, the same rules as `Selector.where`.
    public func whereClause(now: Date) -> (String, [Any?]) {
        var c: [String] = []
        var args: [Any?] = []
        if let source {
            if let ch = Channel.allCases.first(where: { $0.cliSource == source }) {
                c.append("(\(Channel.sql)) = '\(ch.rawValue)'")
            } else {
                c.append("a.source_bundle_id = ?")
                args.append(source)
            }
        }
        switch type {
        case "photo": c.append("a.media_type = 'PHOTO'")
        case "video": c.append("a.media_type = 'VIDEO'")
        case "live", "screenshot", "burst":
            c.append("a.subtypes LIKE ?")
            args.append("%\(type!)%")
        case "raw": c.append("a.is_raw = 1")
        default: break
        }
        if let olderThan, let age = Age(olderThan) {
            c.append("a.created_at_device < ?")
            args.append(age.cutoff(now: now))
        }
        if let newerThan, let age = Age(newerThan) {
            c.append("a.created_at_device > ?")
            args.append(age.cutoff(now: now))
        }
        if let year {
            c.append("a.created_at_device LIKE ?")
            args.append("\(year)-%")
        }
        if let minMB {
            c.append("a.size_bytes >= ?")
            args.append(Int(Int64(minMB) * Self.mb))
        }
        if let maxMB {
            c.append("a.size_bytes <= ?")
            args.append(Int(Int64(maxMB) * Self.mb))
        }
        if !ids.isEmpty { c.append("a.id IN (\(ids.map(String.init).joined(separator: ",")))") }
        for (column, value) in [("country", country), ("city", city)] {
            if let value {
                c.append("a.location_id IN (SELECT id FROM locations WHERE \(column) = ? COLLATE NOCASE)")
                args.append(value)
            }
        }
        return (c.isEmpty ? "1 = 1" : c.joined(separator: " AND "), args)
    }

    /// "WhatsApp videos · over 50 MB · older than 6 months".
    public var summary: String {
        let what: String = {
            switch type {
            case "photo": "photos"
            case "video": "videos"
            case "live": "Live Photos"
            case "screenshot": "screenshots"
            case "burst": "bursts"
            case "raw": "RAW photos"
            default: "everything"
            }
        }()
        if !ids.isEmpty { return ids.count == 1 ? "1 picked item" : "\(ids.count) picked items" }
        var parts = [source.map { "\(Filter.sourceTitle($0)) \(what)" } ?? what.prefix(1).uppercased() + what.dropFirst()]
        if let minMB { parts.append("over \(Filter.size(minMB))") }
        if let maxMB { parts.append("under \(Filter.size(maxMB))") }
        if let olderThan { parts.append("older than \(Age(olderThan)?.words ?? olderThan)") }
        if let newerThan { parts.append("newer than \(Age(newerThan)?.words ?? newerThan)") }
        if let year { parts.append("from \(year)") }
        if let place = city ?? country { parts.append("taken in \(place)") }
        return parts.joined(separator: " · ")
    }

    public static func size(_ mb: Int) -> String { mb >= 1024 && mb % 1024 == 0 ? "\(mb / 1024) GB" : "\(mb) MB" }

    public static func sourceTitle(_ source: String) -> String {
        if let ch = Channel.allCases.first(where: { $0.cliSource == source }) { return ch.title }
        return Fmt.app(source)
    }
}

/// Clean-ups the library itself suggests: candidate filters, each measured against the
/// ledger, kept when they would free something worth the bother.
public struct Suggestion: Identifiable, Hashable, Sendable {
    public let why: String
    public let stats: RecipeStats
    public var id: String { stats.recipe.id }
}

extension Ledger {
    public static func suggestions(_ db: SQLiteReader, apps: [(bundle: String, tally: Tally)], now: Date = Date()) throws -> [Suggestion] {
        var candidates: [(String, Filter)] = [
            ("Forwarded videos are the biggest thing WhatsApp leaves behind",
             Filter(name: "Big old WhatsApp videos", source: "whatsapp", type: "video", olderThan: "6m", minMB: 20)),
            ("Memes and forwards you will not scroll back to",
             Filter(name: "WhatsApp photos over a year old", source: "whatsapp", type: "photo", olderThan: "1y")),
            ("Single videos that each cost a gigabyte",
             Filter(name: "Camera videos over 1 GB", source: "camera", type: "video", minMB: 1024)),
            ("Videos from more than two years ago, safe in Google Drive",
             Filter(name: "Camera videos over 2 years old", source: "camera", type: "video", olderThan: "2y")),
            ("Screenshots are rarely needed after a few months",
             Filter(name: "Screenshots over 90 days old", source: "screenshot", olderThan: "90d")),
            ("Screen recordings are big and usually one-off",
             Filter(name: "Screen recordings over 30 days old", source: "screenrecording", olderThan: "30d")),
            ("Burst shots: the picks stay with the rest of the burst in Google Drive",
             Filter(name: "Bursts over 30 days old", type: "burst", olderThan: "30d")),
            ("Old videos: once in your Google Drive archive they need not fill the phone",
             Filter(name: "Videos over 5 years old", type: "video", olderThan: "5y")),
        ]
        let known: Set<String> = ["com.apple.camera", "net.whatsapp.WhatsApp", "com.apple.springboard", "com.apple.replayd"]
        for app in apps.prefix(4) where !known.contains(app.bundle) {
            candidates.append(("\(Fmt.app(app.bundle)) saves \(Fmt.bytes(app.tally.bytes)) to your library",
                               Filter(name: "\(Fmt.app(app.bundle)) over a year old", source: app.bundle, olderThan: "1y")))
        }
        let floor: Int64 = 200 * Filter.mb
        return try candidates.compactMap { why, f in
            let s = try stats(db, Recipe(custom: f), now: now)
            return s.selected.bytes >= floor ? Suggestion(why: why, stats: s) : nil
        }
        .sorted { ($0.stats.removableNow.bytes, $0.stats.selected.bytes) > ($1.stats.removableNow.bytes, $1.stats.selected.bytes) }
    }
}
