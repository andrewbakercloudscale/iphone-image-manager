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

    func testTakenBetweenSaysTheAgeRange() {
        let now = ISO8601DateFormatter().date(from: "2026-10-25T12:00:00Z")!
        XCTAssertEqual(Fmt.takenBetween("2023-09-01T00:00:00Z", "2026-09-20T08:00:00Z", now: now), "taken between 5 weeks and 3 years ago")
        XCTAssertEqual(Fmt.takenBetween("2026-10-20T00:00:00.123Z", "2026-10-20T00:00:00Z", now: now), "taken 5 days ago")
        XCTAssertNil(Fmt.takenBetween("", "2026-10-20T00:00:00Z", now: now))
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
        CREATE TABLE locations (id INTEGER PRIMARY KEY, country TEXT, city TEXT);
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
        // A suspect is estimated as removable (the plan settles it by Drive's size); no hash never is.
        try insert(db, 7, file: "g", bundle: "net.whatsapp.WhatsApp", at: old, bytes: 10, proxy: 0.1, cloud: "CLOUD_VERIFIED")
        try insert(db, 8, file: "h", bundle: "net.whatsapp.WhatsApp", at: old, bytes: 20, cloud: "CLOUD_VERIFIED", sha: nil)
        let now = ISO8601DateFormatter().date(from: "2026-10-25T00:00:00Z")!
        let s = try Ledger.read(db, recipes: [Recipe(kind: .whatsapp, age: Age(18, "m"))], now: now)
        let r = try XCTUnwrap(s.recipes.first)
        XCTAssertEqual(r.selected, Tally(count: 6, bytes: 1030))
        XCTAssertEqual(r.removableNow, Tally(count: 3, bytes: 510), "favourites and unhashed items are never removable; suspects are, pending the size check")
        XCTAssertEqual(r.notBackedUp, Tally(count: 1, bytes: 200))
        XCTAssertEqual(r.kept, Tally(count: 2, bytes: 320), "only items in Google Drive count as kept; the rest are not backed up")
        XCTAssertEqual(s.onPhone, Tally(count: 7, bytes: 1530))
        XCTAssertEqual(s.proxies, 2)
    }

    func testBreakdownCutsEverythingAndASelectionTheSameWays() throws {
        let db = try makeDB()
        try insert(db, 1, file: "a", bundle: "net.whatsapp.WhatsApp", at: "2019-03-01T00:00:00Z", bytes: 100, cloud: "CLOUD_VERIFIED")
        try insert(db, 2, file: "b", type: "VIDEO", bundle: "net.whatsapp.WhatsApp", at: "2019-04-01T00:00:00Z", bytes: 200)
        try insert(db, 3, file: "c", subtypes: "[\"live\"]", bundle: "com.apple.camera", at: "2026-09-01T00:00:00Z", bytes: 300, fav: 1, cloud: "CLOUD_VERIFIED")
        try insert(db, 4, file: "d", subtypes: "[\"screenshot\"]", bundle: "com.apple.springboard", at: "2026-10-01T00:00:00Z", bytes: 400)
        try insert(db, 5, file: "e", bundle: "net.whatsapp.WhatsApp", bytes: 999, present: 0)  // gone from the phone
        let now = ISO8601DateFormatter().date(from: "2026-10-25T00:00:00Z")!

        let all = try Ledger.breakdown(db, now: now)
        XCTAssertEqual(all.total, Tally(count: 4, bytes: 1000))
        XCTAssertEqual(all.bySource.map(\.key), ["screenshot", "camera", "whatsapp"], "biggest first, ties by name")
        XCTAssertEqual(Dictionary(uniqueKeysWithValues: all.byKind.map { ($0.key, $0.tally.bytes) }),
                       ["photo": 100, "video": 200, "live": 300, "screenshot": 400])
        XCTAssertEqual(Dictionary(uniqueKeysWithValues: all.byStatus.map { ($0.key, $0.tally.bytes) }),
                       ["canGo": 100, "kept": 300, "notInDrive": 600], "a favourite in Google Drive is kept, not counted as can go")
        XCTAssertEqual(all.byYear.map(\.period), ["2019", "2026"])
        XCTAssertEqual(all.byYear[0].inDrive.bytes, 100)
        XCTAssertEqual(all.byYear[0].notInDrive.bytes, 200)
        XCTAssertEqual(all.byMonth.map(\.period), ["2019-03", "2019-04", "2026-09", "2026-10"], "every month, not only recent ones")

        let wa = try Ledger.breakdown(db, filter: Filter(source: "whatsapp"), now: now)
        XCTAssertEqual(wa.total, Tally(count: 2, bytes: 300))
        XCTAssertEqual(wa.bySource.map(\.key), ["whatsapp"])
        XCTAssertEqual(wa.oldest?.prefix(7), "2019-03")
    }

    func testRoutesSayWhichDriveFolderEachItemIsOrWillBeIn() throws {
        let db = try makeDB()
        let wa = "net.whatsapp.WhatsApp"
        try insert(db, 1, file: "x.jpg", bundle: wa, bytes: 10, cloud: "CLOUD_VERIFIED")  // cloud_path Drive/x.jpg
        try db.exec("UPDATE assets SET cloud_path = 'P/WA/x.jpg' WHERE id = 1")
        try insert(db, 2, file: "y.mp4", type: "VIDEO", bundle: wa, bytes: 20)              // WhatsApp video: WhatsApp's folder
        try insert(db, 3, file: "z.mp4", type: "VIDEO", bundle: "com.apple.camera", bytes: 30)
        let json = """
        {"remote":"gdrive","account":{"name":"gdrive","kind":"drive","title":"Google Drive","canVerify":true},
         "accounts":[],"archive":"/a","destinations":{"photos":"P","videos":"V","screenshots":"","whatsapp":"P/WA"},
         "backedUp":{"count":1,"bytes":10},"proposed":{"remote":"gdrive","archive":"/a"},"changed":false,"applied":false}
        """
        let storage = try JSONDecoder().decode(StorageReport.self, from: Data(json.utf8))
        let routes = try Ledger.routes(db, Recipe(custom: Filter(type: "photo")), storage: storage)
        XCTAssertEqual(routes.map(\.folder), ["P/WA"])
        let all = try Ledger.routes(db, Recipe(custom: Filter(minMB: 0)), storage: storage)
        XCTAssertEqual(Dictionary(uniqueKeysWithValues: all.map { ($0.folder, [$0.inDrive.bytes, $0.toBackUp.bytes]) }),
                       ["P/WA": [10, 20], "V": [0, 30]], "the longest matching folder wins, so P/WA is not P")
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
        XCTAssertFalse(offline.clean, "an audit that never asked Google Drive proves nothing")
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
        XCTAssertFalse(report.clean, "--offline never asks Google Drive, so it must not read as clean")
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

    func testUploadStepIsNotCountedAsDone() {
        // The last chunk copied leaves 0 to fetch; the upload of it is still hours of work.
        let more = log + "\n[10-25 18:25:53] [whatsapp/photo] fetching the next chunk\n  [18,800] 7cf5a498  137.3 KB  0.15 MB/s"
            + "\n[10-26 06:28:00] [whatsapp/photo] uploading what landed"
            + "\n  2026/10/26 09:57:22 NOTICE:   450.249 MiB / 1.993 GiB, 22%, 47.892 KiB/s, ETA 9h26m50s (xfr#1585/11602)"
        let j = Jobs.read(log: more, processes: ["/bin/bash /x/cycle.sh photo 40 whatsapp 18m"])[0]
        XCTAssertEqual(j.phase, .uploading)
        XCTAssertEqual(j.uploaded, 1585)
        XCTAssertEqual(j.toUpload, 11602)
        XCTAssertEqual(j.progress!, 1585.0 / 11602.0, accuracy: 0.0001)
        XCTAssertEqual(j.step, "uploading to Google Drive, \(Fmt.count(1585)) of \(Fmt.count(11602)) files")
    }

    func testUploadPaceAndTimeLeft() {
        let more = log + "\n[10-26 06:28:00] [whatsapp/photo] uploading what landed"
            + "\n  2026/10/26 10:00:00 NOTICE:   1 MiB / 2 GiB, 1%, 40 KiB/s, ETA 9h (xfr#1000/2200)"
            + "\n  2026/10/26 10:04:00 NOTICE:   2 MiB / 2 GiB, 1%, 40 KiB/s, ETA 9h (xfr#1080/2200)"
        let j = Jobs.read(log: more, processes: ["/bin/bash /x/cycle.sh photo 40 whatsapp 18m"])[0]
        XCTAssertEqual(j.uploadRate!, 20, accuracy: 0.001)
        // 1,120 left at 20 a minute is 56 minutes.
        XCTAssertTrue(j.step!.hasSuffix(", about 20 a minute, under an hour to go"), j.step!)
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
        XCTAssertEqual(Filter(country: "Greece", ids: [4, 5]).selectorArgs, ["--country", "Greece", "--id", "4", "--id", "5"])
        XCTAssertTrue(Filter(source: "whatsapp", ids: [4]).backups.isEmpty, "picked items are backed up by id, not by cycle.sh")
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
            Filter(country: "South Africa"),
            Filter(type: "video", city: "cape town"),
            Filter(ids: [1, 2, 3, 99999]),
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

final class UpdatesTests: XCTestCase {
    func testVersionsCompareByNumberNotText() {
        XCTAssertTrue(Updates.isNewer("0.10.0", than: "0.9.1"))
        XCTAssertTrue(Updates.isNewer("v0.2", than: "0.1.9"))
        XCTAssertFalse(Updates.isNewer("0.1.0", than: "0.1"))
    }

    func testParsesARelease() {
        let json = ###"{"tag_name":"v0.2.0","html_url":"https://github.com/x/y/releases/tag/v0.2.0","body":"## New\n- **Folders** per source\n- Updates\n- third"}"###
        let rel = Updates.parse(Data(json.utf8))
        XCTAssertEqual(rel?.version, "0.2.0")
        XCTAssertEqual(Updates.highlights(rel?.notes ?? ""), ["Folders per source", "Updates"])
    }
}

final class TaskProgressTests: XCTestCase {
    private func status(_ stderr: String) -> TaskStatus {
        let task = CLITask(id: "t", kind: .plan, recipe: nil, title: "t", args: [], startedAt: Date(), pid: 0)
        return TaskStatus(task: task, state: .running, output: nil, stderr: stderr)
    }

    func testTheLatestProgressLineWins() {
        let s = status("""
            {"phase": "scan", "done": 0, "total": 75854}
            some log line
            {"phase": "scan", "done": 12500, "total": 75854}
            """)
        XCTAssertEqual(s.progressText, "Finding them in your library: looked at \(Fmt.count(12500)) of \(Fmt.count(75854)) photos and videos")
    }

    func testDriveAndReusedScan() {
        XCTAssertEqual(status(#"{"phase": "drive", "items": 8451}"#).progressText,
                       "Checking each of the \(Fmt.count(8451)) against its copy in Google Drive (about a minute)")
        XCTAssertEqual(status(#"{"phase": "scan", "reused": 27}"#).progressText, "Using the phone scan from a few minutes ago")
        XCTAssertNil(status("plain text").progressText)
    }

    func testDriveListingAndRemovalCounts() {
        XCTAssertEqual(status(#"{"phase": "drive", "items": 2226, "listed": 1200, "expected": 2907}"#).progressText,
                       "Checking each of the \(Fmt.count(2226)) against Google Drive: read \(Fmt.count(1200)) of about \(Fmt.count(2907)) files there")
        XCTAssertEqual(status(#"{"phase": "drive", "items": 2226, "listed": 3100, "expected": 2907}"#).progressText,
                       "Checking each of the \(Fmt.count(2226)) against Google Drive: read \(Fmt.count(3100)) files there")
        XCTAssertEqual(status(#"{"phase": "remove", "done": 500, "total": 2226}"#).progressText,
                       "Removed \(Fmt.count(500)) of \(Fmt.count(2226))")
    }
}
