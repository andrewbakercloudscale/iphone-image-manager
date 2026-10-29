import Charts
import PhotoCore
import SwiftUI

/// Opens from a Checkup tile: how its number is made, what it is part of, and the same
/// items by source, by kind and over time.
struct StatDetailSheet: View {
    @ObservedObject var model: Model
    let stat: StatDetail.Stat
    /// `--snapshot` passes the detail in and turns scrolling off: ImageRenderer draws nothing in a ScrollView.
    var preset: StatDetail? = nil
    var scrolls = true
    @Environment(\.dismiss) private var dismiss
    @State private var loaded: StatDetail?
    private var detail: StatDetail? { preset ?? loaded }
    @State private var failed: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                SymbolTile(symbol: symbol, color: color, size: 32)
                Text(title).font(.title2.weight(.bold))
                Spacer()
                Button("Close") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            .padding(18)
            Divider()
            if scrolls { ScrollView { inner } } else { inner }
        }
        .frame(minWidth: 680, idealWidth: 820, minHeight: 560, idealHeight: 760)
        .task { await load() }
    }

    @ViewBuilder private var inner: some View {
        if let detail {
            content(detail).padding(18)
        } else if let failed {
            Callout(text: failed).padding(18)
        } else {
            ProgressView().padding(40).frame(maxWidth: .infinity)
        }
    }

    private func load() async {
        guard preset == nil else { return }
        let path = model.ledgerPath, stat = stat
        let result = await Task.detached { Result { try Ledger.statDetail(SQLiteReader(path: path), stat) } }.value
        switch result {
        case .success(let d): loaded = d
        case .failure(let e): failed = "Could not read the ledger: \(e)"
        }
    }

    @ViewBuilder private func content(_ d: StatDetail) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text(headline(d)).font(.system(size: 30, weight: .bold)).monospacedDigit()
                Text(rule).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }

            ForEach(d.parts) { p in PartsBar(parts: p, color: partColor) }

            if d.total.count > 0 {
                Card(title: "What it is made of", symbol: "chart.pie.fill", tint: color) {
                    HStack(alignment: .top, spacing: 18) {
                        Donut(title: "From", slices: d.bySource, total: d.total,
                              color: { Theme.color(Channel(rawValue: $0) ?? .unattributed) }, onPick: nil)
                        Donut(title: "Kind", slices: d.byKind, total: d.total, color: Donuts.kindColor, onPick: nil)
                    }
                }
                Card(title: timeTitle, symbol: "chart.bar.fill", tint: color) {
                    TimeBars(slices: d.overTime, color: color, label: label)
                }
            }

            if let note = note(d) {
                Label(note, systemImage: "info.circle").font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: words per tile

    private var title: String {
        switch stat {
        case .verified: "Checked safe in Google Drive"
        case .freed: "Freed on the phone"
        case .recentlyDeleted: "In Recently Deleted"
        }
    }

    private var symbol: String {
        switch stat {
        case .verified: "checkmark.shield.fill"
        case .freed: "iphone"
        case .recentlyDeleted: "arrow.uturn.backward"
        }
    }

    private var color: Color {
        switch stat {
        case .verified: Theme.good
        case .freed: Tab.backup.color
        case .recentlyDeleted: Theme.warn
        }
    }

    private func headline(_ d: StatDetail) -> String {
        switch stat {
        case .recentlyDeleted: "\(Fmt.count(d.total.count)) items · \(Fmt.bytes(d.total.bytes))"
        default: "\(Fmt.bytes(d.total.bytes)) · \(Fmt.count(d.total.count)) items"
        }
    }

    private var rule: String {
        switch stat {
        case .verified:
            "Items still on your iPhone whose Google Drive copy was downloaded and matched the original's SHA-256 fingerprint, byte for byte. Only these can ever be removed from the phone."
        case .freed:
            "The size of every item Photo Sentinel removed from your iPhone, added up. Each one was checked in Google Drive first, and the phone was re-checked just before."
        case .recentlyDeleted:
            "Items Photo Sentinel removed in the last 30 days. iPhone keeps deleted items in Recently Deleted for 30 days, so these can still be put back from the Photos app."
        }
    }

    private var timeTitle: String {
        switch stat {
        case .verified: "When each was checked"
        case .freed: "When each was removed, by month"
        case .recentlyDeleted: "When each was removed, by day"
        }
    }

    private func note(_ d: StatDetail) -> String? {
        switch stat {
        case .verified:
            "Removed items drop out of this number and into Freed on the phone. They stay in Google Drive."
        case .freed:
            "This counts removals, not space you can see in iPhone Storage yet: items in Recently Deleted still use space until it empties itself after 30 days, or you empty it."
        case .recentlyDeleted:
            (d.firstExpiry.flatMap(Fmt.dateOnly).map { "The first of these leave Recently Deleted for good on \($0). " } ?? "")
                + "If you emptied Recently Deleted on the phone, they are already gone; Photo Sentinel cannot see that. Every one is still in Google Drive."
        }
    }

    private func partColor(_ key: String) -> Color {
        switch key {
        case "this": color
        case "todo": Theme.warn
        case "recent": Theme.warn
        default: Theme.other
        }
    }

    private func label(_ key: String) -> String {
        let parts = key.split(separator: "-")
        let months = Calendar.current.shortMonthSymbols
        switch parts.count {
        case 2: if let m = Int(parts[1]), (1...12).contains(m) { return "\(months[m - 1]) \(parts[0].suffix(2))" }
        case 3: if let m = Int(parts[1]), (1...12).contains(m) { return "\(Int(parts[2]) ?? 0) \(months[m - 1])" }
        default: break
        }
        return key.isEmpty ? "Unknown" : key
    }
}

/// One whole split into its parts, as a single bar with a legend underneath.
private struct PartsBar: View {
    let parts: StatDetail.Parts
    let color: (String) -> Color

    var body: some View {
        let total = max(parts.total.bytes, 1)
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(parts.title.uppercased()).sectionLabel()
                Spacer()
                Text("\(Fmt.bytes(parts.total.bytes)) · \(Fmt.count(parts.total.count)) items").font(.callout).foregroundStyle(.secondary).monospacedDigit()
            }
            GeometryReader { g in
                HStack(spacing: 2) {
                    ForEach(parts.slices) { s in
                        RoundedRectangle(cornerRadius: 4).fill(color(s.key))
                            .frame(width: max(s.tally.bytes > 0 ? 3 : 0, g.size.width * CGFloat(s.tally.bytes) / CGFloat(total)))
                    }
                }
            }
            .frame(height: 16)
            HStack(spacing: 16) {
                ForEach(parts.slices) { s in
                    HStack(spacing: 6) {
                        RoundedRectangle(cornerRadius: 2).fill(color(s.key)).frame(width: 10, height: 10)
                        Text(s.title)
                        Text("\(Fmt.bytes(s.tally.bytes)) · \(Fmt.count(s.tally.count))").fontWeight(.semibold).monospacedDigit()
                        Text(Fmt.pct(Double(s.tally.bytes) / Double(total))).foregroundStyle(.secondary).monospacedDigit()
                    }
                    .font(.callout)
                }
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.cardFill))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.cardStroke, lineWidth: 1.5))
    }
}

private struct TimeBars: View {
    let slices: [Breakdown.Slice]
    let color: Color
    let label: (String) -> String

    var body: some View {
        Chart(slices) { s in
            BarMark(x: .value("When", s.key), y: .value("GB", Double(s.tally.bytes) / 1_000_000_000), width: slices.count < 8 ? .fixed(56) : .automatic)
                .foregroundStyle(color)
                .annotation(position: .top) {
                    if slices.count <= 12 { Text(Fmt.bytes(s.tally.bytes)).font(.caption2).foregroundStyle(.secondary) }
                }
        }
        .chartXScale(domain: slices.map(\.key))
        .chartXAxis {
            let step = max(1, Int((Double(slices.count) / 12).rounded(.up)))
            AxisMarks(values: slices.enumerated().filter { $0.offset % step == 0 }.map(\.element.key)) { v in
                AxisValueLabel { if let k = v.as(String.self) { Text(label(k)).fixedSize() } }
            }
        }
        .chartYAxis { AxisMarks { v in
            AxisGridLine()
            AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.bytes(Int64(d * 1_000_000_000))) } }
        } }
        .frame(height: 200)
    }
}
