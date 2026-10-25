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
    @Published var notice: String?
    @Published var tab: Tab = .checkup
    @Published var tasks: [TaskStatus] = []
    @Published var removing: Recipe?
    @Published var allProof: Proof?
    /// Backups waiting for the running one to end. One PhotoKit exporter at a time.
    @Published var queue: [QueuedBackup] = [] { didSet { saveQueue() } }
    @Published var suggestions: [Suggestion] = []
    /// The filter being built on the Clean up tab.
    @Published var draft = Filter()
    /// Clean-ups the owner built and saved.
    @Published var saved: [Filter] = [] { didSet { saveFilters() } }

    @AppStorage("age.oldVideos") var videoAge = "1y"
    @AppStorage("age.whatsapp") var whatsappAge = "18m"
    @AppStorage("age.screenshots") var screenshotAge = "1y"
    @AppStorage("age.screenRecordings") var recordingAge = "30d"
    @AppStorage("ledgerPath") var ledgerPath = Ledger.defaultPath
    @AppStorage("repoPath") var repoPath = CLIEngine.defaultRepo
    @AppStorage("onboarded") var onboarded = false

    private var timers: [Timer] = []

    var recipes: [Recipe] {
        [
            Recipe(kind: .oldVideos, age: Age(videoAge)),
            Recipe(kind: .whatsapp, age: Age(whatsappAge)),
            Recipe(kind: .screenshots, age: Age(screenshotAge)),
            Recipe(kind: .screenRecordings, age: Age(recordingAge)),
            Recipe(kind: .largeVideos, minBytes: 500 * Filter.mb),
        ] + saved.map { Recipe(custom: $0) }
    }

    var engine: CLIEngine { CLIEngine(repo: repoPath, ledgerPath: ledgerPath, recipes: recipes) }

    var findings: [Finding] {
        guard let library else { return [] }
        return Detector.findings(library: library, jobs: jobs, doctor: doctor)
    }

    var activeJob: Job? { jobs.first { if case .running = $0.state { return true }; if case .paused = $0.state { return true }; return false } }

    func start() {
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
                self.jobs = jobs
                switch result {
                case .success(let s): self.library = s; self.error = nil
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
                self.jobs = jobs
                self.pumpQueue()
            }
        }
    }

    // MARK: CLI tasks: audit, removal plan, removal

    func refreshTasks() {
        let before = Set(tasks.filter(\.running).map(\.task.id))
        tasks = TaskStore.all()
        // Something just finished: the ledger has moved.
        if tasks.contains(where: { before.contains($0.task.id) && !$0.running }) { refresh() }
    }

    func latest(_ kind: CLITask.Kind, recipe: Recipe? = nil) -> TaskStatus? {
        tasks.first { $0.task.kind == kind && (recipe == nil || $0.task.recipe == recipe?.id) }
    }

    var runningTask: TaskStatus? { tasks.first(where: \.running) }

    func runAudit() {
        launch(.audit, recipe: nil, title: "Google Drive check", args: ["audit"])
    }

    func plan(_ r: Recipe) {
        launch(.plan, recipe: r, title: "Plan: \(r.title)", args: ["remove-from-iphone"] + r.removalArgs)
    }

    func remove(_ r: Recipe, phrase: String) {
        launch(.remove, recipe: r, title: "Remove: \(r.title)",
               args: ["remove-from-iphone"] + r.removalArgs + ["--apply", "--confirm", phrase])
    }

    private func launch(_ kind: CLITask.Kind, recipe: Recipe?, title: String, args: [String]) {
        // One CLI task at a time: two scans or a scan beside a removal would race.
        if let busy = runningTask { notice = "\(busy.task.title) is still running. Try again when it ends."; return }
        do {
            _ = try TaskStore.start(kind: kind, recipe: recipe?.id, title: title, args: args, repo: repoPath)
        } catch {
            notice = "Could not start: \(error)"
        }
        refreshTasks()
    }

    // MARK: Backups

    func backUp(_ r: Recipe) {
        let wanted = r.backups.map { QueuedBackup(type: $0.type, source: $0.source, older: $0.older) }
        let fresh = wanted.filter { !queue.contains($0) }
        queue += fresh
        pumpQueue()
        if !queue.isEmpty { notice = "\(r.title): backup queued. It starts when the running job ends." }
    }

    func cancelQueued(_ q: QueuedBackup) { queue.removeAll { $0 == q } }

    /// Starts the next queued backup once nothing else is exporting.
    func pumpQueue() {
        guard let next = queue.first, !Jobs.anyRunning(Jobs.processes()) else { return }
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
        let engine = engine
        Task.detached {
            let report = try? engine.doctor()
            await MainActor.run {
                self.doctor = report
                self.doctorRunning = false
            }
        }
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
            tab = .cleanUp
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
    var id: String { "\(source)/\(type)" }
    var title: String { "\(Channel.allCases.first { $0.cliSource == source }?.title ?? source) \(type == "video" ? "videos" : "photos")" + (older.map { ", older than \(Age($0)?.words ?? $0)" } ?? "") }
}
