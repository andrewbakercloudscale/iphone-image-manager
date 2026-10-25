import Foundation
@testable import PhotoCore
import XCTest

final class AgeTests: XCTestCase {
    func testUnitsMatchRetentionPy() {
        XCTAssertEqual(Age("18m")?.days, 540)
        XCTAssertEqual(Age("1y")?.days, 365)
        XCTAssertEqual(Age("12w")?.days, 84)
        XCTAssertEqual(Age("60")?.days, 60, "a bare number is days, as in retention.py")
        XCTAssertNil(Age("soon"))
        XCTAssertEqual(Age("18m")?.description, "18m")
        XCTAssertEqual(Age(1, "y").words, "1 year")
    }

    func testCutoffIsComparableWithLedgerTimestamps() {
        let now = ISO8601DateFormatter().date(from: "2026-10-25T00:00:00Z")!
        XCTAssertEqual(Age(1, "y").cutoff(now: now), "2025-10-25T00:00:00Z")
    }
}

final class LedgerTests: XCTestCase {
    /// The columns Ledger.read touches, from migrations 0001 to 0004.
    func makeDB() throws -> SQLiteReader {
        let db = SQLiteReader()
        try db.exec("""
        CREATE TABLE assets (id INTEGER PRIMARY KEY, filename TEXT, media_type TEXT, subtypes TEXT,
          source_bundle_id TEXT, created_at_device TEXT, size_bytes INTEGER, is_favourite INTEGER DEFAULT 0,
          proxy_suspicion REAL DEFAULT 0, cloud_status TEXT DEFAULT 'NONE', present_on_phone INTEGER DEFAULT 1,
          sha256 TEXT, cloud_path TEXT);
        CREATE TABLE deletion_events (id INTEGER PRIMARY KEY, asset_id INTEGER, status TEXT, completed_at TEXT,
          size_bytes INTEGER, evidence TEXT DEFAULT '{}');
        CREATE TABLE operations (id INTEGER PRIMARY KEY, operation TEXT, status TEXT, command TEXT, started_at TEXT, finished_at TEXT);
        """)
        return db
    }

    func insert(_ db: SQLiteReader, _ id: Int, file: String, type: String = "PHOTO", subtypes: String = "[]",
                bundle: String? = nil, at: String = "2020-01-01T00:00:00Z", bytes: Int = 1000,
                fav: Int = 0, proxy: Double = 0, cloud: String = "NONE", present: Int = 1, sha: String? = "abc") throws {
        let b = bundle.map { "'\($0)'" } ?? "NULL"
        let h = sha.map { "'\($0)'" } ?? "NULL"
        try db.exec("""
        INSERT INTO assets VALUES (\(id), '\(file)', '\(type)', '\(subtypes)', \(b), '\(at)', \(bytes), \(fav), \(proxy), '\(cloud)', \(present),
          \(h), 'Drive/\(file)');
        """)
    }

    /// The channel rule must agree with selector.py's channel_of, case by case.
    func testChannelRuleMatchesThePythonSelector() throws {
        let db = try makeDB()
        try insert(db, 1, file: "IMG_0001.HEIC", bundle: "com.apple.camera")
        try insert(db, 2, file: "IMG_0002.HEIC")                                   // old camera, no bundle
        try insert(db, 3, file: "img_0003.jpg")                                    // LIKE is case-insensitive
        try insert(db, 4, file: "IMG_0004.PNG", subtypes: "[\"screenshot\"]")      // a screenshot is not camera
        try insert(db, 5, file: "x.jpg", bundle: "net.whatsapp.WhatsApp")
        try insert(db, 6, file: "IMG_0006.PNG", bundle: "com.apple.springboard")
        try insert(db, 7, file: "RPReplay.mp4", type: "VIDEO", bundle: "com.apple.replayd")
        try insert(db, 8, file: "a.jpg", bundle: "com.burbn.instagram")
        try insert(db, 9, file: "a.jpg")
        let rows = try db.rows("SELECT a.id, \(Channel.sql) FROM assets a ORDER BY a.id").map { $0.str(1) }
        XCTAssertEqual(rows, ["camera", "camera", "camera", "unattributed", "whatsapp", "screenshot",
                              "screenRecording", "otherApps", "unattributed"])
    }

    func testRecipesCountOnlyWhatIsSafeToRemove() throws {
        let db = try makeDB()
        let old = "2020-01-01T00:00:00Z", new = "2026-10-01T00:00:00Z"
        try insert(db, 1, file: "a", bundle: "net.whatsapp.WhatsApp", at: old, bytes: 100, cloud: "CLOUD_VERIFIED")
        try insert(db, 2, file: "b", bundle: "net.whatsapp.WhatsApp", at: old, bytes: 200)                       // not backed up
        try insert(db, 3, file: "c", bundle: "net.whatsapp.WhatsApp", at: old, bytes: 300, fav: 1, cloud: "CLOUD_VERIFIED")
        try insert(db, 4, file: "d", bundle: "net.whatsapp.WhatsApp", at: old, bytes: 400, proxy: 0.9, cloud: "CLOUD_VERIFIED")
        try insert(db, 5, file: "e", bundle: "net.whatsapp.WhatsApp", at: new, bytes: 500, cloud: "CLOUD_VERIFIED") // too new
        try insert(db, 6, file: "f", bundle: "net.whatsapp.WhatsApp", at: old, bytes: 600, cloud: "CLOUD_VERIFIED", present: 0) // gone
        // remove.py blocks any suspicion above zero, and anything with no hash to compare.
        try insert(db, 7, file: "g", bundle: "net.whatsapp.WhatsApp", at: old, bytes: 10, proxy: 0.1, cloud: "CLOUD_VERIFIED")
        try insert(db, 8, file: "h", bundle: "net.whatsapp.WhatsApp", at: old, bytes: 20, cloud: "CLOUD_VERIFIED", sha: nil)
        let now = ISO8601DateFormatter().date(from: "2026-10-25T00:00:00Z")!
        let s = try Ledger.read(db, recipes: [Recipe(kind: .whatsapp, age: Age(18, "m"))], now: now)
        let r = try XCTUnwrap(s.recipes.first)
        XCTAssertEqual(r.selected, Tally(count: 6, bytes: 1030))
        XCTAssertEqual(r.removableNow, Tally(count: 1, bytes: 100), "favourites, proxies and unhashed items are never removable")
        XCTAssertEqual(r.notBackedUp, Tally(count: 1, bytes: 200))
        XCTAssertEqual(r.kept, Tally(count: 4, bytes: 730), "only items in Drive count as kept; the rest are not backed up")
        XCTAssertEqual(s.onPhone, Tally(count: 7, bytes: 1530))
        XCTAssertEqual(s.proxies, 2)
    }

    func testProofCountsOnlyDriveConfirmedRemovals() throws {
        let db = try makeDB()
        try insert(db, 1, file: "a", cloud: "CLOUD_VERIFIED")
        try insert(db, 2, file: "b", cloud: "CLOUD_VERIFIED")
        try insert(db, 3, file: "c")
        try db.exec("""
        INSERT INTO deletion_events (asset_id, status, completed_at, size_bytes, evidence) VALUES
          (1, 'REMOVED', '2026-10-25T10:00:00+00:00', 5, '{"remote_hash_confirmed": true}'),
          (2, 'REMOVED', '2026-10-25T10:00:01+00:00', 5, '{}'),
          (3, 'REMOVED', '2026-10-25T10:00:02+00:00', 5, '{"remote_hash_confirmed": true}'),
          (1, 'REMOVED', '2026-10-24T10:00:00+00:00', 5, '{"remote_hash_confirmed": true}'),
          (2, 'REQUESTED', '2026-10-25T11:00:00+00:00', 5, '{}');
        """)
        let since = Ledger.stamp(ISO8601DateFormatter().date(from: "2026-10-25T09:00:00Z")!)
        XCTAssertEqual(since, "2026-10-25T09:00:00+00:00")
        let p = try Ledger.proof(db, since: since)
        XCTAssertEqual(p.total, 3)
        XCTAssertEqual(p.confirmed, 2)
        XCTAssertEqual(p.stillVerified, 2)
        XCTAssertFalse(p.allConfirmed)
        XCTAssertEqual(p.rows.first?.drivePath, "Drive/c")
    }
}

final class TaskTests: XCTestCase {
    func testRemovalPlanDecodesTheCLIJSON() throws {
        let json = """
        {"applied": false, "scanId": 31, "examined": 10795, "eligible": 8451, "eligibleBytes": 12972961501,
         "blocked": 2344, "blockedReasons": {"flagged as a suspected proxy": 2226},
         "blockedAssets": [{"id": 4, "filename": "IMG_1.PNG", "reason": "flagged as a suspected proxy"}]}
        """
        let p = try JSONDecoder().decode(RemovalPlan.self, from: Data(json.utf8))
        XCTAssertEqual(p.phrase, "remove 8451 photos from my iphone")
        XCTAssertNil(p.removed)
    }

    func testTheNewCountIsReadFromARefusal() {
        let msg = "--apply needs the confirmation phrase for this exact plan. Re-run with:\n    --confirm \"remove 8460 photos from my iphone\"\n"
        XCTAssertEqual(RemovalPlan.expectedCount(inError: msg), 8460)
        XCTAssertNil(RemovalPlan.expectedCount(inError: "nothing is safe to remove"))
    }

    func testAuditIsCleanOnlyWhenTheRemoteWasAsked() throws {
        let offline = try JSONDecoder().decode(AuditReport.self, from: Data(#"{"rowsExamined": 5, "conflicts": []}"#.utf8))
        XCTAssertFalse(offline.clean, "an audit that never asked Drive proves nothing")
        let clean = try JSONDecoder().decode(AuditReport.self, from: Data(#"{"rowsExamined": 5, "conflicts": [], "matched": 5, "missing": [], "hashMismatch": []}"#.utf8))
        XCTAssertTrue(clean.clean)
    }

    /// The real CLI, detached, read back from disk: what the app does for audit and plan.
    func testATaskRunsDetachedAndIsReadBack() throws {
        let saved = TaskStore.root
        TaskStore.root = NSTemporaryDirectory() + "tasks-\(UUID().uuidString)"
        defer { TaskStore.root = saved }
        let t = try TaskStore.start(kind: .audit, recipe: nil, title: "t", args: ["audit", "--offline"])
        var s = TaskStore.status(of: t.id)
        for _ in 0..<300 where s?.running == true {
            Thread.sleep(forTimeInterval: 0.1)
            s = TaskStore.status(of: t.id)
        }
        let done = try XCTUnwrap(s)
        XCTAssertEqual(done.state, .finished(0), done.stderr)
        let report = try XCTUnwrap(done.decode(AuditReport.self))
        XCTAssertGreaterThan(report.rowsExamined, 0)
        XCTAssertFalse(report.clean, "--offline never asks Drive, so it must not read as clean")
        XCTAssertEqual(TaskStore.all().map(\.task.id), [t.id])
    }

    func testSelectorArgsMatchTheCLI() {
        XCTAssertEqual(Recipe(kind: .whatsapp, age: Age(18, "m")).selectorArgs, ["--source", "whatsapp", "--older-than", "18m"])
        XCTAssertEqual(Recipe(kind: .largeVideos, minBytes: 500 * Filter.mb).selectorArgs, ["--type", "video", "--min-size", "500MB"])
        XCTAssertEqual(Recipe(kind: .whatsapp, age: Age(18, "m")).backups.map(\.type), ["photo", "video"])
    }
}

final class JobsTests: XCTestCase {
    let log = """
    [09-30 02:45:34] [screenshot/photo] nothing left to fetch. done.
    [09-30 02:45:35] [screenshot/photo] cycle finished: 17.6 GB free, 0 still to fetch
    [09-30 02:45:48] [whatsapp/photo] starting: 40 cycle(s), 32254 left to fetch, 17.6 GB free, a chunk needs 12.9 GB, release=1, empty_bin=1
    [09-30 15:23:50] [whatsapp/photo] === cycle 8/40: 17.9 GB free, 18800 left to fetch ===
    [09-30 15:23:50] [whatsapp/photo] no progress: 18800 left to fetch, unchanged for 2 cycles. STOPPING. See the sync errors above.
    [09-30 15:23:50] [chain] the WhatsApp photo cycle did not finish cleanly, so WhatsApp videos are NOT starting
      WARNING: an indented line from the CLI, ignored
    """

    func testReadsStateAndProgressFromTheLog() {
        let jobs = Jobs.read(log: log, processes: [])
        XCTAssertEqual(jobs.map(\.id), ["whatsapp/photo", "screenshot/photo"])
        let w = jobs[0]
        guard case .stopped(let why) = w.state else { return XCTFail("expected stopped, got \(w.state)") }
        XCTAssertTrue(why.contains("iCloud"))
        XCTAssertEqual(w.leftToFetch, 18800)
        XCTAssertEqual(w.startedLeft, 32254)
        XCTAssertEqual(jobs[1].state, .finished)
        XCTAssertEqual(w.resumeArguments, ["photo", "40", "whatsapp", "18m"])
    }

    func testAliveProcessMakesItRunningAndADeadRunningJobStopped() {
        let more = log + "\n[10-25 17:47:54] [whatsapp/photo] fetching the next chunk"
        let ps = ["/bin/bash /Users/x/.iphone-image/cycle.sh photo 40 whatsapp 18m", "grep cycle.sh"]
        XCTAssertEqual(Jobs.read(log: more, processes: ps).first?.state, .running)
        guard case .stopped = Jobs.read(log: more, processes: []).first?.state else { return XCTFail() }
    }

    func testHotspotPauseSurvivesWhileAlive() {
        let paused = log + "\n[10-25 18:00:00] [whatsapp/photo] on a metered connection (phone hotspot). PAUSED until back on normal wifi."
        let ps = ["caffeinate -dimsu /Users/x/.iphone-image/cycle.sh photo 40 whatsapp 18m"]
        guard case .paused = Jobs.read(log: paused, processes: ps).first?.state else { return XCTFail() }
    }

    func testChunkProgressMovesBetweenCycles() {
        let more = log + "\n[10-25 18:25:53] [whatsapp/photo] fetching the next chunk\n  [3,931] 7cf5a498  137.3 KB  0.15 MB/s"
        let ps = ["/bin/bash /x/cycle.sh photo 40 whatsapp 18m"]
        let j = Jobs.read(log: more, processes: ps)[0]
        XCTAssertEqual(j.fetchedThisChunk, 3931)
        XCTAssertEqual(j.left, 18800 - 3931)
    }

    func testIsCycleIgnoresOtherJobs() {
        XCTAssertFalse(Jobs.isCycle("/bin/bash cycle.sh video 40 whatsapp 18m", type: "photo", source: "whatsapp"))
        XCTAssertFalse(Jobs.isCycle("/bin/bash watch-job.sh whatsapp 18m", type: "photo", source: "whatsapp"))
    }
}

final class DetectorTests: XCTestCase {
    func testStoppedJobsComeBeforeSpace() {
        var s = LibrarySnapshot()
        var r = RecipeStats(recipe: Recipe(kind: .screenshots, age: Age(1, "y")))
        r.selected = Tally(count: 10, bytes: 10_000_000_000)
        r.removableNow = Tally(count: 10, bytes: 10_000_000_000)
        s.recipes = [r]
        let job = Job(source: "whatsapp", type: "photo", state: .stopped("x"), lastLine: "", lastAt: "")
        let f = Detector.findings(library: s, jobs: [job], doctor: nil)
        XCTAssertEqual(f.map(\.id), ["job.whatsapp/photo", "ready.screenshots"])
        XCTAssertEqual(f[0].action, .resume("whatsapp/photo"))
    }
}

final class FilterTests: XCTestCase {
    func testArgsAreTheCLIFlags() {
        let f = Filter(source: "whatsapp", type: "video", olderThan: "6m", minMB: 20, proxyTolerance: 0.5)
        XCTAssertEqual(f.removalArgs, ["--source", "whatsapp", "--type", "video", "--older-than", "6m", "--min-size", "20MB", "--proxy-threshold", "0.50"])
        XCTAssertEqual(f.summary, "WhatsApp videos · over 20 MB · older than 6 months")
        XCTAssertEqual(f.backups.map(\.type), ["video"])
        XCTAssertTrue(Filter(type: "video").backups.isEmpty, "no source: cycle.sh would read it as camera")
        XCTAssertEqual(Recipe(custom: f).id, Recipe(custom: Filter(name: "x", source: "whatsapp", type: "video", olderThan: "6m", minMB: 20, proxyTolerance: 0.5)).id)
    }

    /// The app's count must be the CLI's count. Runs `iphone-image list` against the real
    /// ledger for a spread of filters; skipped where there is no ledger.
    func testCountsMatchTheCLIOnTheRealLedger() throws {
        guard FileManager.default.fileExists(atPath: Ledger.defaultPath) else { throw XCTSkip("no ledger here") }
        let db = try SQLiteReader(path: Ledger.defaultPath)
        let filters = [
            Filter(source: "whatsapp", type: "video", olderThan: "6m", minMB: 20),
            Filter(source: "camera", type: "video", minMB: 1024),
            Filter(source: "screenshot", olderThan: "90d"),
            Filter(type: "burst", olderThan: "30d"),
            Filter(source: "com.burbn.instagram", year: 2022),
            Filter(type: "photo", newerThan: "1y", maxMB: 1),
        ]
        for f in filters {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/bash")
            let args = f.selectorArgs.map { "'\($0)'" }.joined(separator: " ")
            p.arguments = ["-c", "cd '\(CLIEngine.defaultRepo)' && PYTHONPATH=src ./.venv/bin/python -m iphone_image --json list \(args)"]
            let pipe = Pipe()
            p.standardOutput = pipe
            try p.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            let cli = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            let app = try Ledger.stats(db, Recipe(custom: f))
            XCTAssertEqual(app.selected.count, cli["matched"] as? Int, f.summary)
        }
    }
}

final class DoctorProgressTests: XCTestCase {
    func testLinesArriveWhole() {
        final class Box: @unchecked Sendable { var lines: [String] = [] }
        let box = Box()
        let b = LineBuffer { box.lines.append($0) }
        b.feed(Data(#"{"plan": ["a"]}"#.utf8))
        b.feed(Data("\n{\"start\"".utf8))
        b.feed(Data(": \"a\"}\n".utf8))
        XCTAssertEqual(box.lines, [#"{"plan": ["a"]}"#, #"{"start": "a"}"#])
    }

    /// The real doctor, about a minute. `PS_SLOW=1 swift test --filter DoctorProgress`.
    func testTheRealDoctorReportsEachCheck() throws {
        guard ProcessInfo.processInfo.environment["PS_SLOW"] == "1" else { throw XCTSkip("slow") }
        final class Box: @unchecked Sendable { var events: [String] = [] ; let lock = NSLock() }
        let box = Box()
        let report = try CLIEngine().doctor { p in
            box.lock.lock(); defer { box.lock.unlock() }
            switch p {
            case .plan(let k): box.events.append("plan:\(k.count)")
            case .started(let k): box.events.append("start:\(k)")
            case .finished(let k, let s): box.events.append("done:\(k):\(s)")
            }
        }
        XCTAssertEqual(box.events.first, "plan:\(report.checks)")
        XCTAssertEqual(box.events.filter { $0.hasPrefix("done:") }.count, report.checks)
    }
}
