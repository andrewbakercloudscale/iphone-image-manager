import Foundation
import PhotoCore
import SwiftUI

/// The app's state. Reads only: Phase A never writes the ledger. The one action it takes is
/// resuming a backup job, which runs cycle.sh exactly as HANDOVER.md does.
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
            Recipe(kind: .largeVideos, minBytes: 500_000_000),
        ]
    }

    var engine: CLIEngine { CLIEngine(repo: repoPath, ledgerPath: ledgerPath, recipes: recipes) }

    var findings: [Finding] {
        guard let library else { return [] }
        return Detector.findings(library: library, jobs: jobs, doctor: doctor)
    }

    var activeJob: Job? { jobs.first { if case .running = $0.state { return true }; if case .paused = $0.state { return true }; return false } }

    func start() {
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
            await MainActor.run {
                self.loading = false
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
            await MainActor.run { self.jobs = jobs }
        }
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
        case .recipe: tab = .cleanUp
        case .openTab(let name): tab = Tab.named(name) ?? .checkup
        case .none: break
        }
    }
}
