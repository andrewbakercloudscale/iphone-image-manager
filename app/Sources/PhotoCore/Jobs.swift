import Foundation

/// A background backup job, as `cycle.sh <type> <cycles> <source> [older]` runs it today.
///
/// Phase A has no job table of its own: state comes from `~/.iphone-image/cycle.log`
/// (every line `[MM-dd HH:mm:ss] [source/type] message`) and from whether a matching
/// process is alive. The log line wording is cycle.sh's; `JobsTests` pins each one.
public struct Job: Identifiable, Hashable, Sendable {
    public enum State: Hashable, Sendable {
        case running
        case paused(String)
        case stopped(String)
        case finished
    }

    public let source: String
    public let type: String
    public var state: State
    public var lastLine: String
    public var lastAt: String
    public var leftToFetch: Int?
    public var startedLeft: Int?
    /// Items fetched so far in the chunk under way, from the CLI's `  [3,931] name` lines.
    /// `left_to_fetch` only moves once per cycle; this moves every few seconds.
    public var fetchedThisChunk = 0
    public var left: Int? { leftToFetch.map { max(0, $0 - fetchedThisChunk) } }

    /// What a cycle is doing now. Each cycle copies a chunk out of Photos, uploads it to
    /// Drive and checks it, then tidies the Mac copies; one bar for the whole job read 100%
    /// as soon as the last chunk was copied, while hours of uploading were still to come.
    public enum Phase: Hashable, Sendable { case fetching, uploading, releasing, other }
    public var phase = Phase.other
    /// rclone's `(xfr#1585/11602)` while uploading: files sent, and files it has found to send.
    public var uploaded: Int?
    public var toUpload: Int?
    /// The first and latest rclone stats line of this upload, as (time, files sent), for its pace.
    public struct Sample: Hashable, Sendable { public let at: Date; public let sent: Int }
    public var uploadFirst: Sample?
    public var uploadLast: Sample?

    /// Files a minute over this upload so far, once it has run long enough to say.
    public var uploadRate: Double? {
        guard let a = uploadFirst, let b = uploadLast, b.sent > a.sent else { return nil }
        let minutes = b.at.timeIntervalSince(a.at) / 60
        return minutes >= 2 ? Double(b.sent - a.sent) / minutes : nil
    }
    public var id: String { "\(source)/\(type)" }

    public var title: String {
        let what = type == "video" ? "videos" : (source == "screenshot" ? "" : "photos")
        let name: String
        switch source {
        case "camera": name = "Camera"
        case "whatsapp": name = "WhatsApp"
        case "screenshot": name = "Screenshots"
        case "screenrecording": name = "Screen recordings"
        default: name = source.capitalized
        }
        return what.isEmpty ? name : "\(name) \(what)"
    }

    /// The age cycle.sh was given, which its log does not record. These are the
    /// owner's choices so far (HANDOVER.md); a new source starts with no age.
    public static var ages: [String: String] {
        var a = ["screenshot": "1y", "whatsapp": "18m"]
        // Ages of the jobs this app started, so Resume repeats the same selection.
        if let d = FileManager.default.contents(atPath: Jobs.agesPath),
           let saved = try? JSONDecoder().decode([String: String].self, from: d) { a.merge(saved) { $1 } }
        return a
    }

    /// The command that resumes this job from the ledger, the same one HANDOVER.md gives.
    public var resumeArguments: [String] {
        var a = [type, "40", source]
        if let age = Self.ages[source] { a.append(age) }
        return a
    }

    /// How far the current step is, for its bar: the copy out of Photos across the whole
    /// job, or the upload of the chunk under way. Nil when the step has no count.
    public var progress: Double? {
        switch phase {
        case .uploading:
            guard let uploaded, let toUpload, toUpload > 0 else { return nil }
            return min(1, Double(uploaded) / Double(toUpload))
        case .fetching:
            guard let left, let start = startedLeft, start > 0 else { return nil }
            return 1 - Double(left) / Double(start)
        case .releasing, .other: return nil
        }
    }

    /// The step in words: "uploading to Drive, 1,585 of 11,602 files".
    public var step: String? {
        let n = Fmt.count
        switch phase {
        case .uploading:
            guard let uploaded, let toUpload else { return "uploading to Google Drive" }
            var text = "uploading to Google Drive, \(n(uploaded)) of \(n(toUpload)) files"
            if let rate = uploadRate {
                text += ", about \(n(Int(rate.rounded()))) a minute"
                let hours = Double(max(0, toUpload - uploaded)) / rate / 60
                text += hours < 1 ? ", under an hour to go" : ", about \(Int(hours.rounded())) h to go"
            }
            return text
        case .fetching:
            guard let left else { return "copying from Photos" }
            return left == 0 ? "copying the last items from Photos" : "copying from Photos, \(n(left)) left"
        case .releasing: return "tidying the Mac copies already in Google Drive"
        case .other: return nil
        }
    }
}

public enum Jobs {
    public static let home = NSString(string: "~/.iphone-image").expandingTildeInPath
    public static var logPath: String { home + "/cycle.log" }
    public static var cyclePath: String { home + "/cycle.sh" }

    /// Every job the log mentions, newest activity first, with whether it is alive.
    public static func read(log: String, processes: [String]) -> [Job] {
        var jobs: [String: Job] = [:]
        var order: [String] = []
        var current: String?
        for raw in log.split(separator: "\n", omittingEmptySubsequences: true) {
            if let key = current, let n = chunkCount(raw) {
                jobs[key]?.fetchedThisChunk = n
                continue
            }
            if let key = current, let (sent, total) = transfers(raw) {
                jobs[key]?.uploaded = sent
                jobs[key]?.toUpload = total
                if let at = statsTime(raw) {
                    if jobs[key]?.uploadFirst == nil { jobs[key]?.uploadFirst = Job.Sample(at: at, sent: sent) }
                    jobs[key]?.uploadLast = Job.Sample(at: at, sent: sent)
                }
                continue
            }
            guard let parsed = parse(String(raw)) else { continue }
            current = parsed.key
            let key = parsed.key
            let parts = key.split(separator: "/").map(String.init)
            guard parts.count == 2 else { continue }
            var job = jobs[key] ?? Job(source: parts[0], type: parts[1], state: .finished, lastLine: "", lastAt: "")
            let m = parsed.message
            job.lastLine = m
            job.lastAt = parsed.time
            if m.hasPrefix("starting:"), let n = number(after: "cycle(s), ", in: m) {
                job.startedLeft = n
                job.leftToFetch = n
            }
            if m.hasPrefix("=== cycle"), let n = number(after: "GB free, ", in: m) { job.leftToFetch = n }
            if m.hasPrefix("=== cycle") || m.hasPrefix("starting:") || m.hasPrefix("fetching the next chunk") { job.fetchedThisChunk = 0 }
            if m.contains("nothing left to fetch") { job.leftToFetch = 0 }
            if m.hasPrefix("fetching the next chunk") { job.phase = .fetching }
            if m.hasPrefix("uploading what landed") { job.phase = .uploading; job.uploaded = nil; job.toUpload = nil; job.uploadFirst = nil; job.uploadLast = nil }
            if m.hasPrefix("releasing") { job.phase = .releasing }
            if m.hasPrefix("=== cycle") || m.hasPrefix("starting:") { job.phase = .other }
            job.state = state(after: m, previous: job.state)
            if jobs[key] == nil { order.append(key) }
            jobs[key] = job
        }
        for key in order {
            guard var job = jobs[key] else { continue }
            let alive = processes.contains { isCycle($0, type: job.type, source: job.source) }
            if alive {
                if case .paused = job.state {} else { job.state = .running }
            } else if job.state == .running {
                job.state = .stopped("The job is no longer running (the Mac may have restarted or slept). Last: \(job.lastLine)")
            } else if case .paused = job.state {
                job.state = .stopped("Stopped while paused. Last: \(job.lastLine)")
            }
            jobs[key] = job
        }
        return order.compactMap { jobs[$0] }.sorted { $0.lastAt > $1.lastAt }
    }

    /// What a log message means for the job's state.
    static func state(after m: String, previous: Job.State) -> Job.State {
        if m.contains("nothing left to fetch") || m.hasPrefix("cycle finished") && m.hasSuffix(" 0 still to fetch") { return .finished }
        if m.contains("STOPPING") || m.contains("EMPTY THE BIN") || m.hasPrefix("release failed") {
            return .stopped(plain(m))
        }
        if m.contains("metered connection") { return .paused("On your iPhone's hotspot. Carries on by itself on normal wifi.") }
        if m.hasPrefix("network is down") { return .paused("No network. Waiting up to 3 hours.") }
        if m.contains("Google Drive quota/rate limit") { return .paused("Google Drive asked it to slow down. Retrying.") }
        return .running
    }

    /// cycle.sh's stop messages in words the owner reads at a glance.
    static func plain(_ m: String) -> String {
        if m.hasPrefix("no progress") { return "No progress for 2 cycles: Photos could not download items from iCloud. Usually a network blip; resuming normally works." }
        if m.contains("network still down") { return "The network was down for 3 hours." }
        if m.contains("Google Drive still over quota") { return "Google Drive refused uploads for 3 hours (quota)." }
        if m.contains("EMPTY THE BIN") { return "Not enough disk space: empty the Bin or free some space, then resume." }
        if m.hasPrefix("release failed") { return "Tidying the Mac copies failed. Read cycle.log before resuming." }
        return m
    }

    static func parse(_ line: String) -> (time: String, key: String, message: String)? {
        // [10-25 17:40:44] [whatsapp/photo] message
        guard line.hasPrefix("["), let close = line.firstIndex(of: "]") else { return nil }
        let time = String(line[line.index(after: line.startIndex)..<close])
        let rest = line[line.index(after: close)...].drop { $0 == " " }
        guard rest.hasPrefix("["), let close2 = rest.firstIndex(of: "]") else { return nil }
        let key = String(rest[rest.index(after: rest.startIndex)..<close2])
        guard key.contains("/") else { return nil }
        let message = rest[rest.index(after: close2)...].trimmingCharacters(in: .whitespaces)
        return (time, key, message)
    }

    /// `  [3,931] 7cf5a498-...  137.3 KB  0.15 MB/s` to 3931.
    static func chunkCount(_ line: Substring) -> Int? {
        let t = line.drop { $0 == " " }
        guard t.count < line.count, t.hasPrefix("["), let close = t.firstIndex(of: "]") else { return nil }
        return Int(t[t.index(after: t.startIndex)..<close].filter { $0 != "," && $0 != " " })
    }

    /// rclone's `NOTICE:   450 MiB / 1.993 GiB, 22%, 47 KiB/s, ETA 9h (xfr#1585/11602)` to (1585, 11602).
    static func transfers(_ line: Substring) -> (Int, Int)? {
        guard let r = line.range(of: "(xfr#") else { return nil }
        let parts = line[r.upperBound...].prefix { $0 != ")" }.split(separator: "/")
        guard parts.count == 2, let a = Int(parts[0]), let b = Int(parts[1]) else { return nil }
        return (a, b)
    }

    /// `  2026/10/26 09:57:22 NOTICE: ...` to its time, in this Mac's zone as rclone writes it.
    static func statsTime(_ line: Substring) -> Date? {
        let t = line.drop { $0 == " " }.prefix(19)
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy/MM/dd HH:mm:ss"
        return f.date(from: String(t))
    }

    static func number(after marker: String, in s: String) -> Int? {
        guard let r = s.range(of: marker) else { return nil }
        return Int(s[r.upperBound...].prefix { $0.isNumber })
    }

    /// `/bin/bash /Users/x/.iphone-image/cycle.sh photo 40 whatsapp 18m`, not the caffeinate
    /// wrapper (which names it too, so either matches) and not a grep.
    static func isCycle(_ command: String, type: String, source: String) -> Bool {
        let words = command.split(separator: " ").map(String.init)
        guard let i = words.firstIndex(where: { $0.hasSuffix("/cycle.sh") || $0 == "cycle.sh" }),
              words.count > i + 3 else { return false }
        return words[i + 1] == type && words[i + 3] == source
    }

    /// Command lines of every process, from `ps`.
    public static func processes() -> [String] {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/ps")
        p.arguments = ["-axo", "command="]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return [] }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return String(decoding: data, as: UTF8.self).split(separator: "\n").map(String.init)
    }

    /// The last `bytes` of the log: enough for every job's recent history without reading
    /// a log that grows by megabytes a day.
    public static func tail(_ path: String = logPath, bytes: Int = 4_000_000) -> String {
        guard let h = FileHandle(forReadingAtPath: path) else { return "" }
        defer { try? h.close() }
        let size = (try? h.seekToEnd()) ?? 0
        try? h.seek(toOffset: size > UInt64(bytes) ? size - UInt64(bytes) : 0)
        return String(decoding: h.readDataToEndOfFile(), as: UTF8.self)
    }

    /// Resumes a backup job exactly as HANDOVER.md does: detached, under caffeinate, so it
    /// outlives this app. Backup only: cycle.sh never removes anything from the phone.
    /// Refuses when any cycle.sh is already running (one PhotoKit exporter at a time).
    public static func resume(_ job: Job, processes: [String]) -> Result<Void, JobError> {
        start(job.resumeArguments, source: job.source, processes: processes)
    }

    static var agesPath: String { home + "/ui/ages.json" }

    public static func anyRunning(_ processes: [String]) -> Bool {
        processes.contains(where: { $0.contains("/cycle.sh ") && !$0.hasPrefix("caffeinate") })
    }

    /// Starts a new backup job: `cycle.sh <type> 40 <source> [older]`. Same rules as resume.
    public static func start(type: String, source: String, older: String?, processes: [String]) -> Result<Void, JobError> {
        if let older {
            var saved = (FileManager.default.contents(atPath: agesPath)).flatMap { try? JSONDecoder().decode([String: String].self, from: $0) } ?? [:]
            saved[source] = older
            try? FileManager.default.createDirectory(atPath: home + "/ui", withIntermediateDirectories: true)
            try? JSONEncoder().encode(saved).write(to: URL(fileURLWithPath: agesPath))
        }
        return start([type, "40", source] + (older.map { [$0] } ?? []), source: source, processes: processes)
    }

    static func start(_ arguments: [String], source: String, processes: [String]) -> Result<Void, JobError> {
        if anyRunning(processes) {
            return .failure(.message("Another job is running. Only one runs at a time, so this one waits until it ends."))
        }
        guard FileManager.default.isExecutableFile(atPath: cyclePath) else {
            return .failure(.message("cycle.sh is not at \(cyclePath)."))
        }
        let out = home + "/cycle-\(source).out"
        let args = arguments.map { "'\($0)'" }.joined(separator: " ")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-c", "cd ~ && nohup caffeinate -dimsu '\(cyclePath)' \(args) > '\(out)' 2>&1 & disown"]
        do {
            try p.run()
            p.waitUntilExit()
            return .success(())
        } catch {
            return .failure(.message(error.localizedDescription))
        }
    }
}

public enum JobError: Error, Equatable {
    case message(String)
    public var text: String { if case .message(let m) = self { return m }; return "" }
}
