import AppKit
import PhotoCore
import SwiftUI

/// Settings: which account backups go to, which folders in it, and the archive folder on
/// this Mac. Accounts are rclone remotes; adding one is `rclone config`, opened in Terminal.
struct StorageCard: View {
    @ObservedObject var model: Model
    @State private var account = ""
    @State private var archive = ""
    @State private var message: (ok: Bool, text: String)?
    @State private var saving = false

    var body: some View {
        Card(title: "Where backups go", symbol: "externaldrive.fill.badge.icloud", tint: Tab.backup.color) {
            if let s = model.storage {
                VStack(alignment: .leading, spacing: 12) {
                    Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                        GridRow {
                            Text("Account")
                            HStack(spacing: 8) {
                                Picker("", selection: $account) {
                                    ForEach(s.accounts) { a in
                                        Text(a.canVerify ? "\(a.name) · \(a.title)" : "\(a.name) · \(a.title) (cannot be checked yet)")
                                            .tag(a.name)
                                            .selectionDisabled(!a.canVerify)
                                    }
                                }
                                .labelsHidden().fixedSize()
                                CapsuleButton(title: "Add an account…", symbol: "plus.circle.fill", color: Tab.backup.color, style: .ghost) {
                                    Self.openRcloneConfig()
                                }
                                .help("Opens Terminal with rclone's own setup. It signs in to the account in your browser and keeps the credentials; Photo Sentinel never sees them. Come back and pick the new account here.")
                            }
                        }
                        GridRow(alignment: .top) {
                            Text("Folders")
                            VStack(alignment: .leading, spacing: 4) {
                                ForEach(["photos", "videos", "screenshots", "whatsapp"], id: \.self) { k in
                                    HStack(spacing: 6) {
                                        Text(k == "whatsapp" ? "WhatsApp" : k.capitalized).foregroundStyle(.secondary).frame(width: 90, alignment: .leading)
                                        Text((s.destinations[k] ?? "").isEmpty ? "with the photos" : s.destinations[k]!)
                                            .font(.callout.monospaced()).textSelection(.enabled)
                                    }
                                }
                                Button("Change folders and their layout in Backup → How backups are stored") { model.tab = .backup }
                                    .buttonStyle(.link)
                            }
                        }
                        GridRow {
                            Text("Archive on this Mac")
                            HStack(spacing: 8) {
                                TextField("", text: $archive).textFieldStyle(.roundedBorder).font(.callout.monospaced())
                                Button("Choose…") { choose() }
                                Button("Show in Finder") {
                                    NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: NSString(string: s.archive).expandingTildeInPath)
                                }
                            }
                        }
                    }
                    .font(.callout)
                    if changed(s) { pending(s) }
                    if let m = message {
                        Text(m.text).font(.callout).foregroundStyle(m.ok ? Theme.good : Theme.bad).fixedSize(horizontal: false, vertical: true)
                    }
                    Text("Every backup is checked by its SHA-256 before anything leaves the phone. Google Drive reports SHA-256. S3 reports only MD5, and OneDrive and Dropbox their own hashes, so they are listed but cannot be picked until the check learns their hash.")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                .onAppear { reset(s) }
                .onChange(of: s.remote) { reset(s) }
                .onChange(of: s.archive) { reset(s) }
            } else {
                Text("Reading the backup settings…").font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    private func changed(_ s: StorageReport) -> Bool {
        account != s.remote || NSString(string: archive).expandingTildeInPath != s.archive
    }

    @ViewBuilder private func pending(_ s: StorageReport) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if account != s.remote {
                Label("New backups go to \(account). The \(Fmt.count(s.backedUp.count)) items (\(Fmt.bytes(s.backedUp.bytes))) already backed up stay in \(s.accountTitle) and are not copied over. Removal checks the account in use, so those items stay on the phone until they are in \(account) too.",
                      systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(Theme.warn)
            }
            if NSString(string: archive).expandingTildeInPath != s.archive {
                Label("New copies go to \(archive). Files already in \(s.archive) stay there.", systemImage: "folder.fill")
            }
            HStack(spacing: 8) {
                CapsuleButton(title: saving ? "Saving…" : "Save", symbol: "checkmark", color: Tab.backup.color) {
                    Task { await save(s) }
                }
                .disabled(saving || model.backupBusy)
                .help(model.backupBusy ? "Wait for the running backup to finish: it uses these settings." : "")
                CapsuleButton(title: "Cancel", color: .secondary, style: .ghost) { reset(s) }
                if model.backupBusy { Text("Saving waits until the running backup finishes.").foregroundStyle(.secondary) }
            }
        }
        .font(.callout).fixedSize(horizontal: false, vertical: true)
    }

    private func reset(_ s: StorageReport) {
        account = s.remote
        archive = s.archive
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.directoryURL = URL(fileURLWithPath: NSString(string: archive).expandingTildeInPath)
        if panel.runModal() == .OK, let url = panel.url { archive = url.path }
    }

    private func save(_ s: StorageReport) async {
        saving = true
        let engine = model.engine
        let remote = account != s.remote ? account : nil
        let folder = NSString(string: archive).expandingTildeInPath
        let arch = folder != s.archive ? folder : nil
        let result = await Task.detached { Result { try engine.storage(remote: remote, archive: arch, apply: true) } }.value
        saving = false
        switch result {
        case .success(let r):
            model.storage = r
            message = (true, "Saved. New backups use it.")
            model.refresh()
        case .failure(let e):
            message = (false, "Not saved: \((e as? JobError)?.text ?? "\(e)")")
        }
    }

    static func openRcloneConfig() {
        let script = "tell application \"Terminal\" to activate\ntell application \"Terminal\" to do script \"rclone config\""
        NSAppleScript(source: script)?.executeAndReturnError(nil)
    }
}

/// Settings: when backups pause. The job checks before every step and stops a transfer
/// the moment the Mac joins a metered connection.
struct WhenBackupsRunCard: View {
    @State private var metered: Bool?

    var body: some View {
        Card(title: "When backups run", symbol: "wifi", tint: Tab.backup.color) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Image(systemName: "personalhotspot").foregroundStyle(Theme.good)
                    Text("Pause on a phone hotspot").font(.body.weight(.medium))
                    Pill(text: "Always on", symbol: "checkmark.circle.fill", color: Theme.good)
                    Spacer()
                    if let metered {
                        Text(metered ? "Now: on a hotspot, backups are paused" : "Now: on normal Wi-Fi")
                            .font(.callout).foregroundStyle(metered ? Theme.warn : .secondary)
                    }
                }
                Text("Nothing copies or uploads on your iPhone's Personal Hotspot or any connection macOS marks as metered. Switching to one mid-upload stops the upload at once; it carries on by itself back on normal Wi-Fi. Checks and plans are small and still run.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
        .task { metered = await Self.isMetered() }
    }

    /// cycle.sh's rule: an iPhone hotspot hands out 172.20.10.0/28, or macOS flags the link IsExpensive.
    static func isMetered() async -> Bool {
        await Task.detached {
            let route = shell("/sbin/route", ["-n", "get", "default"])
            let gateway = route.split(separator: "\n").first { $0.contains("gateway:") }?.split(separator: " ").last.map(String.init) ?? ""
            if gateway.hasPrefix("172.20.10.") { return true }
            guard let ifc = route.split(separator: "\n").first(where: { $0.contains("interface:") })?.split(separator: " ").last else { return false }
            return shell("/usr/sbin/ipconfig", ["getsummary", String(ifc)]).contains("IsExpensive : TRUE")
        }.value
    }

    nonisolated private static func shell(_ path: String, _ args: [String]) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return "" }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return String(decoding: data, as: UTF8.self)
    }
}
