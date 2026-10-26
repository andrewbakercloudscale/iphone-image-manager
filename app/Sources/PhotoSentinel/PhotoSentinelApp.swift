import AppKit
import PhotoCore
import SwiftUI

@main
struct PhotoSentinelApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = Model()

    var body: some Scene {
        Window("Photo Sentinel", id: "main") {
            ContentView(model: model)
                .disclosureGroupStyle(.expander)
                .frame(minWidth: 880, minHeight: 640)
                .onAppear { model.start() }
        }
        .defaultSize(width: 1040, height: 860)
        .commands {
            CommandGroup(replacing: .appInfo) {
                Button("About Photo Sentinel") { AboutWindow.show(model) }
                Button("Share with a Friend…") { ShareApp.show() }
            }
        }

        MenuBarExtra {
            MenuView(model: model)
        } label: {
            Image(systemName: menuSymbol)
            let pct = model.activeJob?.progress.map { Fmt.pct($0) }
            let label = [pct, model.updates.update == nil ? nil : "● update"].compactMap { $0 }.joined(separator: " ")
            if !label.isEmpty { Text(label).monospacedDigit() }
        }
        .menuBarExtraStyle(.window)
    }

    /// The glyph says the most urgent thing: a stopped job, then a pause, then a running backup.
    private var menuSymbol: String {
        if model.jobs.contains(where: { if case .stopped = $0.state { return $0.lastAt >= recentCutoff }; return false }) {
            return "exclamationmark.icloud.fill"
        }
        if case .paused = model.activeJob?.state { return "pause.circle.fill" }
        if model.activeJob != nil { return "icloud.and.arrow.up.fill" }
        return "photo.on.rectangle.angled"
    }

    /// Jobs stopped within the last 2 days raise the menu bar; older ones live on Activity.
    private var recentCutoff: String {
        let f = DateFormatter()
        f.dateFormat = "MM-dd HH:mm:ss"
        return f.string(from: Date().addingTimeInterval(-2 * 86_400))
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        MainActor.assumeIsolated { Snapshot.runIfAsked() }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

struct ContentView: View {
    @ObservedObject var model: Model

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Header(model: model)
            TabBar(selection: $model.tab, badges: badges)
            UpdateBanner(updates: model.updates, busy: model.backupBusy)
            PermissionsBanner(permissions: model.permissions)
            if let notice = model.notice {
                HStack {
                    Image(systemName: "info.circle.fill").foregroundStyle(Tab.backup.color)
                    Text(notice).font(.callout)
                    Spacer()
                    Button("Dismiss") { model.notice = nil }.buttonStyle(.link)
                }
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 10).fill(Tab.backup.color.opacity(0.1)))
            }
            ScrollViewReader { proxy in
            ScrollView {
                Group {
                    if let error = model.error, model.library == nil {
                        Card(title: "Can't read the library yet", symbol: "exclamationmark.triangle.fill", tint: Theme.warn) {
                            VStack(alignment: .leading, spacing: 8) {
                                Text(error).font(.callout).textSelection(.enabled)
                                Text("Check the ledger path in Settings.").font(.callout).foregroundStyle(.secondary)
                            }
                        }
                    } else if model.library == nil {
                        ProgressView("Reading your library…").frame(maxWidth: .infinity, minHeight: 300)
                    } else {
                        page
                    }
                }
                .padding(.bottom, 12)
                // The scroller draws over the content's right edge: keep the cards clear of
                // it, and move it out into the window margin so the cards still line up.
                .padding(.trailing, 14)
            }
            .padding(.trailing, -14)
            .onChange(of: model.scrollTarget) {
                guard let target = model.scrollTarget else { return }
                // After the tab has drawn, so the target exists.
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                    withAnimation { proxy.scrollTo(target, anchor: .top) }
                    model.scrollTarget = nil
                }
            }
            }
            Footer(model: model)
        }
        .padding(16)
        .background(Color(light: "#f5f5f7", dark: "#1c1c1f"))
        .sheet(isPresented: Binding(get: { !model.onboarded }, set: { if !$0 { model.onboarded = true } })) {
            OnboardingView(model: model)
        }
        .sheet(item: $model.removing) { r in RemovalSheet(model: model, recipe: r) }
    }

    @ViewBuilder private var page: some View {
        switch model.tab {
        case .checkup: CheckupView(model: model)
        case .library: LibraryView(model: model)
        case .cleanUp: LibraryView(model: model)
        case .backup: BackupView(model: model)
        case .activity: ActivityView(model: model)
        case .bin: BinView(model: model)
        case .settings: SettingsView(model: model)
        }
    }

    private var badges: [Tab: Int] {
        let f = model.findings
        let stopped = model.jobs.filter { if case .stopped = $0.state { return true }; return false }.count
        return [.checkup: f.count, .activity: stopped]
    }
}

struct Header: View {
    @ObservedObject var model: Model

    var body: some View {
        HStack(spacing: 12) {
            SymbolTile(symbol: "photo.on.rectangle.angled", color: Tab.checkup.color, size: 44)
            VStack(alignment: .leading, spacing: 2) {
                Text("Photo Sentinel").font(.title2.weight(.bold))
                Text(subtitle).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            if let s = model.library {
                Pill(text: "\(Fmt.pct(s.coverage)) backed up", symbol: "checkmark.shield.fill",
                     color: s.coverage > 0.9 ? Theme.good : Theme.warn)
            }
            jobPill
        }
    }

    private var subtitle: String {
        guard let s = model.library else { return "Reading the ledger" }
        return "\(Fmt.count(s.onPhone.count)) items on your iPhone · \(Fmt.bytes(s.onPhone.bytes))"
    }

    @ViewBuilder private var jobPill: some View {
        if let j = model.activeJob {
            // Says what it is doing, not just a number: "Backing up WhatsApp photos · 32% done · 21,880 left".
            Button { model.tab = .activity } label: {
                switch j.state {
                case .paused: Pill(text: "Backing up \(j.title): paused", symbol: "pause.circle.fill", color: Theme.warn)
                default:
                    Pill(text: "Backing up \(j.title)" + (j.progress.map { " · \(Fmt.pct($0)) done" } ?? "")
                         + (j.left.map { " · \(Fmt.count($0)) left" } ?? ""),
                         symbol: "icloud.and.arrow.up.fill", color: Tab.backup.color)
                }
            }
            .buttonStyle(.plain)
            .help("Copying from Photos to Google Drive. Percent of this job's items done since it started. Click for details.")
        } else {
            Pill(text: "No job running", symbol: "moon.zzz.fill", color: .secondary)
        }
    }
}

struct Footer: View {
    @ObservedObject var model: Model

    var body: some View {
        HStack(spacing: 10) {
            Spacer()
            CapsuleButton(title: "Welcome", symbol: "hand.wave.fill", color: Theme.accent, style: .soft) { model.onboarded = false }
            CapsuleButton(title: "Open Photos", symbol: "photo.fill", color: Theme.good, style: .soft) {
                NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/Photos.app"))
            }
            CapsuleButton(title: "Open cycle.log", symbol: "doc.text.fill", color: Tab.backup.color, style: .soft) {
                NSWorkspace.shared.open(URL(fileURLWithPath: Jobs.logPath))
            }
            if let rel = model.updates.update {
                CapsuleButton(title: "About: v\(rel.version) is out", symbol: "arrow.down.circle.fill", color: Color(hex: "#ea580c")) { AboutWindow.show(model) }
            } else {
                CapsuleButton(title: "About", symbol: "info.circle.fill", color: Tab.library.color, style: .soft) { AboutWindow.show(model) }
            }
            CapsuleButton(title: "Share", symbol: "square.and.arrow.up", color: Tab.checkup.color, style: .soft) { ShareApp.show() }
                .help("Share with a friend by Messages, Mail, WhatsApp, AirDrop and more")
            CapsuleButton(title: "Quit", symbol: "power", color: Theme.bad, style: .soft) { NSApp.terminate(nil) }
            Spacer()
        }
    }
}

/// The menu bar panel: the job in flight and a way back to the window.
struct MenuView: View {
    @ObservedObject var model: Model
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Photo Sentinel").font(.headline)
            if let rel = model.updates.update {
                Button { model.updates.install(busy: model.backupBusy) } label: {
                    Label("Update available: v\(rel.version)", systemImage: "arrow.down.circle.fill").foregroundStyle(Color(hex: "#2563eb"))
                }
                .buttonStyle(.plain)
            }
            if model.jobs.isEmpty { Text("No backup jobs yet.").foregroundStyle(.secondary) }
            ForEach(model.jobs.prefix(4)) { j in
                JobRow(job: j, compact: true) { model.resume(j.id) }
            }
            Divider()
            HStack {
                Button("Open Photo Sentinel") {
                    openWindow(id: "main")
                    NSApp.activate(ignoringOtherApps: true)
                }
                Spacer()
                Button("About") { AboutWindow.show(model) }
                Button("Share…") { ShareApp.show() }
                Button("Quit") { NSApp.terminate(nil) }
            }
        }
        .padding(14)
        .frame(width: 380)
        .onAppear { model.refreshJobs() }
    }
}
