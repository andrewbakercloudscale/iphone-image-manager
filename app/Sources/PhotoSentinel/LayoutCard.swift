import PhotoCore
import SwiftUI

/// Backup: how backups are stored. Per source, which Drive folder and which folder layout;
/// when a place earns its own folder. Preview, save, then move what is already in Drive.
struct LayoutCard: View {
    @ObservedObject var model: Model
    @State private var current: LayoutReport.Settings?
    @State private var draft: LayoutReport.Settings?
    @State private var preview: LayoutReport.Preview?
    @State private var refile: RefileReport?
    @State private var working: String?
    @State private var message: (ok: Bool, text: String)?

    /// Layouts inside a source's Drive folder. Every pattern starts with {source}: upload strips
    /// that level to reach the source's own folder.
    static let layouts: [(title: String, pattern: String)] = [
        ("Year / month or place", "{source}/{year}/{event}"),
        ("Year / month", "{source}/{year}/{month}"),
        ("Year / month / day", "{source}/{year}/{month}/{day}"),
        ("Year", "{source}/{year}"),
    ]
    static let flatTag = "flat"
    static let sameTag = "same"
    static let thresholds = [5, 10, 20, 50, 100]
    static let gaps = [14, 30, 45, 90]

    /// The sources with a Drive folder of their own, and the channel that names their layout.
    static let sources: [(kind: String, channel: String?, title: String, symbol: String)] = [
        ("photos", nil, "Photos", "camera.fill"),
        ("videos", nil, "Videos", "video.fill"),
        ("screenshots", "screenshot", "Screenshots", "camera.viewfinder"),
        ("whatsapp", "whatsapp", "WhatsApp", "message.fill"),
    ]

    /// Re-filing is per channel; photos and videos share the main layout and are not re-filed.
    static let refileSources = ["whatsapp", "screenshot"]

    /// A backup reads these settings as it goes; changing them half way would split one run.
    private var busy: Bool {
        model.jobs.contains { $0.state == .running }
            || model.tasks.contains { $0.running && ($0.task.kind == .backup || $0.task.kind == .refile) }
    }

    var body: some View {
        Card(title: "How backups are stored", symbol: "folder.fill", tint: Tab.backup.color) {
            VStack(alignment: .leading, spacing: 12) {
                if let d = draft {
                    sourcesGrid(d)
                    placeControls(d)
                    Divider()
                    previewView
                    actions(d)
                    refileView
                } else if let message {
                    Text(message.text).font(.callout).foregroundStyle(Theme.bad)
                } else {
                    ProgressView().controlSize(.small)
                }
            }
        }
        .task { await load() }
    }

    private func edited() { preview = nil; message = nil }

    // MARK: Per source

    private func layoutTag(_ d: LayoutReport.Settings, channel: String?) -> String {
        guard let channel else { return d.pattern }
        if d.flat.contains(channel) { return Self.flatTag }
        return d.sourcePatterns[channel] ?? Self.sameTag
    }

    private func setLayout(_ tag: String, channel: String?) {
        guard var d = draft else { return }
        if let channel {
            d.flat.removeAll { $0 == channel }
            d.sourcePatterns[channel] = nil
            if tag == Self.flatTag { d.flat.append(channel) }
            else if tag != Self.sameTag { d.sourcePatterns[channel] = tag }
        } else {
            d.pattern = tag
        }
        draft = d
        edited()
    }

    private func destinationBinding(_ kind: String) -> Binding<String> {
        Binding(get: { draft?.destinations[kind] ?? "" },
                set: { draft?.destinations[kind] = $0; edited() })
    }

    @ViewBuilder private func sourcesGrid(_ d: LayoutReport.Settings) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
            GridRow {
                Text("SOURCE")
                Text("GOOGLE DRIVE FOLDER")
                Text("FOLDERS INSIDE IT")
            }
            .font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
            ForEach(Self.sources, id: \.kind) { s in
                GridRow {
                    Label(s.title, systemImage: s.symbol).font(.callout.weight(.medium)).lineLimit(1)
                    TextField(s.kind == "photos" ? "Required" : "With the photos", text: destinationBinding(s.kind))
                        .textFieldStyle(.roundedBorder).font(.callout).frame(minWidth: 240)
                    if s.kind == "videos" {
                        Text("Same as photos").font(.callout).foregroundStyle(.secondary)
                    } else {
                        let tag = layoutTag(d, channel: s.channel)
                        Picker("", selection: Binding(get: { tag }, set: { setLayout($0, channel: s.channel) })) {
                            if s.channel != nil {
                                Text("Same as photos").tag(Self.sameTag)
                                Text("One folder").tag(Self.flatTag)
                            }
                            ForEach(Self.layouts, id: \.pattern) { Text($0.title).tag($0.pattern) }
                            if ![Self.sameTag, Self.flatTag].contains(tag), !Self.layouts.contains(where: { $0.pattern == tag }) {
                                Text("Custom: \(tag)").tag(tag)
                            }
                        }
                        .labelsHidden().fixedSize()
                    }
                }
            }
        }
        Text("Folders start at the top of your Google Drive. Leave one empty to keep that source with the photos. Videos use the photos' layout.")
            .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder private func placeControls(_ d: LayoutReport.Settings) -> some View {
        let patterns = [d.pattern] + Array(d.sourcePatterns.values)
        if patterns.contains(where: { $0.contains("{event}") }) {
            HStack(spacing: 8) {
                Text("A place gets its own folder from").font(.callout)
                Picker("", selection: Binding(get: { d.minPhotos }, set: { draft?.minPhotos = $0; edited() })) {
                    ForEach(Set(Self.thresholds + [d.minPhotos]).sorted(), id: \.self) { Text("\($0) items").tag($0) }
                }
                .labelsHidden().fixedSize()
                Text("in one visit. A new visit starts after").font(.callout)
                Picker("", selection: Binding(get: { d.gapDays }, set: { draft?.gapDays = $0; edited() })) {
                    ForEach(Set(Self.gaps + [d.gapDays]).sorted(), id: \.self) { Text("\($0) days away").tag($0) }
                }
                .labelsHidden().fixedSize()
            }
            Text("\"Month or place\" makes a month folder (\"03\"), or a place folder (\"03 Cape Town\") once a place has enough items.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Preview, save, move

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
            if d != current { Button("Undo changes") { draft = current; edited() }.buttonStyle(.link).font(.caption) }
        }
        if let message {
            Label(message.text, systemImage: message.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .font(.caption).foregroundStyle(message.ok ? Theme.good : Theme.bad).fixedSize(horizontal: false, vertical: true)
        } else if busy && d != current {
            Text("A backup is running. Save once it finishes.").font(.caption).foregroundStyle(Theme.warn)
        }
    }

    /// After a save: what is already in Drive under the old layout, and a button to move it.
    @ViewBuilder private var refileView: some View {
        if let r = refile, r.toMove > 0 {
            VStack(alignment: .leading, spacing: 6) {
                Text("\(Fmt.count(r.toMove)) files already in Google Drive are in the old place.")
                    .font(.callout.weight(.semibold))
                ForEach(r.examples.prefix(2), id: \.self) { e in
                    Text("\(e.from)\n→ \(e.to)").font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(2)
                }
                ForEach(r.skipped.sorted(by: { $0.key < $1.key }), id: \.key) { reason, n in
                    Text("\(Fmt.count(n)) stay where they are: \(reason).").font(.caption).foregroundStyle(.secondary)
                }
                HStack {
                    CapsuleButton(title: "Move them in Google Drive", symbol: "arrow.right.doc.on.clipboard", color: Tab.backup.color) {
                        model.refile(Self.refileSources)
                        refile = nil
                    }
                    .disabled(busy)
                    .help(busy ? "Wait for the backup under way to finish" : "Each file is found by its fingerprint at the new place before the ledger changes")
                    Text("Moves inside Google Drive; nothing is re-uploaded. Progress is in Activity.").font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 8).fill(Tab.backup.color.opacity(0.08)))
        } else {
            Text("Saving changes new backups. If files already in Google Drive are then in the old place, a button here moves them.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func load() async {
        let engine = model.engine
        let result = await Task.detached { Result { try engine.layout() } }.value
        switch result {
        case .success(let r): current = r.current; draft = r.current
        case .failure(let e): message = (false, "Could not read the layout: \((e as? JobError)?.text ?? "\(e)")")
        }
        await checkRefile()
    }

    private func checkRefile() async {
        let engine = model.engine
        let sources = Self.refileSources
        refile = await Task.detached { try? engine.refilePlan(sources) }.value
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
                message = (true, "Saved. New backups use this.")
                await checkRefile()
            }
        case .failure(let e):
            message = (false, (e as? JobError)?.text ?? "\(e)")
        }
    }
}
