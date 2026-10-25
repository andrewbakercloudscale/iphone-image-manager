import AppKit
import PhotoCore
import SwiftUI

/// Review what was removed and get any of it back: from Recently Deleted for 30 days,
/// from Google Drive after that.
struct RecoverCard: View {
    @ObservedObject var model: Model
    let library: LibrarySnapshot
    @State private var search = ""
    @State private var rows: [ProofRow] = []
    @State private var picked: Set<Int> = []
    @State private var limit = 50

    private static let undoDays = 30.0

    private func inRecentlyDeleted(_ r: ProofRow) -> Bool {
        guard let d = ISO8601DateFormatter().date(from: r.removedAt) else { return false }
        return Date().timeIntervalSince(d) < Self.undoDays * 86_400
    }

    private var lastRecovery: TaskStatus? { model.latest(.recover) }

    var body: some View {
        Card(title: "Review and recover", symbol: "arrow.uturn.backward.circle.fill", tint: Tab.bin.color,
             trailing: AnyView(RowButton(title: "Open Photos", symbol: "photo.fill", color: Theme.good) { openPhotos() })) {
            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    Label("Removed in the last 30 days: in Photos, open Recently Deleted (iPhone: Albums › Recently Deleted; Mac: the sidebar), select, Recover.",
                          systemImage: "1.circle.fill")
                    Label("Older: pick them below and Download from Drive. Each file is checked against the fingerprint taken when it left the phone, then you can add it back to Photos.",
                          systemImage: "2.circle.fill")
                }
                .font(.callout).fixedSize(horizontal: false, vertical: true)

                HStack {
                    TextField("Search removed items by name", text: $search).textFieldStyle(.roundedBorder).frame(maxWidth: 320)
                    Spacer()
                    if !picked.isEmpty {
                        Text("\(picked.count) picked").font(.callout.weight(.semibold))
                        Button("Clear") { picked = [] }.buttonStyle(.link)
                        RowButton(title: "Download from Drive", symbol: "icloud.and.arrow.down.fill", color: Tab.backup.color, style: .fill) {
                            model.recover(Array(picked))
                            picked = []
                        }
                    }
                }
                recoveryStatus
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 4) {
                        ForEach(rows) { r in
                            HStack(spacing: 10) {
                                Toggle("", isOn: Binding(get: { picked.contains(r.assetId) },
                                                         set: { if $0 { picked.insert(r.assetId) } else { picked.remove(r.assetId) } }))
                                    .labelsHidden().toggleStyle(.checkbox)
                                Text(r.filename).lineLimit(1).frame(width: 220, alignment: .leading)
                                Text(String(r.removedAt.prefix(10))).foregroundStyle(.secondary).monospacedDigit()
                                Text(inRecentlyDeleted(r) ? "in Recently Deleted" : "in Drive only")
                                    .foregroundStyle(inRecentlyDeleted(r) ? Theme.good : Tab.backup.color)
                                    .frame(width: 140, alignment: .leading)
                                Spacer()
                                Text(Fmt.bytes(r.bytes)).monospacedDigit()
                            }
                            .font(.callout)
                            .help(r.drivePath)
                        }
                    }
                }
                .frame(height: 260)
                HStack {
                    Text("\(Fmt.count(rows.count)) shown of \(Fmt.count(library.removed.total.count)) removed").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if rows.count == limit { Button("Show more") { limit += 200 }.buttonStyle(.link) }
                }
            }
        }
        .task(id: "\(search)|\(limit)|\(library.readAt)") {
            try? await Task.sleep(for: .milliseconds(150))
            guard !Task.isCancelled else { return }
            rows = (try? Ledger.removed(SQLiteReader(path: model.ledgerPath), search: search, limit: limit)) ?? []
        }
    }

    @ViewBuilder private var recoveryStatus: some View {
        if let t = lastRecovery {
            if t.running {
                Running(text: "Downloading from Google Drive…", since: t.task.startedAt)
            } else if let r = t.decode(RecoverReport.self) {
                HStack(spacing: 8) {
                    Image(systemName: r.verified == r.files.count ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(r.verified == r.files.count ? Theme.good : Theme.warn)
                    Text("\(Fmt.count(r.verified)) of \(Fmt.count(r.files.count)) recovered and matched their fingerprint.")
                        .font(.callout.weight(.semibold))
                    Spacer()
                    RowButton(title: "Show in Finder", symbol: "folder.fill", color: Tab.backup.color) {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: r.folder)])
                    }
                    RowButton(title: "Add to Photos", symbol: "photo.badge.plus", color: Theme.good, style: .fill) {
                        addToPhotos(r.files.filter(\.verified).map(\.path))
                    }
                    .help("Opens the matched files with Photos, which imports them into your library and iCloud.")
                }
            } else if let e = t.errorMessage {
                Text("Recovery stopped: \(e)").font(.callout).foregroundStyle(Theme.bad)
            }
        }
    }

    private func openPhotos() {
        NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/Photos.app"))
    }

    private func addToPhotos(_ paths: [String]) {
        let photos = URL(fileURLWithPath: "/System/Applications/Photos.app")
        NSWorkspace.shared.open(paths.map { URL(fileURLWithPath: $0) }, withApplicationAt: photos, configuration: NSWorkspace.OpenConfiguration())
    }
}
