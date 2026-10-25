import PhotoCore
import SwiftUI

/// Backup: how folders are laid out in the archive and on Drive. Choose, preview, save.
/// Saving changes new backups only; files already backed up keep their folders.
struct LayoutCard: View {
    @ObservedObject var model: Model
    @State private var current: LayoutReport.Settings?
    @State private var draft: LayoutReport.Settings?
    @State private var preview: LayoutReport.Preview?
    @State private var working: String?
    @State private var message: (ok: Bool, text: String)?

    /// Each source has its own Drive folder (Settings, Drive), so every layout starts with it;
    /// the titles say what Drive shows inside that folder.
    static let presets: [(title: String, pattern: String)] = [
        ("Year / month or place", "{source}/{year}/{event}"),
        ("Year / month", "{source}/{year}/{month}"),
        ("Year / month / day", "{source}/{year}/{month}/{day}"),
    ]
    static let thresholds = [5, 10, 20, 50, 100]
    static let gaps = [14, 30, 45, 90]
    static let flatChoices: [(id: String, title: String)] = [("whatsapp", "WhatsApp"), ("screenshot", "Screenshots")]

    /// A backup reads the layout as it goes; changing it half way would split one run across two layouts.
    private var busy: Bool {
        model.jobs.contains { $0.state == .running } || model.tasks.contains { $0.running && $0.task.kind == .backup }
    }

    var body: some View {
        Card(title: "Folder layout", symbol: "folder.fill", tint: Tab.backup.color) {
            VStack(alignment: .leading, spacing: 12) {
                if let d = draft {
                    controls(d)
                    Divider()
                    previewView
                    actions(d)
                } else if let message {
                    Text(message.text).font(.callout).foregroundStyle(Theme.bad)
                } else {
                    ProgressView().controlSize(.small)
                }
            }
        }
        .task { await load() }
    }

    private func binding<T>(_ key: WritableKeyPath<LayoutReport.Settings, T>) -> Binding<T> {
        Binding(get: { draft![keyPath: key] }, set: { draft![keyPath: key] = $0; preview = nil; message = nil })
    }

    @ViewBuilder private func controls(_ d: LayoutReport.Settings) -> some View {
        let usesPlaces = d.pattern.contains("{event}")
        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
            GridRow {
                Text("Folders").frame(width: 130, alignment: .leading)
                Picker("", selection: binding(\.pattern)) {
                    ForEach(Self.presets, id: \.pattern) { Text($0.title).tag($0.pattern) }
                    if !Self.presets.contains(where: { $0.pattern == d.pattern }) { Text("Custom: \(d.pattern)").tag(d.pattern) }
                }
                .labelsHidden().fixedSize()
            }
            if usesPlaces {
                GridRow {
                    Text("Place gets a folder").frame(width: 130, alignment: .leading)
                    Picker("", selection: binding(\.minPhotos)) {
                        ForEach(Self.thresholds, id: \.self) { Text("from \($0) items").tag($0) }
                        if !Self.thresholds.contains(d.minPhotos) { Text("from \(d.minPhotos) items").tag(d.minPhotos) }
                    }
                    .labelsHidden().fixedSize()
                }
                GridRow {
                    Text("New visit after").frame(width: 130, alignment: .leading)
                    Picker("", selection: binding(\.gapDays)) {
                        ForEach(Self.gaps, id: \.self) { Text("\($0) days away").tag($0) }
                        if !Self.gaps.contains(d.gapDays) { Text("\(d.gapDays) days away").tag(d.gapDays) }
                    }
                    .labelsHidden().fixedSize()
                }
            }
            GridRow {
                Text("One folder for").frame(width: 130, alignment: .leading)
                HStack(spacing: 14) {
                    ForEach(Self.flatChoices, id: \.id) { c in
                        Toggle(c.title, isOn: Binding(
                            get: { draft?.flat.contains(c.id) ?? false },
                            set: { on in
                                draft?.flat.removeAll { $0 == c.id }
                                if on { draft?.flat.append(c.id) }
                                preview = nil; message = nil
                            }))
                    }
                }
            }
        }
        .font(.callout)
        Text(usesPlaces
             ? "A month folder (\"03\"), or a place folder (\"03 Cape Town\") once a place has enough items in one visit. Items in \"One folder for\" skip the tree."
             : "A folder per month. Items in \"One folder for\" skip the tree.")
            .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder private var previewView: some View {
        if let p = preview {
            VStack(alignment: .leading, spacing: 6) {
                Text("Your \(Fmt.count(p.assets)) backed-up items would sit in \(Fmt.count(p.folders)) folders, \(Fmt.count(p.placeFolders)) of them named after a place.")
                    .font(.callout.weight(.semibold))
                let rows = p.places.isEmpty ? p.biggest : p.places
                ForEach(rows.prefix(8), id: \.folder) { f in
                    HStack {
                        Image(systemName: "folder").foregroundStyle(.secondary)
                        Text(f.folder).lineLimit(1)
                        Spacer()
                        Text(Fmt.count(f.count)).foregroundStyle(.secondary).monospacedDigit()
                    }
                    .font(.caption)
                }
            }
        } else {
            Text("Preview to see the folders this makes from what is already backed up.")
                .font(.callout).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private func actions(_ d: LayoutReport.Settings) -> some View {
        HStack(spacing: 8) {
            CapsuleButton(title: "Preview", symbol: "eye.fill", color: Tab.backup.color, style: .soft) {
                Task { await run(d, apply: false) }
            }
            .disabled(working != nil)
            CapsuleButton(title: "Save for new backups", symbol: "checkmark", color: Tab.backup.color) {
                Task { await run(d, apply: true) }
            }
            .disabled(working != nil || d == current || busy)
            .help(busy ? "Wait for the backup under way to finish: changing folders half way would split it" : "")
            if let working { ProgressView().controlSize(.small); Text(working).font(.caption).foregroundStyle(.secondary) }
            Spacer()
            if d != current { Button("Undo changes") { draft = current; preview = nil; message = nil }.buttonStyle(.link).font(.caption) }
        }
        if let message {
            Label(message.text, systemImage: message.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .font(.caption).foregroundStyle(message.ok ? Theme.good : Theme.bad).fixedSize(horizontal: false, vertical: true)
        } else if busy && d != current {
            Text("A backup is running. Save once it finishes.").font(.caption).foregroundStyle(Theme.warn)
        }
        Text("Files already backed up stay in their folders; only new backups use a change.")
            .font(.caption).foregroundStyle(.secondary)
    }

    private func load() async {
        let engine = model.engine
        let result = await Task.detached { Result { try engine.layout() } }.value
        switch result {
        case .success(let r): current = r.current; draft = r.current
        case .failure(let e): message = (false, "Could not read the layout: \((e as? JobError)?.text ?? "\(e)")")
        }
    }

    private func run(_ d: LayoutReport.Settings, apply: Bool) async {
        working = apply ? "Saving…" : "Reading places from Photos…"
        message = nil
        let engine = model.engine
        let result = await Task.detached { Result { try engine.layout(d, preview: !apply, apply: apply) } }.value
        working = nil
        switch result {
        case .success(let r):
            if let p = r.preview { preview = p }
            if apply {
                current = r.proposed
                draft = r.proposed
                message = (true, "Saved. New backups use this layout.")
            }
        case .failure(let e):
            message = (false, (e as? JobError)?.text ?? "\(e)")
        }
    }
}
