import Combine
import Foundation
import PhotoCore
import SwiftUI

/// The app's state. It never writes the ledger itself: every action is the CLI or cycle.sh,
/// started detached so it outlives the app (TaskStore, Jobs).
@MainActor
final class Model: ObservableObject {
    @Published var library: LibrarySnapshot?
    @Published var jobs: [Job] = []
    @Published var doctor: DoctorReport?
    @Published var error: String?
    @Published var loading = false
    @Published var doctorRunning = false
    /// Doctor's checks as they run: key and status ("" waiting, "RUNNING", then PASS/WARN/FAIL).
    @Published var doctorSteps: [(key: String, status: String)] = []
    @Published var doctorStarted = Date()
    @Published var notice: String?
    @Published var tab: Tab = .checkup
    @Published var tasks: [TaskStatus] = []
    @Published var removing: Recipe? { didSet { if removing == nil { planNextIfAny() } } }
    /// A section to scroll to once its tab shows ("builder").
    @Published var scrollTarget: String?
    let permissions = Permissions()
    let updates = UpdateChecker()
    private var forwarding: AnyCancellable?
    /// A backup or move is running from this copy's code or settings: updates and saves wait.
    var backupBusy: Bool {
        jobs.contains { $0.state == .running } || tasks.contains { $0.running && ($0.task.kind == .backup || $0.task.kind == .refile) }
    }
    /// "Apply my plan": the clean-ups still to walk through, one removal sheet at a time.
    @Published var plan: [Recipe] = []
    @Published var planDone: [String] = []
    @Published var allProof: Proof?
    /// Backups waiting for the running one to end. One PhotoKit exporter at a time.
    @Published var queue: [QueuedBackup] = [] { didSet { saveQueue() } }
    @Published var suggestions: [Suggestion] = []
    /// The filter being built on the Library tab.
    @Published var draft = Filter()
    /// Clean-ups the owner built and saved.
    @Published var saved: [Filter] = [] { didSet { saveFilters() } }

    /// "off" keeps every item of that type on the phone.
    @AppStorage("age.cameraPhotos") var photoAge = "3y"
    @AppStorage("age.oldVideos") var videoAge = "1y"
    @AppStorage("age.whatsapp") var whatsappAge = "18m"
    @AppStorage("age.screenshots") var screenshotAge = "1y"
    @AppStorage("age.screenRecordings") var recordingAge = "30d"
    @AppStorage("ledgerPath") var ledgerPath = Ledger.defaultPath
    @AppStorage("repoPath") var repoPath = CLIEngine.defaultRepo
    @AppStorage("onboarded") var onboarded = false

    private var timers: [Timer] = []

    var recipes: [Recipe] {
        // A type set to "off" has no clean-up at all: an age-less recipe would select everything.
        let aged: [Recipe] = [
            (Recipe.Kind.cameraPhotos, photoAge), (.oldVideos, videoAge), (.whatsapp, whatsappAge),
            (.screenshots, screenshotAge), (.screenRecordings, recordingAge),
        ].compactMap { kind, text in Age(text).map { Recipe(kind: kind, age: $0) } }
        return aged + [Recipe(kind: .largeVideos, minBytes: 500 * Filter.mb)] + saved.map { Recipe(custom: $0) }
    }

    /// The age setting behind each kept-on-phone type, for the Library and onboarding.
    func ageBinding(_ kind: Recipe.Kind) -> Binding<String> {
        switch kind {
        case .cameraPhotos: $photoAge
        case .oldVideos: $videoAge
        case .whatsapp: $whatsappAge
        case .screenshots: $screenshotAge
        default: $recordingAge
        }
    }

    var ages: [String] { [photoAge, videoAge, whatsappAge, screenshotAge, recordingAge] }

    var engine: CLIEngine { CLIEngine(repo: repoPath, ledgerPath: ledgerPath, recipes: recipes) }

    var findings: [Finding] {
        guard let library else { return [] }
        return Detector.findings(library: library, jobs: jobs, doctor: doctor)
    }

    var activeJob: Job? { jobs.first { if case .running = $0.state { return true }; if case .paused = $0.state { return true }; return false } }

    func start() {
        // The menu bar and footer read the update through this model.
        forwarding = updates.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
        updates.schedule()
        loadQueue()
        loadFilters()
        refreshTasks()
        timers.append(Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refreshTasks() }
        })
        refresh()
        refreshDoctor()
        // Jobs move by the minute; the library by the hour. The ledger read takes about a second.
        timers.append(Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refreshJobs() }
        })
        timers.append(Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        })
    }

    func refresh() {
        loading = true
        let engine = engine
        Task.detached {
            let result = Result { try engine.library() }
            let jobs = engine.jobs()
            let proof = try? Ledger.proof(SQLiteReader(path: engine.ledgerPath), since: "", limit: 0)
            let suggestions = (try? result.get()).flatMap { s in
                try? Ledger.suggestions(SQLiteReader(path: engine.ledgerPath), apps: s.apps)
            } ?? []
            await MainActor.run {
                self.suggestions = suggestions
                self.loading = false
                self.allProof = proof
                self.notifyChanges(from: self.jobs, to: jobs)
                self.jobs = jobs
                switch result {
                case .success(let s): self.library = s; self.error = nil; self.placesIfStale()
                case .failure(let e): self.error = "Could not read the ledger: \(e)"
                }
            }
        }
    }

    func refreshJobs() {
        let engine = engine
        Task.detached {
            let jobs = engine.jobs()
            await MainActor.run {
                self.notifyChanges(from: self.jobs, to: jobs)
                self.jobs = jobs
                self.pumpQueue()
            }
        }
    }

    /// A backup job that was running and is not any more.
    private func notifyChanges(from old: [Job], to new: [Job]) {
        for job in new {
            guard old.first(where: { $0.id == job.id })?.state == .running else { continue }
            switch job.state {
            case .running: break
            case .finished: Notify.send("\(job.title) backed up", "The backup finished.")
            case .paused(let why): Notify.send("\(job.title) paused", why)
            case .stopped(let why): Notify.send("\(job.title) stopped", "\(why) Open Photo Sentinel to resume it.")
            }
        }
    }

    // MARK: CLI tasks: audit, removal plan, removal

    func refreshTasks() {
        let before = Set(tasks.filter(\.running).map(\.task.id))
        tasks = TaskStore.all()
        // Something just finished: the ledger has moved.
        let ended = tasks.filter { before.contains($0.task.id) && !$0.running }
        for t in ended where t.task.kind != .places {
            if let e = t.errorMessage { Notify.send("\(t.task.title) failed", e) }
            else { Notify.send("\(t.task.title) finished", "Open Photo Sentinel to see the result.") }
        }
        if !ended.isEmpty {
            refresh()
            pumpQueue()
        }
    }

    func latest(_ kind: CLITask.Kind, recipe: Recipe? = nil) -> TaskStatus? {
        tasks.first { $0.task.kind == kind && (recipe == nil || $0.task.recipe == recipe?.id) }
    }

    var runningTask: TaskStatus? { tasks.first(where: \.running) }

    func runAudit() {
        launch(.audit, recipe: nil, title: "Google Drive check", args: ["audit"])
    }

    /// Reads where each item was taken from Photos into the ledger. About 10 seconds;
    /// touches no file, phone or cloud.
    func runPlaces(quiet: Bool = false) {
        launch(.places, recipe: nil, title: "Look up places", args: ["places"], quiet: quiet)
    }

    /// Once a day, so new photos get their place without anyone asking.
    private var placesChecked = false
    private func placesIfStale() {
        guard !placesChecked, let s = library else { return }
        placesChecked = true
        let stale = s.placesAt.flatMap { ISO8601DateFormatter().date(from: $0) }.map { Date().timeIntervalSince($0) > 86_400 } ?? true
        if stale { runPlaces(quiet: true) }
    }

    /// Records the outcome of removals a crashed run left unconfirmed, from the newest scan.
    func settleRemovals() {
        launch(.settle, recipe: nil, title: "Finish unconfirmed removals", args: ["settle", "--apply"])
    }

    /// Downloads removed items back from Drive into a new folder in Downloads, hash-checked.
    func recover(_ ids: [Int]) {
        launch(.recover, recipe: nil, title: "Recover \(ids.count) item\(ids.count == 1 ? "" : "s") from Drive",
               args: ["recover"] + ids.flatMap { ["--id", String($0)] })
    }

    /// Moves files already in Drive to where the saved layout puts them (`refile`).
    func refile(_ sources: [String]) {
        launch(.refile, recipe: nil, title: "Move existing files in Drive",
               args: ["refile", "--apply"] + sources.flatMap { ["--source", $0] })
    }

    func plan(_ r: Recipe) {
        launch(.plan, recipe: r, title: "Plan: \(r.title)", args: ["remove-from-iphone"] + r.removalArgs)
    }

    func remove(_ r: Recipe, phrase: String) {
        launch(.remove, recipe: r, title: "Remove: \(r.title)",
               args: ["remove-from-iphone"] + r.removalArgs + ["--apply", "--confirm", phrase])
    }

    private func launch(_ kind: CLITask.Kind, recipe: Recipe?, title: String, args: [String], quiet: Bool = false) {
        // One CLI task at a time: two scans or a scan beside a removal would race.
        if let busy = runningTask {
            if !quiet { notice = "\(busy.task.title) is still running. Try again when it ends." }
            return
        }
        do {
            _ = try TaskStore.start(kind: kind, recipe: recipe?.id, title: title, args: args, repo: repoPath)
        } catch {
            notice = "Could not start: \(error)"
        }
        refreshTasks()
    }

    // MARK: Backups

    func backUp(_ r: Recipe) {
        if !r.filter.ids.isEmpty {
            let bytes = (try? Ledger.stats(SQLiteReader(path: ledgerPath), r))?.notBackedUp.bytes ?? 0
            backUpItems(r.filter.ids, bytes: bytes, label: r.title)
            return
        }
        let wanted = r.backups.map { QueuedBackup(type: $0.type, source: $0.source, older: $0.older) }
        let fresh = wanted.filter { !queue.contains($0) }
        queue += fresh
        pumpQueue()
        if !queue.isEmpty { notice = "\(r.title): backup queued. It starts when the running job ends." }
    }

    /// Opens the filter builder on one place.
    func showPlace(country: String, city: String? = nil) {
        draft = Filter(country: country, city: city)
        openBuilder()
    }

    /// The filter builder, on the Library tab.
    func openBuilder() {
        tab = .library
        scrollTarget = "builder"
    }

    /// Backs up everything the ages need, then opens each type's removal in turn. Each one
    /// still shows its plan and asks for the typed phrase; nothing is removed without that.
    func applyPlan(_ steps: [RecipeStats]) {
        for s in steps where s.notBackedUp.count > 0 { backUp(s.recipe) }
        plan = steps.filter { $0.removableNow.count > 0 }.map(\.recipe)
        planDone = []
        if plan.isEmpty {
            notice = "Backups queued. Nothing is in Google Drive yet to remove; come back when they finish."
        } else {
            planNext()
        }
    }

    func planNext() {
        guard let next = plan.first(where: { !planDone.contains($0.id) }) else { plan = []; return }
        planDone.append(next.id)
        removing = next
    }

    func stopPlan() {
        plan = []
        planDone = []
    }

    /// When a removal sheet closes during a plan, the next step waits for a click on the card.
    private func planNextIfAny() {
        if !plan.isEmpty && plan.allSatisfy({ planDone.contains($0.id) }) {
            notice = "Your plan is done. Anything that still needs a backup comes off the next time you apply it."
            plan = []
        }
    }

    /// Backs up items picked one by one. Queued behind any running export.
    func backUpItems(_ ids: [Int], bytes: Int64, label: String) {
        let q = QueuedBackup(type: "items", source: "", older: nil, ids: ids, bytes: bytes, label: label)
        guard !queue.contains(q) else { return }
        queue.append(q)
        pumpQueue()
        if queue.contains(q) { notice = "\(label): backup queued. It starts when the running job ends." }
    }

    func cancelQueued(_ q: QueuedBackup) { queue.removeAll { $0 == q } }

    /// Starts the next queued backup once nothing else is exporting.
    func pumpQueue() {
        guard let next = queue.first, !Jobs.anyRunning(Jobs.processes()),
              !tasks.contains(where: { $0.running && $0.task.kind == .backup }) else { return }
        if next.ids != nil {
            guard runningTask == nil else { return }
            do {
                _ = try TaskStore.start(kind: .backup, recipe: nil, title: "Back up: \(next.title)", steps: next.steps, repo: repoPath)
                queue.removeFirst()
                notice = "Backing up \(next.title). Nothing is removed from the phone by a backup."
            } catch { notice = "Could not start: \(error)" }
            refreshTasks()
            return
        }
        switch Jobs.start(type: next.type, source: next.source, older: next.older, processes: Jobs.processes()) {
        case .success:
            queue.removeFirst()
            notice = "Backing up \(next.title). Nothing is removed from the phone by a backup."
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.refreshJobs() }
        case .failure(let e): notice = e.text
        }
    }

    // MARK: Filters

    /// Counts a filter against the ledger, off the main thread.
    nonisolated func preview(_ f: Filter, ledger: String) async -> RecipeStats? {
        try? Ledger.stats(SQLiteReader(path: ledger), Recipe(custom: f))
    }

    func save(_ f: Filter) {
        saved.removeAll { $0.key == f.key }
        saved.append(f)
        refresh()
    }

    func forget(_ f: Filter) {
        saved.removeAll { $0.key == f.key }
        refresh()
    }

    private func saveFilters() {
        UserDefaults.standard.set(try? JSONEncoder().encode(saved), forKey: "savedFilters")
    }

    private func loadFilters() {
        if let d = UserDefaults.standard.data(forKey: "savedFilters"),
           let f = try? JSONDecoder().decode([Filter].self, from: d) { saved = f }
    }

    private func saveQueue() {
        UserDefaults.standard.set(try? JSONEncoder().encode(queue), forKey: "backupQueue")
    }

    private func loadQueue() {
        if let d = UserDefaults.standard.data(forKey: "backupQueue"),
           let q = try? JSONDecoder().decode([QueuedBackup].self, from: d) { queue = q }
    }

    func refreshDoctor() {
        doctorRunning = true
        doctorSteps = []
        doctorStarted = Date()
        let engine = engine
        Task.detached {
            let report = try? engine.doctor { p in
                Task { @MainActor in self.apply(p) }
            }
            await MainActor.run {
                self.doctor = report
                self.doctorRunning = false
            }
        }
    }

    private func apply(_ p: DoctorProgress) {
        switch p {
        case .plan(let keys): doctorSteps = keys.map { ($0, "") }
        case .started(let k): set(k, "RUNNING")
        case .finished(let k, let status): set(k, status)
        }
    }

    private func set(_ key: String, _ status: String) {
        if let i = doctorSteps.firstIndex(where: { $0.key == key }) { doctorSteps[i].status = status }
    }

    func resume(_ id: String) {
        guard let job = jobs.first(where: { $0.id == id }) else { return }
        switch Jobs.resume(job, processes: Jobs.processes()) {
        case .success: notice = "\(job.title) backup resumed. It carries on from where it stopped."
        case .failure(let e): notice = e.text
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.refreshJobs() }
    }

    func perform(_ action: Finding.Action) {
        switch action {
        case .resume(let id): resume(id)
        case .recipe(let id):
            tab = .library
            removing = recipes.first { $0.id == id }
        case .openTab(let name): tab = Tab.named(name) ?? .checkup
        case .none: break
        }
    }
}

struct QueuedBackup: Codable, Hashable, Identifiable {
    let type: String
    let source: String
    let older: String?
    /// Picked items instead of a whole source: fetched, uploaded and checked by id.
    var ids: [Int]? = nil
    var bytes: Int64? = nil
    var label: String? = nil
    var id: String { ids.map { "items/" + $0.map(String.init).joined(separator: ",") } ?? "\(source)/\(type)" }
    var title: String {
        if let label { return label }
        return "\(Channel.allCases.first { $0.cliSource == source }?.title ?? source) \(type == "video" ? "videos" : "photos")" + (older.map { ", older than \(Age($0)?.words ?? $0)" } ?? "")
    }

    /// sync, cloud, release for exactly these items. The budget is their size rounded up,
    /// so a 4.7 GB video is not skipped as bigger than a chunk; sync still keeps the
    /// Mac's free-space floor and refuses if they will not fit.
    var steps: [[String]] {
        let sel = (ids ?? []).flatMap { ["--id", String($0)] }
        let gb = max(1, Int(((bytes ?? 0) + 1_073_741_823) / 1_073_741_824) + 1)
        return [["sync"] + sel + ["--budget", "\(gb)GB", "--apply"], ["cloud"] + sel + ["--apply"], ["release"] + sel + ["--apply"]]
    }
}
