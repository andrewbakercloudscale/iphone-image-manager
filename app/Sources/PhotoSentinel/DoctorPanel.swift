import PhotoCore
import SwiftUI

/// Doctor, in the owner's words: what it is checking while it runs, then what needs a
/// look first, with everything that passed folded away. Getting photos onto this Mac
/// comes first because nothing else matters until Photos has the library.
struct DoctorPanel: View {
    @ObservedObject var model: Model
    @State private var showPassed = false

    struct Info {
        let group: Int
        let doing: String
        let ok: String
        var problem: String?
    }

    static let groups = ["Getting your photos onto this Mac", "This Mac", "Google Drive", "Records"]

    static let info: [String: Info] = [
        "apple_account": Info(group: 0, doing: "Checking your Apple account", ok: "Signed in to your Apple account"),
        "library": Info(group: 0, doing: "Finding your Photos library", ok: "Photos library found"),
        "icloud_mode": Info(group: 0, doing: "Checking how iCloud Photos stores files", ok: "iCloud Photos is set up"),
        "sync": Info(group: 0, doing: "Checking Photos has received everything from iCloud", ok: "Photos has your whole library",
                     problem: "Photos may not have your whole library yet"),
        "photos_authorisation": Info(group: 0, doing: "Checking permission to read Photos", ok: "Allowed to read Photos",
                                     problem: "Photo Sentinel can't read Photos yet"),
        "helper": Info(group: 0, doing: "Testing the Photos reader", ok: "Photos reader works", problem: "The Photos reader isn't working"),
        "disk": Info(group: 1, doing: "Checking free disk space", ok: "Enough free disk space", problem: "This Mac is short of disk space"),
        "archive_path": Info(group: 1, doing: "Checking the working folder", ok: "Working folder ready"),
        "tools": Info(group: 1, doing: "Finding the tools it needs", ok: "Tools installed", problem: "A tool it needs is missing"),
        "platform": Info(group: 1, doing: "Checking macOS", ok: "macOS supported"),
        "logging": Info(group: 1, doing: "Checking the log folder", ok: "Logs are being kept"),
        "cloud": Info(group: 2, doing: "Checking Google Drive is connected", ok: "Google Drive connected",
                      problem: "Google Drive isn't connected"),
        "ledger_conflicts": Info(group: 3, doing: "Checking the records for contradictions", ok: "Records are consistent",
                                 problem: "The records contradict themselves"),
    ]

    static func info(_ key: String) -> Info { info[key] ?? Info(group: 3, doing: "Checking \(key)", ok: key) }

    /// Results paired with their keys: doctor reports them in the order it planned them.
    private var results: [(key: String, check: Check)] {
        guard let r = model.doctor?.results else { return [] }
        let keys = model.doctorSteps.map(\.key)
        return r.enumerated().map { i, c in (i < keys.count ? keys[i] : c.name, c) }
            .sorted { Self.info($0.key).group < Self.info($1.key).group }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if model.doctorRunning { running } else if model.doctor == nil { missing } else { done }
        }
    }

    // MARK: While it runs

    private var running: some View {
        let steps = model.doctorSteps.sorted { Self.info($0.key).group < Self.info($1.key).group }
        let finished = steps.filter { !$0.status.isEmpty && $0.status != "RUNNING" }.count
        return VStack(alignment: .leading, spacing: 10) {
            TimelineView(.periodic(from: model.doctorStarted, by: 1)) { ctx in
                HStack {
                    ProgressView().controlSize(.small)
                    Text(steps.first { $0.status == "RUNNING" }.map { Self.info($0.key).doing + "…" } ?? "Starting the checks…")
                        .font(.body.weight(.semibold))
                    Spacer()
                    Text("\(finished) of \(max(steps.count, 1)) · \(Int(ctx.date.timeIntervalSince(model.doctorStarted)))s")
                        .font(.callout).foregroundStyle(.secondary).monospacedDigit()
                }
            }
            Meter(value: Double(finished) / Double(max(steps.count, 1)), color: Tab.checkup.color, height: 6)
            ForEach(Self.groups.indices, id: \.self) { g in
                let inGroup = steps.filter { Self.info($0.key).group == g }
                if !inGroup.isEmpty {
                    Text(Self.groups[g].uppercased()).sectionLabel().padding(.top, 4)
                    ForEach(inGroup, id: \.key) { step in
                        HStack(spacing: 8) {
                            stateIcon(step.status).frame(width: 18)
                            Text(step.status == "" || step.status == "RUNNING" ? Self.info(step.key).doing : Self.info(step.key).ok)
                                .foregroundStyle(step.status == "" ? .secondary : .primary)
                        }
                        .font(.callout)
                    }
                }
            }
        }
    }

    @ViewBuilder private func stateIcon(_ status: String) -> some View {
        switch status {
        case "RUNNING": ProgressView().controlSize(.mini)
        case "PASS": Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.good)
        case "WARN": Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.warn)
        case "FAIL": Image(systemName: "xmark.octagon.fill").foregroundStyle(Theme.bad)
        default: Image(systemName: "circle").foregroundStyle(.tertiary)
        }
    }

    // MARK: When it has finished

    private var missing: some View {
        HStack {
            Text("The checks did not run. Check the iphone-image folder in Settings.").foregroundStyle(.secondary)
            Spacer()
            CapsuleButton(title: "Run again", symbol: "arrow.clockwise", color: Tab.checkup.color, style: .soft) { model.refreshDoctor() }
        }
    }

    @ViewBuilder private var done: some View {
        let problems = results.filter { !$0.check.passed }
        let passed = results.filter(\.check.passed)
        HStack(spacing: 10) {
            Image(systemName: problems.isEmpty ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                .font(.title2).foregroundStyle(problems.isEmpty ? Theme.good : Theme.warn)
            Text(problems.isEmpty ? "Everything checks out" : "\(passed.count) of \(results.count) fine · \(problems.count) need\(problems.count == 1 ? "s" : "") a look")
                .font(.title3.weight(.semibold))
        }
        ForEach(problems, id: \.key) { p in ProblemCard(info: Self.info(p.key), check: p.check) }
        DisclosureGroup(isExpanded: $showPassed) {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Self.groups.indices, id: \.self) { g in
                    let rows = passed.filter { Self.info($0.key).group == g }
                    if !rows.isEmpty {
                        Text(Self.groups[g].uppercased()).sectionLabel().padding(.top, 4)
                        ForEach(rows, id: \.key) { r in
                            Label(Self.info(r.key).ok, systemImage: "checkmark.circle.fill")
                                .labelStyle(TintedLabel(color: Theme.good)).font(.callout)
                                .help(r.check.detail)
                        }
                    }
                }
            }
            .padding(.top, 4)
        } label: {
            Text("\(passed.count) checks passed").font(.callout.weight(.semibold))
        }
    }
}

private struct ProblemCard: View {
    let info: DoctorPanel.Info
    let check: Check
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: check.failed ? "xmark.octagon.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(check.failed ? Theme.bad : Theme.warn)
                Text(info.problem ?? check.name).font(.body.weight(.semibold))
            }
            Text(check.detail).font(.callout).foregroundStyle(.secondary).lineLimit(2)
            if !check.remedy.isEmpty {
                DisclosureGroup(isExpanded: $open) {
                    Text(check.remedy).font(.callout).fixedSize(horizontal: false, vertical: true).padding(.top, 2)
                } label: {
                    Text("What to do").font(.callout.weight(.semibold))
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill((check.failed ? Theme.bad : Theme.warn).opacity(0.08)))
    }
}

private struct TintedLabel: LabelStyle {
    let color: Color
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 8) {
            configuration.icon.foregroundStyle(color)
            configuration.title
        }
    }
}
