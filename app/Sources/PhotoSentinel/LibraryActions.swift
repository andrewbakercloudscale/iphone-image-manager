import PhotoCore
import SwiftUI

/// Library cards that act, not just show: every row leads to backing up, removing, or a
/// filter of its own on the Library tab.

/// A bar row with its actions on the right.
struct ActionRow<Actions: View>: View {
    let label: String
    let value: Double
    let text: String
    let color: Color
    var note: String?
    @ViewBuilder var actions: Actions

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(label).lineLimit(1)
                if let note { Text(note).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            }
            .frame(width: 210, alignment: .leading)
            // The buttons keep their full width; the meter gives way.
            Meter(value: value, color: color).frame(minWidth: 40)
            Text(text).fontWeight(.semibold).monospacedDigit().frame(width: 70, alignment: .trailing)
            HStack(spacing: 6) { actions }.fixedSize().frame(minWidth: 250, alignment: .trailing).layoutPriority(1)
        }
        .font(.callout)
    }
}

/// Small capsule buttons for rows.
struct RowButton: View {
    let title: String
    var symbol: String?
    var color: Color = Tab.cleanUp.color
    var style: CapsuleButton.Style = .soft
    let action: () -> Void

    var body: some View {
        CapsuleButton(title: title, symbol: symbol, color: color, style: style, action: action)
            .controlSize(.small)
            .fixedSize()
    }
}

struct CoverageCard: View {
    @ObservedObject var model: Model
    let library: LibrarySnapshot

    var body: some View {
        Card(title: "Safe in Google Drive", symbol: "checkmark.shield.fill", tint: Theme.good) {
            VStack(spacing: 10) {
                ForEach(library.channels) { c in
                    ActionRow(label: c.channel.title, value: c.coverage, text: Fmt.pct(c.coverage), color: Theme.good,
                              note: c.coverage >= 0.999 ? "all \(Fmt.count(c.total.count)) in Google Drive"
                                  : "\(Fmt.bytes(c.total.bytes - c.verified.bytes)) only on the phone"
                                    + (c.notBackedUpAge.map { ", \($0)" } ?? "")) {
                        if let source = c.channel.cliSource {
                            if c.coverage < 0.999 {
                                RowButton(title: "Back up all", symbol: "icloud.and.arrow.up.fill", color: Tab.backup.color) {
                                    model.backUp(Recipe(custom: Filter(name: c.channel.title, source: source)))
                                }
                            }
                            RowButton(title: "Filter", symbol: "line.3.horizontal.decrease") {
                                model.draft = Filter(source: source)
                                model.openBuilder()
                            }
                        }
                    }
                }
            }
        }
    }
}

struct BiggestVideosCard: View {
    @ObservedObject var model: Model
    let library: LibrarySnapshot
    @State private var picked: Set<Int> = []
    @State private var showAll = false

    private var rows: [BigItem] { Array(library.biggestVideos.prefix(showAll ? 20 : 8)) }

    var body: some View {
        let chosen = library.biggestVideos.filter { picked.contains($0.id) }
        Card(title: "Biggest videos", symbol: "video.fill", tint: Tab.backup.color,
             trailing: AnyView(Button(showAll ? "Show fewer" : "Show 20") { showAll.toggle() }.buttonStyle(.link))) {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(rows) { v in
                    HStack(spacing: 10) {
                        Toggle("", isOn: Binding(get: { picked.contains(v.id) },
                                                 set: { if $0 { picked.insert(v.id) } else { picked.remove(v.id) } }))
                            .labelsHidden().toggleStyle(.checkbox)
                        Text(v.filename).lineLimit(1)
                        Text(String(v.created.prefix(7))).foregroundStyle(.secondary)
                        Spacer()
                        Text(Fmt.bytes(v.bytes)).fontWeight(.semibold).monospacedDigit()
                        Text(v.inDrive ? "in Google Drive" : "phone only").foregroundStyle(v.inDrive ? Theme.good : Theme.warn).frame(width: 78, alignment: .trailing)
                        if v.inDrive {
                            RowButton(title: "Delete from iPhone…", symbol: "iphone.and.arrow.forward", style: .fill) { remove([v]) }
                        } else {
                            RowButton(title: "Back up", symbol: "icloud.and.arrow.up.fill", color: Tab.backup.color) { backUp([v]) }
                        }
                    }
                    .font(.callout)
                }
                if !chosen.isEmpty {
                    Divider()
                    HStack {
                        Text("\(chosen.count) picked · \(Fmt.bytes(chosen.reduce(0) { $0 + $1.bytes }))").font(.callout.weight(.semibold))
                        Spacer()
                        Button("Clear") { picked = [] }.buttonStyle(.link)
                        if chosen.contains(where: { !$0.inDrive }) {
                            RowButton(title: "Back up picked", symbol: "icloud.and.arrow.up.fill", color: Tab.backup.color) {
                                backUp(chosen.filter { !$0.inDrive })
                            }
                        }
                        RowButton(title: "Delete picked from iPhone…", symbol: "iphone.and.arrow.forward", style: .fill) { remove(chosen) }
                    }
                }
                Text("Back up copies a video off the phone, uploads it and checks it in Google Drive. Remove only ever takes what Google Drive has checked.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func name(_ items: [BigItem]) -> String {
        items.count == 1 ? items[0].filename : "\(items.count) big videos"
    }

    private func backUp(_ items: [BigItem]) {
        model.backUpItems(items.map(\.id), bytes: items.reduce(0) { $0 + $1.bytes }, label: name(items))
        picked.subtract(items.map(\.id))
    }

    private func remove(_ items: [BigItem]) {
        model.removing = Recipe(custom: Filter(name: name(items), ids: items.map(\.id)))
    }
}

struct PlacesCard: View {
    @ObservedObject var model: Model
    let library: LibrarySnapshot
    @State private var open: Set<String> = []

    var body: some View {
        let running = model.tasks.contains { $0.running && $0.task.kind == .places }
        Card(title: "By location", symbol: "mappin.and.ellipse", tint: Tab.library.color,
             trailing: running ? AnyView(ProgressView().controlSize(.small))
                 : AnyView(RowButton(title: library.places.isEmpty ? "Look up places" : "Update", symbol: "arrow.clockwise", color: Tab.library.color) { model.runPlaces() })) {
            VStack(alignment: .leading, spacing: 8) {
                if library.places.isEmpty {
                    Text(running ? "Reading place names from Photos (about 10 seconds)…"
                         : "Reads where each photo was taken from Photos on this Mac. Nothing leaves the Mac.")
                        .font(.callout).foregroundStyle(.secondary)
                } else {
                    let top = Double(library.places.first?.total.bytes ?? 1)
                    ForEach(library.places.prefix(12)) { p in
                        ActionRow(label: p.country, value: Double(p.total.bytes) / max(top, 1), text: Fmt.bytes(p.total.bytes),
                                  color: Tab.library.color,
                                  note: "\(Fmt.count(p.total.count)) items · \(Fmt.pct(p.coverage)) in Google Drive") {
                            if p.cities.count > 1 {
                                RowButton(title: open.contains(p.id) ? "Hide cities" : "\(p.cities.count) cities",
                                          symbol: open.contains(p.id) ? "chevron.up" : "chevron.down", color: Tab.library.color, style: .ghost) {
                                    if open.contains(p.id) { open.remove(p.id) } else { open.insert(p.id) }
                                }
                            }
                            RowButton(title: "Clean up", symbol: "line.3.horizontal.decrease") { model.showPlace(country: p.country) }
                        }
                        if open.contains(p.id) {
                            ForEach(p.cities.prefix(15)) { c in
                                ActionRow(label: "    " + c.city, value: Double(c.total.bytes) / max(top, 1), text: Fmt.bytes(c.total.bytes),
                                          color: Tab.library.color.opacity(0.55),
                                          note: "    \(Fmt.count(c.total.count)) items · \(Fmt.pct(c.coverage)) in Google Drive") {
                                    RowButton(title: "Clean up", symbol: "line.3.horizontal.decrease") { model.showPlace(country: p.country, city: c.city) }
                                }
                            }
                        }
                    }
                    Text("\(Fmt.bytes(library.unplaced.bytes)) (\(Fmt.count(library.unplaced.count)) items) has no place: WhatsApp images, screenshots and recordings carry no location.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

struct AppsCard: View {
    @ObservedObject var model: Model
    let library: LibrarySnapshot

    var body: some View {
        let top = Double(library.apps.first?.tally.bytes ?? 1)
        Card(title: "Apps that fill your library", symbol: "square.grid.2x2.fill", tint: Theme.good) {
            VStack(spacing: 10) {
                ForEach(library.apps, id: \.bundle) { a in
                    ActionRow(label: Fmt.app(a.bundle), value: Double(a.tally.bytes) / max(top, 1), text: Fmt.bytes(a.tally.bytes),
                              color: Theme.series[2], note: "\(Fmt.count(a.tally.count)) items") {
                        AppBackupButton(model: model, bundle: a.bundle)
                        RowButton(title: "Clean up", symbol: "line.3.horizontal.decrease") {
                            model.draft = Filter(source: a.bundle)
                            model.openBuilder()
                        }
                    }
                }
            }
        }
    }
}

/// An app row's Back up. The row itself says the backup took: the notice up top is
/// off screen down here, so a click that only set it looked like nothing happened.
struct AppBackupButton: View {
    @ObservedObject var model: Model
    let bundle: String

    var body: some View {
        let state = Jobs.rowState(source: bundle, jobs: model.jobs, queuedSources: model.queue.map { $0.source })
        if state == .running {
            RowButton(title: "Backing up: see Activity", symbol: "waveform.path.ecg", color: Tab.activity.color) {
                model.tab = .activity
            }
        } else if state == .queued {
            Pill(text: "Queued", symbol: "clock.fill", color: Theme.warn)
        } else {
            RowButton(title: "Back up", symbol: "icloud.and.arrow.up.fill", color: Tab.backup.color) {
                model.backUp(Recipe(custom: Filter(name: Fmt.app(bundle), source: bundle)))
                // Starting it shows on Activity; stay here only when it waits in the queue.
                if !model.queue.contains(where: { $0.source == bundle }) { model.tab = .activity }
            }
        }
    }
}
