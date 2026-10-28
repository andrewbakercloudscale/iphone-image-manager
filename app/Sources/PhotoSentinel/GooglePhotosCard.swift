import AppKit
import PhotoCore
import SwiftUI

/// Settings: an extra copy of backups in Google Photos, for viewing. Google Drive stays the
/// only backup: nothing leaves the Mac or the phone on the strength of a Google Photos copy.
/// Nothing is sent unless the owner clicks Send or turns on a source to follow its backups.
struct GooglePhotosCard: View {
    @ObservedObject var model: Model
    @State private var folders: [GooglePhotosFolder]?
    @State private var showFolders = false
    /// The plan shown before a send starts: what, how much, into which album.
    @State private var pending: Pending?
    @State private var planning: String?
    @State private var message: (ok: Bool, text: String)?
    @State private var settingUp = false

    struct Pending {
        let key: String
        let title: String
        let source: String?
        let folder: String?
        let plan: GooglePhotosSend
    }

    private var sending: TaskStatus? { model.tasks.first { $0.running && $0.task.kind == .googlePhotos } }

    var body: some View {
        Card(title: "Google Photos", symbol: "photo.on.rectangle.angled", tint: Tab.backup.color) {
            VStack(alignment: .leading, spacing: 12) {
                if let g = model.googlePhotos {
                    if g.configured { ready(g) } else { notSetUp(g) }
                } else {
                    Text("Reading the Google Photos settings…").font(.callout).foregroundStyle(.secondary)
                }
                if let m = message {
                    Text(m.text).font(.callout).foregroundStyle(m.ok ? Theme.good : Theme.bad).fixedSize(horizontal: false, vertical: true)
                }
                Text("An extra copy for viewing, never a backup: nothing is removed from the iPhone or the Mac because it is in Google Photos, only because it is in Google Drive. Uploads are at original quality and use your Google storage. Google Photos lets Photo Sentinel see only what it uploaded itself, not the rest of your library.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: Not set up

    @ViewBuilder private func notSetUp(_ g: GooglePhotosStatus) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Also send backups to Google Photos, so you can see them there. Google Drive stays the backup.")
                .font(.callout).fixedSize(horizontal: false, vertical: true)
            if !g.remote.isEmpty {
                Label("\(g.remote) is no longer in rclone. Set it up again.", systemImage: "exclamationmark.triangle.fill")
                    .font(.callout).foregroundStyle(Theme.warn)
            }
            HStack(spacing: 8) {
                CapsuleButton(title: "Set up Google Photos", symbol: "person.badge.key.fill", color: Tab.backup.color) {
                    Self.openSetup(repo: model.repoPath)
                    settingUp = true
                }
                .help("Opens Terminal. rclone opens your browser to sign in to Google and keeps the credentials; Photo Sentinel never sees them.")
                if settingUp {
                    CapsuleButton(title: "Check again", symbol: "arrow.clockwise", color: Tab.backup.color, style: .soft) {
                        model.refreshGooglePhotos()
                    }
                }
            }
            if settingUp {
                Text("Sign in in your browser, then come back and click Check again.").font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: Set up

    @ViewBuilder private func ready(_ g: GooglePhotosStatus) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.good)
                Text("\(g.account?.name ?? g.remote) · Google Photos").font(.callout.weight(.medium))
                Text("· \(Fmt.count(g.sent)) sent (\(Fmt.bytes(g.sentBytes))) · albums start \"\(g.album)\"")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 8) {
                ForEach(g.sources) { s in sourceRow(s) }
            }
            .font(.callout)
            if let p = pending { confirm(p) }
            if let t = sending {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text((t.progressText ?? "Getting ready") + "…").font(.callout).monospacedDigit()
                }
            }
            folderPicker
        }
    }

    private func sourceRow(_ s: GooglePhotosStatus.Source) -> some View {
        GridRow {
            Text(s.title).font(.callout.weight(.medium))
            Toggle("Also send new backups", isOn: Binding(get: { s.following }, set: { follow(s.source, $0) }))
                .toggleStyle(.switch).controlSize(.small)
                .help("New backups of \(s.title) go to Google Photos once Google Drive has checked them, before the Mac copy is tidied. Older backups go only when you click Send.")
            Text("\(Fmt.count(s.sent)) sent · \(Fmt.count(s.notSent)) not yet (\(Fmt.bytes(s.notSentBytes)))")
                .foregroundStyle(.secondary).monospacedDigit()
            if s.notSent > 0 {
                CapsuleButton(title: planning == s.source ? "Checking…" : "Send \(Fmt.count(s.notSent)) (\(Fmt.bytes(s.notSentBytes)))",
                              symbol: "paperplane.fill", color: Tab.backup.color, style: .soft) {
                    Task { await plan(key: s.source, title: s.title, source: s.source, folder: nil) }
                }
                .disabled(planning != nil || sending != nil)
            } else {
                Text(s.sent > 0 ? "All in Google Photos" : "").foregroundStyle(.secondary)
            }
        }
    }

    /// The plan, read from the ledger without sending anything; Send starts it as a task.
    private func confirm(_ p: Pending) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            let albums = p.plan.albums.map { "\"\($0.album)\"" }.joined(separator: ", ")
            Label("Send \(Fmt.count(p.plan.planned)) items (\(Fmt.bytes(p.plan.plannedBytes))) from \(p.title) to Google Photos, album \(albums). \(Fmt.count(p.plan.fromMac)) from this Mac, \(Fmt.count(p.plan.fromDrive)) read from Google Drive. This uses \(Fmt.bytes(p.plan.plannedBytes)) of your Google storage.",
                  systemImage: "paperplane.fill")
            HStack(spacing: 8) {
                CapsuleButton(title: "Send", symbol: "checkmark", color: Tab.backup.color) {
                    model.sendToGooglePhotos(source: p.source, folder: p.folder, title: p.title)
                    pending = nil
                    message = (true, "Sending. It shows in Activity, and carries on if you close the app.")
                }
                .disabled(p.plan.planned == 0 || sending != nil)
                CapsuleButton(title: "Cancel", color: .secondary, style: .ghost) { pending = nil }
            }
        }
        .font(.callout).fixedSize(horizontal: false, vertical: true)
    }

    // MARK: Folders

    @ViewBuilder private var folderPicker: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button(showFolders ? "Hide folders" : "Pick a Google Drive folder…") {
                showFolders.toggle()
                if showFolders && folders == nil { loadFolders() }
            }
            .buttonStyle(.link)
            if showFolders {
                if let folders {
                    if folders.isEmpty { Text("Nothing is in Google Drive yet.").font(.callout).foregroundStyle(.secondary) }
                    ScrollView {
                        Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
                            ForEach(folders) { f in folderRow(f) }
                        }
                        .font(.callout)
                    }
                    .frame(maxHeight: 260)
                } else {
                    ProgressView().controlSize(.small)
                }
            }
        }
    }

    private func folderRow(_ f: GooglePhotosFolder) -> some View {
        GridRow {
            Text(f.label).font(.callout.monospaced()).padding(.leading, CGFloat(f.depth) * 14).lineLimit(1).truncationMode(.middle)
                .help(f.path)
            Text("\(Fmt.count(f.count)) · \(Fmt.count(f.sent)) sent · \(Fmt.count(f.notSent)) not yet (\(Fmt.bytes(f.notSentBytes)))")
                .foregroundStyle(.secondary).monospacedDigit()
            if f.notSent > 0 {
                Button(planning == f.path ? "Checking…" : "Send") {
                    Task { await plan(key: f.path, title: f.label, source: nil, folder: f.path) }
                }
                .disabled(planning != nil || sending != nil)
            } else {
                Text("")
            }
        }
    }

    // MARK: Actions

    private func loadFolders() {
        let engine = model.engine
        Task {
            switch await Task.detached(operation: { Result { try engine.googlePhotosFolders() } }).value {
            case .success(let f): folders = f
            case .failure(let e): folders = []; message = (false, "Could not read the folders: \((e as? JobError)?.text ?? "\(e)")")
            }
        }
    }

    private func plan(key: String, title: String, source: String?, folder: String?) async {
        planning = key
        message = nil
        let engine = model.engine
        let result = await Task.detached { Result { try engine.googlePhotosPlan(source: source, folder: folder) } }.value
        planning = nil
        switch result {
        case .success(let p): pending = Pending(key: key, title: title, source: source, folder: folder, plan: p)
        case .failure(let e): message = (false, "Could not plan it: \((e as? JobError)?.text ?? "\(e)")")
        }
    }

    private func follow(_ source: String, _ on: Bool) {
        let engine = model.engine
        Task {
            switch await Task.detached(operation: { Result { try engine.googlePhotosFollow(source, on) } }).value {
            case .success:
                message = (true, on ? "Saved. New backups of it also go to Google Photos." : "Saved. New backups stop going to Google Photos.")
                model.refreshGooglePhotos()
            case .failure(let e): message = (false, "Not saved: \((e as? JobError)?.text ?? "\(e)")")
            }
        }
    }

    /// Terminal, because signing in needs a browser and rclone's own prompts: run detached,
    /// a question nobody can see would wait forever. StorageCard adds accounts the same way.
    static func openSetup(repo: String) {
        let command = CLIEngine.googlePhotosSetupCommand(repo: repo)
            .replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        let script = "tell application \"Terminal\" to activate\ntell application \"Terminal\" to do script \"\(command)\""
        NSAppleScript(source: script)?.executeAndReturnError(nil)
    }
}
