import Foundation

/// A CLI command the app started: an audit, a removal plan, a removal.
///
/// Each runs detached, writing into its own folder under `~/.iphone-image/ui/`, so it
/// survives the app quitting or crashing and the app picks it up again on launch. That is
/// what lets Activity show it as a job in flight and lets a removal's result be read back
/// after the fact, rather than living only in a window that has gone.
public struct CLITask: Identifiable, Codable, Hashable, Sendable {
    public enum Kind: String, Codable, Sendable { case audit, plan, remove, places, backup, settle, recover, refile }

    public let id: String
    public let kind: Kind
    /// Which recipe it serves, for plan and remove.
    public let recipe: String?
    public let title: String
    public let args: [String]
    public let startedAt: Date
    public let pid: Int32

}

public struct TaskStatus: Hashable, Sendable {
    public enum State: Hashable, Sendable { case running, finished(Int32), lost }
    public let task: CLITask
    public let state: State
    /// The command's JSON, once it has finished.
    public let output: Data?
    public let stderr: String

    public var running: Bool { state == .running }
    /// The CLI's own `{"error": "..."}`, when it refused or failed.
    public var errorMessage: String? {
        guard let output, let obj = try? JSONSerialization.jsonObject(with: output) as? [String: Any] else {
            if case .finished(let code) = state, code != 0 {
                return stderr.split(separator: "\n").last.map(String.init) ?? "exited \(code)"
            }
            if state == .lost { return "It stopped without finishing (the Mac may have restarted)." }
            return nil
        }
        return obj["error"] as? String
    }

    public func decode<T: Decodable>(_ type: T.Type) -> T? {
        guard let output, errorMessage == nil else { return nil }
        return try? JSONDecoder().decode(T.self, from: output)
    }
}

public enum TaskStore {
    nonisolated(unsafe) public static var root = Jobs.home + "/ui"

    /// Starts `iphone-image --json <args>` detached. Returns once it is running.
    public static func start(kind: CLITask.Kind, recipe: String?, title: String, args: [String],
                             repo: String = CLIEngine.defaultRepo) throws -> CLITask {
        try start(kind: kind, recipe: recipe, title: title, steps: [args], repo: repo)
    }

    /// Several commands in order, stopping at the first that fails. `out.json` holds the
    /// last one's JSON, or the failing one's, so the error shown is the step that stopped.
    public static func start(kind: CLITask.Kind, recipe: String?, title: String, steps: [[String]],
                             repo: String = CLIEngine.defaultRepo) throws -> CLITask {
        let args = steps.last ?? []
        let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "")
        let id = "\(stamp)-\(kind.rawValue)" + (recipe.map { "-\($0)" } ?? "")
        let dir = root + "/" + id
        try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)

        func quote(_ a: [String]) -> String { a.map { "'" + $0.replacingOccurrences(of: "'", with: "'\\''") + "'" }.joined(separator: " ") }
        let chain = steps.map { "PYTHONPATH=src ./.venv/bin/python -m iphone_image --json \(quote($0)) > '\(dir)/out.json' 2>> '\(dir)/err.log'" }
            .joined(separator: " && ")
        // The subshell is the task: alive while the commands run, and it writes the exit code
        // last, so `exit` existing means the output is complete.
        let script = """
        ( cd '\(repo)' && \(chain); echo $? > '\(dir)/exit' ) < /dev/null > /dev/null 2>&1 &
        echo $!
        """
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-c", script]
        let pipe = Pipe()
        p.standardOutput = pipe
        try p.run()
        let out = String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        p.waitUntilExit()
        guard let pid = Int32(out.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw JobError.message("could not start iphone-image \(args.first ?? "")")
        }
        let task = CLITask(id: id, kind: kind, recipe: recipe, title: title, args: args, startedAt: Date(), pid: pid)
        let meta = try JSONEncoder().encode(task)
        try meta.write(to: URL(fileURLWithPath: dir + "/task.json"))
        return task
    }

    /// Every task, newest first.
    public static func all() -> [TaskStatus] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: root)) ?? []
        return names.sorted(by: >).compactMap { status(of: $0) }
    }

    public static func status(of id: String) -> TaskStatus? {
        let dir = root + "/" + id
        guard let meta = FileManager.default.contents(atPath: dir + "/task.json"),
              let task = try? JSONDecoder().decode(CLITask.self, from: meta) else { return nil }
        let stderr = (try? String(contentsOfFile: dir + "/err.log", encoding: .utf8)) ?? ""
        if let exit = try? String(contentsOfFile: dir + "/exit", encoding: .utf8),
           let code = Int32(exit.trimmingCharacters(in: .whitespacesAndNewlines)) {
            return TaskStatus(task: task, state: .finished(code), output: FileManager.default.contents(atPath: dir + "/out.json"), stderr: stderr)
        }
        // kill(pid, 0) asks whether the process exists without signalling it.
        let alive = kill(task.pid, 0) == 0
        return TaskStatus(task: task, state: alive ? .running : .lost, output: nil, stderr: stderr)
    }
}

// MARK: The JSON the CLI prints

public struct AuditReport: Decodable, Hashable, Sendable {
    public struct Conflict: Decodable, Hashable, Sendable { public let kind: String; public let detail: String }
    public struct Entry: Decodable, Hashable, Sendable { public let id: Int; public let path: String }
    public let rowsExamined: Int
    public let conflicts: [Conflict]
    public let remoteFiles: Int?
    public let claimedVerified: Int?
    public let matched: Int?
    public let missing: [Entry]?
    public let hashMismatch: [Entry]?
    public let unhashed: Int?

    /// Clean means: the remote was asked, and every verified row is there with its hash.
    public var clean: Bool {
        matched != nil && (missing ?? []).isEmpty && (hashMismatch ?? []).isEmpty && conflicts.isEmpty
    }
}

public struct SettleReport: Decodable, Hashable, Sendable {
    public let confirmedGone: Int
    public let bytesGone: Int64
    public let stillOnPhone: Int
    public let unknown: Int
}

public struct RecoverReport: Decodable, Hashable, Sendable {
    public struct File: Decodable, Hashable, Sendable { public let id: Int; public let filename: String?; public let path: String; public let verified: Bool }
    public let folder: String
    public let verified: Int
    public let files: [File]
}

public struct PlacesReport: Decodable, Hashable, Sendable {
    public let assets: Int
    public let located: Int
    public let places: Int
    public let degraded: String?
}

public struct RemovalPlan: Decodable, Hashable, Sendable {
    public struct Blocked: Decodable, Hashable, Sendable { public let id: Int; public let filename: String?; public let reason: String }
    public let applied: Bool
    public let scanId: Int?
    public let examined: Int
    public let eligible: Int
    public let eligibleBytes: Int64
    public let blocked: Int
    public let blockedReasons: [String: Int]
    public let blockedAssets: [Blocked]
    // Present after --apply.
    public let removed: Int?
    public let alreadyGone: Int?
    public let failed: Int?
    public let bytesRemoved: Int64?

    /// The phrase `remove-from-iphone --apply` requires: `_removal_phrase` in cli/main.py.
    /// Tied to the count, so an older plan cannot authorise a larger removal.
    public var phrase: String { RemovalPlan.phrase(eligible) }
    public static func phrase(_ count: Int) -> String { "remove \(count) photos from my iphone" }

    /// The count the CLI asks for when the plan changed between plan and apply.
    public static func expectedCount(inError message: String) -> Int? {
        guard let r = message.range(of: "--confirm \"remove ") else { return nil }
        return Int(message[r.upperBound...].prefix { $0.isNumber })
    }
}

// MARK: Proof that what was removed is in Google Drive

/// One removed item, with the evidence recorded at the moment of removal.
public struct ProofRow: Identifiable, Hashable, Sendable {
    public let id: Int
    public var assetId = 0
    public let filename: String
    public let drivePath: String
    public let sha256: String
    public let bytes: Int64
    public let removedAt: String
    /// The removal re-read this hash from Google Drive in the same run and it matched.
    public let driveHashConfirmed: Bool
    /// The asset is still recorded as verified in Drive now.
    public let stillVerified: Bool
}

public struct Proof: Hashable, Sendable {
    public var rows: [ProofRow] = []
    public var total = 0
    public var confirmed = 0
    public var stillVerified = 0
    public var bytes: Int64 = 0
    public var allConfirmed: Bool { total > 0 && confirmed == total && stillVerified == total }
}

extension Ledger {
    /// A date in the ledger's own timestamp form, `2026-09-17T10:04:03+00:00`, so string
    /// comparison against its columns is exact.
    public static func stamp(_ d: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'+00:00'"
        return f.string(from: d)
    }

    /// Removals completed since `since` (ISO), with their Drive evidence. `limit` caps the
    /// rows returned; the counts cover every removal in the window.
    public static func proof(_ db: SQLiteReader, since: String, limit: Int = 500) throws -> Proof {
        var p = Proof()
        let sums = try db.rows("""
            SELECT COUNT(*), SUM(json_extract(d.evidence, '$.remote_hash_confirmed') = 1),
                   SUM(a.cloud_status = 'CLOUD_VERIFIED'), COALESCE(SUM(d.size_bytes), 0)
            FROM deletion_events d JOIN assets a ON a.id = d.asset_id
            WHERE d.status = 'REMOVED' AND d.completed_at >= ?
            """, [since])[0]
        p.total = sums.int(0)
        p.confirmed = sums.int(1)
        p.stillVerified = sums.int(2)
        p.bytes = Int64(sums.int(3))
        p.rows = try removed(db, since: since, search: "", limit: limit)
        return p
    }

    /// Removed items, newest first, optionally matching a filename fragment.
    public static func removed(_ db: SQLiteReader, since: String = "", search: String, limit: Int = 500) throws -> [ProofRow] {
        try db.rows("""
            SELECT d.id, COALESCE(a.filename, ''), COALESCE(a.cloud_path, ''), COALESCE(a.sha256, ''),
                   COALESCE(d.size_bytes, 0), d.completed_at,
                   json_extract(d.evidence, '$.remote_hash_confirmed') = 1, a.cloud_status = 'CLOUD_VERIFIED', a.id
            FROM deletion_events d JOIN assets a ON a.id = d.asset_id
            WHERE d.status = 'REMOVED' AND d.completed_at >= ? AND COALESCE(a.filename, '') LIKE ?
            ORDER BY d.completed_at DESC LIMIT ?
            """, [since, "%\(search)%", limit]).map {
            var r = ProofRow(id: $0.int(0), filename: $0.str(1), drivePath: $0.str(2), sha256: $0.str(3), bytes: Int64($0.int(4)),
                             removedAt: $0.str(5), driveHashConfirmed: $0.int(6) == 1, stillVerified: $0.int(7) == 1)
            r.assetId = $0.int(8)
            return r
        }
    }
}
