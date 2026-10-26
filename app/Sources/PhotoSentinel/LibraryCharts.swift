import Charts
import PhotoCore
import SwiftUI

/// The Library's summary: everything on the phone and the rule builder's selection, each as
/// donuts by source, kind and Drive status, then by year and by month.
struct MediaCharts: View {
    @ObservedObject var model: Model
    @AppStorage("timelineUnit") private var unit: TimeUnit = .year
    @AppStorage("timelineSpan") private var span: TimeSpan = .all

    /// The bars for the chosen unit and span. The span counts back from today, so an
    /// empty recent month still has a place.
    private func periods(_ b: Breakdown) -> [Breakdown.Period] {
        let rows = unit == .year ? b.byYear : b.byMonth
        guard let years = span.years else { return rows }
        let cutoff = Calendar.current.date(byAdding: .month, value: -years * 12 + 1, to: Date())!
        let key = unit == .year ? String(Calendar.current.component(.year, from: cutoff))
                                : String(format: "%04d-%02d", Calendar.current.component(.year, from: cutoff), Calendar.current.component(.month, from: cutoff))
        return rows.filter { $0.period >= key }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let all = model.everything {
                Card(title: "Everything on your iPhone", symbol: "chart.pie.fill", tint: Tab.library.color,
                     trailing: AnyView(span(all))) {
                    Donuts(b: all, onSource: pick)
                }
            }
            Card(title: "Your selection", symbol: "line.3.horizontal.decrease.circle.fill", tint: Tab.cleanUp.color,
                 trailing: AnyView(share)) {
                if model.draft.isEmpty {
                    Label("Pick filters in Set your backup rules and this shows what they select, cut the same ways.",
                          systemImage: "hand.point.up.left.fill").font(.callout).foregroundStyle(.secondary)
                } else if let sel = model.selection, sel.total.count > 0 {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(model.draft.summary).font(.body.weight(.semibold))
                        Donuts(b: sel, onSource: nil)
                        Text("OVER TIME").sectionLabel().padding(.top, 4)
                        Timeline(all: periods(sel), label: unit == .year ? { $0 } : Self.shortMonth, onPick: nil)
                    }
                } else {
                    Text("Nothing on the phone matches these filters.").font(.callout).foregroundStyle(.secondary)
                }
            }
            if let all = model.everything {
                Card(title: "Over time", symbol: "calendar", tint: Tab.library.color,
                     trailing: AnyView(TimeControls(unit: $unit, span: $span))) {
                    VStack(alignment: .leading, spacing: 6) {
                        Timeline(all: periods(all), label: unit == .year ? { $0 } : Self.shortMonth) { p in
                            model.draft.year = Int(p.prefix(4))
                            model.openBuilder()
                        }
                        Text("Click a bar to build a rule for that year. \(Self.date(all.oldest)) to \(Self.date(all.newest)).")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .task(id: [AnyHashable(model.draft), AnyHashable(model.everything?.total)]) {
            guard !model.draft.isEmpty else { return }
            try? await Task.sleep(for: .milliseconds(150))
            guard !Task.isCancelled else { return }
            model.selection = await model.breakdown(model.draft, ledger: model.ledgerPath)
        }
    }

    private func pick(_ channel: Channel) {
        guard let source = channel.cliSource else { return }
        model.draft.source = source
        model.openBuilder()
    }

    private func span(_ b: Breakdown) -> some View {
        Text("\(Fmt.bytes(b.total.bytes)) · \(Fmt.count(b.total.count)) items · \(Self.date(b.oldest)) to \(Self.date(b.newest)) · \(Fmt.bytes(b.averageBytes)) each on average")
            .font(.callout).foregroundStyle(.secondary)
    }

    @ViewBuilder private var share: some View {
        if !model.draft.isEmpty, let sel = model.selection, let all = model.everything, all.total.bytes > 0 {
            Text("\(Fmt.bytes(sel.total.bytes)) · \(Fmt.count(sel.total.count)) items · \(Fmt.pct(Double(sel.total.bytes) / Double(all.total.bytes))) of your phone")
                .font(.callout).foregroundStyle(.secondary)
        }
    }

    static func date(_ s: String?) -> String {
        guard let s, s.count >= 7 else { return "?" }
        return month(String(s.prefix(7)))
    }

    /// "2025-03" → "Mar 2025".
    static func month(_ p: String) -> String {
        let parts = p.split(separator: "-")
        guard parts.count == 2, let m = Int(parts[1]), (1...12).contains(m) else { return p }
        return "\(Calendar.current.shortMonthSymbols[m - 1]) \(parts[0])"
    }

    /// "2025-03" → "Mar 25", short enough for 24 bars.
    static func shortMonth(_ p: String) -> String {
        let m = month(p)
        return m.count > 4 ? String(m.dropLast(4)) + String(m.suffix(2)) : m
    }
}

enum TimeUnit: String, CaseIterable { case year, month }

enum TimeSpan: String, CaseIterable {
    case all, five, two, one
    var years: Int? { switch self { case .all: nil; case .five: 5; case .two: 2; case .one: 1 } }
    var title: String { switch self { case .all: "All time"; case .five: "Last 5 years"; case .two: "Last 2 years"; case .one: "Last 12 months" } }
}

private struct TimeControls: View {
    @Binding var unit: TimeUnit
    @Binding var span: TimeSpan

    var body: some View {
        HStack(spacing: 8) {
            Picker("", selection: $unit) {
                Text("By year").tag(TimeUnit.year)
                Text("By month").tag(TimeUnit.month)
            }
            .pickerStyle(.segmented).labelsHidden().fixedSize()
            Picker("", selection: $span) {
                ForEach(TimeSpan.allCases, id: \.self) { Text($0.title).tag($0) }
            }
            .labelsHidden().fixedSize()
        }
    }
}

/// Three donuts side by side: where it came from, what it is, whether Drive has it.
private struct Donuts: View {
    let b: Breakdown
    let onSource: ((Channel) -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: 18) {
            Donut(title: "From", slices: b.bySource, total: b.total, color: {
                Theme.color(Channel(rawValue: $0) ?? .unattributed)
            }, onPick: onSource.map { f in { key in Channel(rawValue: key).map(f) } })
            Donut(title: "Kind", slices: b.byKind, total: b.total, color: Self.kindColor, onPick: nil)
            Donut(title: "Google Drive", slices: b.byStatus, total: b.total, color: Self.statusColor, onPick: nil)
        }
    }

    static func kindColor(_ k: String) -> Color {
        switch k {
        case "photo": Theme.series[0]
        case "live": Theme.series[6]
        case "video": Theme.series[1]
        default: Theme.series[3]
        }
    }

    static func statusColor(_ k: String) -> Color {
        switch k {
        case "canGo": Theme.good
        case "kept": Theme.other
        default: Theme.warn
        }
    }
}

private struct Donut: View {
    let title: String
    let slices: [Breakdown.Slice]
    let total: Tally
    let color: (String) -> Color
    let onPick: ((String) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased()).sectionLabel()
            Chart(slices) { s in
                SectorMark(angle: .value("Size", Double(s.tally.bytes)), innerRadius: .ratio(0.62), angularInset: 1.2)
                    .foregroundStyle(color(s.key))
                    .cornerRadius(3)
            }
            .chartLegend(.hidden)
            .frame(height: 150)
            .overlay {
                VStack(spacing: 0) {
                    Text(Fmt.bytes(total.bytes)).font(.headline).monospacedDigit()
                    Text("\(Fmt.count(total.count)) items").font(.caption).foregroundStyle(.secondary)
                }
            }
            VStack(alignment: .leading, spacing: 4) {
                ForEach(slices) { s in
                    row(s)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func row(_ s: Breakdown.Slice) -> some View {
        let line = HStack(spacing: 6) {
            RoundedRectangle(cornerRadius: 2).fill(color(s.key)).frame(width: 10, height: 10)
            Text(s.title).lineLimit(1)
            Spacer(minLength: 4)
            Text(Fmt.bytes(s.tally.bytes)).fontWeight(.semibold).monospacedDigit()
            Text(Fmt.pct(total.bytes == 0 ? 0 : Double(s.tally.bytes) / Double(total.bytes)))
                .foregroundStyle(.secondary).monospacedDigit().frame(width: 36, alignment: .trailing)
        }
        .font(.callout)
        .help("\(Fmt.count(s.tally.count)) items")
        if let onPick {
            Button { onPick(s.key) } label: { line.contentShape(Rectangle()) }
                .buttonStyle(.plain)
                .help("\(Fmt.count(s.tally.count)) items. Click to build a rule for \(s.title)")
        } else {
            line
        }
    }
}

/// Stacked bars of what is in Drive and what is not.
private struct Timeline: View {
    let all: [Breakdown.Period]
    let label: (String) -> String
    let onPick: ((String) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Chart {
                ForEach(all) { p in
                    BarMark(x: .value("When", p.period), y: .value("GB", gb(p.inDrive.bytes)))
                        .foregroundStyle(by: .value("Status", "In Drive"))
                    BarMark(x: .value("When", p.period), y: .value("GB", gb(p.notInDrive.bytes)))
                        .foregroundStyle(by: .value("Status", "Not in Drive yet"))
                }
            }
            .chartForegroundStyleScale(["In Drive": Theme.good, "Not in Drive yet": Theme.warn])
            .chartXAxis {
                // About a dozen labels, however many bars.
                let step = max(1, Int((Double(all.count) / 12).rounded(.up)))
                AxisMarks(values: all.enumerated().filter { $0.offset % step == 0 }.map(\.element.period)) { v in
                    AxisGridLine()
                    AxisValueLabel { if let p = v.as(String.self) { Text(label(p)).fixedSize() } }
                }
            }
            .chartXScale(domain: all.map(\.period))
            .chartYAxis { AxisMarks { v in
                AxisGridLine()
                AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.bytes(Int64(d * 1_000_000_000))) } }
            } }
            .chartOverlay { proxy in
                GeometryReader { _ in
                    Rectangle().fill(.clear).contentShape(Rectangle())
                        .onTapGesture { point in
                            guard let onPick, let x: String = proxy.value(atX: point.x) else { return }
                            onPick(x)
                        }
                }
            }
            .frame(height: 200)
        }
    }

    private func gb(_ b: Int64) -> Double { Double(b) / 1_000_000_000 }
}
