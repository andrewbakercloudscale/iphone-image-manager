import Foundation

/// One `doctor` check, as `iphone-image --json doctor` reports it.
public struct Check: Codable, Identifiable, Hashable, Sendable {
    public let name: String
    public let status: String
    public let detail: String
    public let remedy: String
    public var id: String { name }
    public var passed: Bool { status == "PASS" }
    public var failed: Bool { status == "FAIL" }
}

public enum DoctorProgress: Sendable {
    case plan([String])
    case started(String)
    case finished(String, String)
}

/// Splits a pipe's output into lines as it arrives.
final class LineBuffer: @unchecked Sendable {
    private var pending = Data()
    private let lock = NSLock()
    private let emit: @Sendable (String) -> Void
    init(_ emit: @escaping @Sendable (String) -> Void) { self.emit = emit }

    func feed(_ data: Data) {
        lock.lock()
        pending.append(data)
        var lines: [String] = []
        while let i = pending.firstIndex(of: 0x0A) {
            lines.append(String(decoding: pending[pending.startIndex..<i], as: UTF8.self))
            pending.removeSubrange(pending.startIndex...i)
        }
        lock.unlock()
        lines.forEach(emit)
    }
}

public struct DoctorReport: Codable, Sendable {
    public let checks: Int
    public let passed: Int
    public let warnings: Int
    public let failures: Int
    public let ready: Bool
    public let results: [Check]
}

/// What the app asks of whichever engine does the work. Phase A has one implementation,
/// `CLIEngine`, over the Python tool; Phase C adds a native one behind the same protocol.
public protocol Engine: Sendable {
    func library() throws -> LibrarySnapshot
    func doctor() throws -> DoctorReport
    func jobs() -> [Job]
}

public struct CLIEngine: Engine {
    public var repo: String
    public var ledgerPath: String
    public var recipes: [Recipe]

    public static let defaultRepo = NSString(string: "~/Desktop/github/iphone-image-manager").expandingTildeInPath

    public init(repo: String = CLIEngine.defaultRepo, ledgerPath: String = Ledger.defaultPath, recipes: [Recipe] = Recipe.defaults) {
        self.repo = repo
        self.ledgerPath = ledgerPath
        self.recipes = recipes
    }

    public func library() throws -> LibrarySnapshot {
        try Ledger.read(SQLiteReader(path: ledgerPath), recipes: recipes)
    }

    public func jobs() -> [Job] {
        Jobs.read(log: Jobs.tail(), processes: Jobs.processes())
    }

    /// `--skip-slow` keeps it to seconds; the slow checks copy the Photos database.
    public func doctor() throws -> DoctorReport {
        let data = try run(["--json", "doctor", "--skip-slow"])
        return try JSONDecoder().decode(DoctorReport.self, from: data)
    }

    /// The same, reporting each check as it starts and ends (`doctor --progress`), so the
    /// app can say what it is doing during the minute this takes.
    public func doctor(progress: @escaping @Sendable (DoctorProgress) -> Void) throws -> DoctorReport {
        let data = try run(["--json", "doctor", "--skip-slow", "--progress"]) { line in
            guard let obj = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else { return }
            if let plan = obj["plan"] as? [String] { progress(.plan(plan)) }
            if let key = obj["start"] as? String { progress(.started(key)) }
            if let key = obj["done"] as? String { progress(.finished(key, obj["status"] as? String ?? "")) }
        }
        return try JSONDecoder().decode(DoctorReport.self, from: data)
    }

    func run(_ args: [String], stderrLine: (@Sendable (String) -> Void)? = nil) throws -> Data {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: repo + "/.venv/bin/python")
        p.arguments = ["-m", "iphone_image"] + args
        p.currentDirectoryURL = URL(fileURLWithPath: repo)
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = repo + "/src"
        p.environment = env
        let out = Pipe()
        p.standardOutput = out
        if let stderrLine {
            let err = Pipe()
            p.standardError = err
            let buffer = LineBuffer(stderrLine)
            err.fileHandleForReading.readabilityHandler = { h in buffer.feed(h.availableData) }
        } else {
            p.standardError = FileHandle.nullDevice
        }
        try p.run()
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        // doctor exits non-zero when a check fails but still prints its report.
        guard !data.isEmpty else { throw JobError.message("iphone-image \(args.joined(separator: " ")) printed nothing (exit \(p.terminationStatus))") }
        return data
    }
}

/// `config layout`: the archive's folder layout, a preview of it, and saving it.
public struct LayoutReport: Codable, Sendable {
    public struct Settings: Codable, Hashable, Sendable {
        public var pattern: String
        public var minPhotos: Int
        public var gapDays: Int
        public var flat: [String]
    }
    public struct Folder: Codable, Hashable, Sendable {
        public let folder: String
        public let count: Int
    }
    public struct Preview: Codable, Sendable {
        public let assets: Int
        public let folders: Int
        public let placeFolders: Int
        public let biggest: [Folder]
        public let places: [Folder]
        public let smallest: Int
    }
    public let current: Settings
    public let proposed: Settings
    public let changed: Bool
    public let applied: Bool
    public let preview: Preview?
    public let error: String?
}

extension CLIEngine {
    /// Without `apply` nothing is written; `preview` reads a Photos database copy (seconds).
    public func layout(_ s: LayoutReport.Settings? = nil, preview: Bool = false, apply: Bool = false) throws -> LayoutReport {
        var args = ["--json", "config", "layout"]
        if let s {
            args += ["--pattern", s.pattern, "--min-photos", String(s.minPhotos), "--gap-days", String(s.gapDays),
                     "--flat", s.flat.joined(separator: ",")]
        }
        if preview { args.append("--preview") }
        if apply { args.append("--apply") }
        let data = try run(args)
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let e = obj["error"] as? String {
            throw JobError.message(e)
        }
        return try JSONDecoder().decode(LayoutReport.self, from: data)
    }
}
