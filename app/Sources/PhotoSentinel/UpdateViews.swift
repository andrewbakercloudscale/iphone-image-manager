import AppKit
import PhotoCore
import SwiftUI
import UserNotifications

/// Checks GitHub at launch and every 6 hours. A newer release raises a banner on every tab,
/// a dot on the menu-bar icon, an About button in the footer, and one notification per version.
@MainActor
final class UpdateChecker: ObservableObject {
    enum State: Equatable { case idle, checking, upToDate, noReleases, installing, failed(String) }

    @Published var update: Updates.Release?
    @Published var state: State = .idle
    @Published var lastCheck: Date?
    @AppStorage("notifiedUpdate") private var notified = ""
    private var timer: Timer?

    static var appVersion: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0" }

    /// The source checkout this copy was built from, when there is one: it updates in place.
    static var checkout: String? {
        let repo = CLIEngine.defaultRepo
        return FileManager.default.fileExists(atPath: repo + "/.git") ? repo : nil
    }

    func schedule() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak self] in self?.check() }
        timer = Timer.scheduledTimer(withTimeInterval: 6 * 3600, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.check() }
        }
    }

    func check() {
        guard state != .checking, state != .installing else { return }
        state = .checking
        Task.detached {
            let result = Updates.latest()
            await MainActor.run {
                self.lastCheck = Date()
                switch result {
                case .success(let rel?) where Updates.isNewer(rel.version, than: Self.appVersion):
                    self.update = rel
                    self.state = .idle
                    self.announce(rel)
                case .success(let rel):
                    self.update = nil
                    self.state = rel == nil ? .noReleases : .upToDate
                case .failure(let e):
                    self.state = .failed(e.text)
                }
            }
        }
    }

    private func announce(_ rel: Updates.Release) {
        guard notified != rel.version else { return }
        notified = rel.version
        let new = Updates.highlights(rel.notes)
        Notify.send("Photo Sentinel \(rel.version) is available",
                    (new.isEmpty ? "" : new.joined(separator: " ") + " ") + "Open Photo Sentinel to update.")
    }

    /// From a source checkout: pull, rebuild, reinstall and reopen (build-app.sh does the last three).
    /// Otherwise the release page, to download it.
    func install(busy: Bool) {
        guard let rel = update else { return }
        guard let repo = Self.checkout else {
            if let page = rel.page { NSWorkspace.shared.open(page) }
            return
        }
        guard !busy else {
            state = .failed("A backup is running from this copy's code. Update once it finishes.")
            return
        }
        state = .installing
        let log = Jobs.home + "/update.log"
        let script = "cd '\(repo)' && git fetch --tags && git checkout -q main && git pull --ff-only && bash app/scripts/build-app.sh"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-lc", "(\(script)) > '\(log)' 2>&1"]
        p.terminationHandler = { proc in
            Task { @MainActor in
                // Success relaunches the new copy, which replaces this one; only a failure lands here.
                if proc.terminationStatus != 0 { self.state = .failed("The update did not build. Details are in \(log).") }
            }
        }
        do { try p.run() } catch { state = .failed("Could not start the update: \(error.localizedDescription)") }
    }

    var checkedText: String {
        guard let lastCheck else { return "Not checked yet" }
        return "Last checked " + RelativeDateTimeFormatter().localizedString(for: lastCheck, relativeTo: Date())
    }
}

/// At the top of every tab while a newer version is out: hard to miss on purpose.
struct UpdateBanner: View {
    @ObservedObject var updates: UpdateChecker
    let busy: Bool
    /// The version whose banner was hidden. The next version brings it back; About still shows it.
    @AppStorage("dismissedUpdate") private var dismissed = ""
    private let tint = Color(hex: "#2563eb")

    var body: some View {
        if let rel = updates.update, dismissed != rel.version {
            HStack(spacing: 12) {
                Image(systemName: "arrow.down.circle.fill").font(.system(size: 26, weight: .semibold)).foregroundStyle(.white)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Update available: v\(rel.version)").font(.headline).foregroundStyle(.white)
                    let new = Updates.highlights(rel.notes)
                    Text("You have v\(UpdateChecker.appVersion)." + (new.isEmpty ? "" : " New: " + new.joined(separator: " ")))
                        .font(.caption).foregroundStyle(.white.opacity(0.92)).lineLimit(2)
                    if case .failed(let why) = updates.state {
                        Text(why).font(.caption.weight(.semibold)).foregroundStyle(.white)
                    }
                }
                Spacer(minLength: 6)
                if let page = rel.page {
                    Button { NSWorkspace.shared.open(page) } label: {
                        Text("What's new").font(.callout.weight(.medium)).foregroundStyle(.white)
                            .padding(.horizontal, 10).padding(.vertical, 6)
                            .background(Capsule().strokeBorder(.white.opacity(0.8), lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                }
                Button { updates.install(busy: busy) } label: {
                    Group {
                        if updates.state == .installing {
                            HStack(spacing: 6) { ProgressView().controlSize(.small); Text("Updating…") }
                        } else {
                            Label(UpdateChecker.checkout == nil ? "Download" : "Update and relaunch", systemImage: "arrow.down.to.line")
                        }
                    }
                    .font(.callout.weight(.semibold)).foregroundStyle(tint)
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(Capsule().fill(.white))
                }
                .buttonStyle(.plain)
                .disabled(updates.state == .installing)
                .help(busy ? "A backup is running; update once it finishes" : "Get the new version and reopen")
                Button { dismissed = rel.version } label: {
                    Image(systemName: "xmark").font(.callout.weight(.bold)).foregroundStyle(.white)
                        .frame(width: 26, height: 26).background(Circle().fill(.white.opacity(0.2)))
                }
                .buttonStyle(.plain)
                .help("Hide until the next version. About still has it")
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 10).fill(tint))
        }
    }
}

/// In About: which version this is, whether a newer one is out, and a Check now button.
struct UpdatesRow: View {
    @ObservedObject var updates: UpdateChecker
    let busy: Bool

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(status).font(.callout.weight(.semibold)).foregroundStyle(updates.update == nil ? Color.primary : Color(hex: "#2563eb"))
                Text("You have v\(UpdateChecker.appVersion)  ·  \(updates.checkedText)").font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if updates.update != nil {
                Button(UpdateChecker.checkout == nil ? "Download" : "Update and relaunch") { updates.install(busy: busy) }
                    .buttonStyle(.borderedProminent).disabled(updates.state == .installing)
            }
            Button {
                updates.check()
            } label: {
                if updates.state == .checking { ProgressView().controlSize(.small) } else { Label("Check now", systemImage: "arrow.clockwise") }
            }
            .disabled(updates.state == .checking || updates.state == .installing)
        }
    }

    private var status: String {
        if let u = updates.update { return "Version \(u.version) is available" }
        switch updates.state {
        case .checking: return "Checking…"
        case .installing: return "Updating…"
        case .failed(let why): return why
        case .noReleases: return "No releases published yet"
        case .upToDate: return "Up to date"
        case .idle: return "Not checked yet"
        }
    }
}
