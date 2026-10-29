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
    @State private var showOwnClient = false
    @State private var clientId = ""
    @State private var clientSecret = ""
    @State private var signingIn = false

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
                    if s.sharedClientId == true { clientIdHint(s) }
                    if changed(s) { pending(s) }
                    if let m = message {
                        Text(m.text).font(.callout).foregroundStyle(m.ok ? Theme.good : Theme.bad).fixedSize(horizontal: false, vertical: true)
                    }
                    Text("Every backup is checked by hash before anything leaves the phone. Google Drive and a folder on this Mac are checked by SHA-256, S3 by MD5. OneDrive and Dropbox report hashes of their own, so they are listed but cannot be picked yet. Items backed up before Photo Sentinel kept MD5 have none, so an S3 account never removes those from the phone.")
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

    /// rclone's built-in Google project is shared by every rclone user, so Google Drive throttles
    /// it. The owner makes their own client in Google Cloud (links below), pastes it here, and
    /// `config own-client` signs in with it in the browser. The account only changes if that
    /// reaches the same Google Drive.
    @ViewBuilder private func clientIdHint(_ s: StorageReport) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Uploads to \(s.accountTitle) are slowed down: it signs in through rclone's shared Google project, and Google Drive limits how fast everyone using it can upload. Your own sign-in, free, lifts that. About 10 minutes, once.",
                  systemImage: "tortoise.fill")
                .foregroundStyle(Theme.warn)
            if showOwnClient {
                ownClientSteps(s)
            } else {
                CapsuleButton(title: "Use my own Google sign-in", symbol: "person.badge.key.fill", color: Tab.backup.color) {
                    showOwnClient = true
                }
            }
        }
        .font(.callout).fixedSize(horizontal: false, vertical: true)
    }

    private static let ownClientSteps: [(title: String, how: String, url: String)] = [
        ("Make a Google Cloud project", "Any name, then Create. Sign in to Google Cloud with any Google account.", "https://console.cloud.google.com/projectcreate"),
        ("Turn on the Google Drive API", "Check the new project is picked at the top, then Enable.", "https://console.cloud.google.com/apis/library/drive.googleapis.com"),
        ("Set up its sign-in screen", "Get started: any app name, your email, Audience External, your email again, then Create.", "https://console.cloud.google.com/auth/branding"),
        ("Publish it", "Publish app, then Confirm. Skip this and Google signs you out every 7 days, which stops backups.", "https://console.cloud.google.com/auth/audience"),
        ("Make a client", "Application type Desktop app, any name, Create. Keep that window open: it shows the Client ID and Client secret.", "https://console.cloud.google.com/auth/clients/create"),
    ]

    @ViewBuilder private func ownClientSteps(_ s: StorageReport) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 8) {
                ForEach(Array(Self.ownClientSteps.enumerated()), id: \.offset) { i, step in
                    GridRow(alignment: .firstTextBaseline) {
                        Text("\(i + 1).").monospacedDigit().foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(step.title).fontWeight(.medium)
                            Text(step.how).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                        CapsuleButton(title: "Open", symbol: "arrow.up.right.square", color: Tab.backup.color, style: .soft) {
                            NSWorkspace.shared.open(URL(string: step.url)!)
                        }
                    }
                }
                GridRow(alignment: .firstTextBaseline) {
                    Text("6.").monospacedDigit().foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Paste them here and sign in").fontWeight(.medium)
                        TextField("Client ID", text: $clientId).textFieldStyle(.roundedBorder).font(.callout.monospaced())
                        SecureField("Client secret", text: $clientSecret).textFieldStyle(.roundedBorder).font(.callout.monospaced())
                        Text("Your browser opens: sign in as the Google account your backups are in. Google says it hasn't verified the app: it is your own, so click Continue. The account changes only if that sign-in reaches the same Google Drive.")
                            .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                    Color.clear.frame(width: 1, height: 1)
                }
            }
            HStack(spacing: 8) {
                CapsuleButton(title: signingIn ? "Waiting for the browser…" : "Sign in to Google", symbol: "person.badge.key.fill", color: Tab.backup.color) {
                    Task { await signIn() }
                }
                .disabled(signingIn || model.backupBusy || clientId.trimmingCharacters(in: .whitespaces).isEmpty || clientSecret.isEmpty)
                .help(model.backupBusy ? "Wait for the running backup to finish: signing in again cuts off its uploads." : "")
                if signingIn { ProgressView().controlSize(.small) }
                CapsuleButton(title: "Cancel", color: .secondary, style: .ghost) {
                    showOwnClient = false
                    clientSecret = ""
                }
                .disabled(signingIn)
            }
            if model.backupBusy { Text("Signing in waits until the running backup finishes: it would cut off its uploads.").foregroundStyle(.secondary) }
            Button("rclone's guide to this") { NSWorkspace.shared.open(URL(string: "https://rclone.org/drive/#making-your-own-client-id")!) }
                .buttonStyle(.link)
        }
    }

    private func signIn() async {
        signingIn = true
        message = nil
        let engine = model.engine
        let id = clientId.trimmingCharacters(in: .whitespacesAndNewlines)
        let secret = clientSecret.trimmingCharacters(in: .whitespacesAndNewlines)
        let result = await Task.detached { Result { try engine.ownClient(id: id, secret: secret) } }.value
        signingIn = false
        switch result {
        case .success:
            clientSecret = ""
            showOwnClient = false
            message = (true, "Done. Uploads to Google Drive now use your own sign-in. Nothing already backed up changes.")
            model.refresh()
        case .failure(let e):
            message = (false, (e as? JobError)?.text ?? "\(e)")
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
/// the moment the Mac joins a metered connection, unless the switch here is off.
struct WhenBackupsRunCard: View {
    @ObservedObject var model: Model
    @State private var metered: Bool?
    @State private var pause: Bool?
    @State private var error: String?

    var body: some View {
        Card(title: "When backups run", symbol: "wifi", tint: Tab.backup.color) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Image(systemName: "personalhotspot").foregroundStyle(pause == false ? Theme.warn : Theme.good)
                    Toggle("Pause on a phone hotspot", isOn: Binding(get: { pause ?? true }, set: { save($0) }))
                        .toggleStyle(.switch).font(.body.weight(.medium))
                        .disabled(pause == nil)
                    Spacer()
                    if let metered {
                        Text(metered ? (pause == false ? "Now: on a hotspot, backups carry on" : "Now: on a hotspot, backups are paused") : "Now: on normal Wi-Fi")
                            .font(.callout).foregroundStyle(metered ? Theme.warn : .secondary)
                    }
                }
                Text(pause == false
                     ? "Off: backups copy and upload on your iPhone's Personal Hotspot too, which uses its data. A running backup follows the switch within a minute."
                     : "Nothing copies or uploads on your iPhone's Personal Hotspot or any connection macOS marks as metered. Switching to one mid-upload stops the upload at once; it carries on by itself back on normal Wi-Fi. Checks and plans are small and still run.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if let error { Text(error).font(.callout).foregroundStyle(Theme.bad) }
            }
        }
        .task {
            metered = await Self.isMetered()
            let engine = model.engine
            switch await Task.detached(operation: { Result { try engine.pauseOnHotspot() } }).value {
            case .success(let on): pause = on
            case .failure(let e): error = "Could not read the setting: \((e as? JobError)?.text ?? "\(e)")"
            }
        }
    }

    private func save(_ on: Bool) {
        let engine = model.engine
        pause = on
        Task {
            switch await Task.detached(operation: { Result { try engine.pauseOnHotspot(on) } }).value {
            case .success(let saved): pause = saved; error = nil
            case .failure(let e): pause = !on; error = "Not saved: \((e as? JobError)?.text ?? "\(e)")"
            }
        }
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
