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

    func run(_ args: [String]) throws -> Data {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: repo + "/.venv/bin/python")
        p.arguments = ["-m", "iphone_image"] + args
        p.currentDirectoryURL = URL(fileURLWithPath: repo)
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = repo + "/src"
        p.environment = env
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        try p.run()
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        // doctor exits non-zero when a check fails but still prints its report.
        guard !data.isEmpty else { throw JobError.message("iphone-image \(args.joined(separator: " ")) printed nothing (exit \(p.terminationStatus))") }
        return data
    }
}
