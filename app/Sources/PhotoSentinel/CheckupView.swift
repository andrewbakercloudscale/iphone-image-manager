import PhotoCore
import SwiftUI

struct CheckupView: View {
    @ObservedObject var model: Model

    var body: some View {
        let findings = model.findings
        VStack(alignment: .leading, spacing: 14) {
            hero(findings)
            Card(title: "What to do next", symbol: "stethoscope", tint: Tab.checkup.color) {
                VStack(alignment: .leading, spacing: 10) {
                    if findings.isEmpty {
                        Label("Nothing needs you. Everything selected is safe in Google Drive.", systemImage: "checkmark.seal.fill")
                            .foregroundStyle(Theme.good)
                    }
                    ForEach(groups(findings), id: \.0) { label, items in
                        Text(label).sectionLabel().padding(.top, 4)
                        ForEach(items) { f in FindingRow(finding: f) { model.perform(f.action) } }
                    }
                    if let s = model.library {
                        worthKnowing(s).padding(.top, 4)
                    }
                }
            }
            if let s = model.library { stats(s) }
        }
    }

    private func hero(_ findings: [Finding]) -> some View {
        let free = model.library?.recipes.filter { $0.recipe.kind != .largeVideos }.reduce(Int64(0)) { $0 + $1.selected.bytes } ?? 0
        let ready = model.library?.recipes.filter { $0.recipe.kind != .largeVideos }.reduce(Int64(0)) { $0 + $1.removableNow.bytes } ?? 0
        return HStack(spacing: 20) {
            Image(systemName: "sparkles").font(.system(size: 54, weight: .semibold))
            VStack(alignment: .leading, spacing: 6) {
                Text(findings.isEmpty ? "All clear" : "\(findings.count) thing\(findings.count == 1 ? "" : "s") to look at")
                    .font(.title.weight(.bold))
                Text("Clean-ups could free \(Fmt.bytes(free)); \(Fmt.bytes(ready)) of it is already safe in Google Drive and can come off the phone now. Read \(model.library.map { $0.readAt.formatted(date: .omitted, time: .shortened) } ?? "–").")
                    .font(.body).fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Button { model.refresh(); model.refreshDoctor() } label: {
                Label(model.loading ? "Checking…" : "Check again", systemImage: "arrow.clockwise")
                    .font(.title3.weight(.semibold)).foregroundStyle(Color(hex: "#9a3412"))
                    .padding(.horizontal, 18).padding(.vertical, 10)
                    .background(Capsule().fill(.white))
            }
            .buttonStyle(.plain)
        }
        .foregroundStyle(.white)
        .padding(22)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.heroGradient))
    }

    private func groups(_ f: [Finding]) -> [(String, [Finding])] {
        let safety = f.filter { $0.kind == .safety }
        let jobs = f.filter { $0.kind == .job }
        let space = f.filter { $0.kind == .space }
        return [("KEEP IT SAFE FIRST", safety), ("JOBS THAT NEED YOU", jobs), ("FREE UP SPACE", space)].filter { !$0.1.isEmpty }
    }

    private func worthKnowing(_ s: LibrarySnapshot) -> some View {
        DisclosureGroup("Worth knowing") {
            VStack(alignment: .leading, spacing: 6) {
                Text("\(Fmt.count(s.proxies)) items look like low-res stand-ins, so they stay on the phone until checked.")
                Text("\(Fmt.count(s.favourites)) favourites are never cleaned up.")
                Text("\(Fmt.count(s.live)) Live Photos stay until their motion half is backed up too.")
            }
            .font(.callout).foregroundStyle(.secondary).padding(.top, 4)
        }
        .font(.body.weight(.medium))
    }

    private func stats(_ s: LibrarySnapshot) -> some View {
        HStack(spacing: 12) {
            StatTile(symbol: "checkmark.shield.fill", color: Theme.good, value: Fmt.bytes(s.verified.bytes), label: "checked safe in Google Drive")
            StatTile(symbol: "iphone", color: Tab.backup.color, value: Fmt.bytes(s.removed.total.bytes), label: "freed on the phone so far")
            StatTile(symbol: "arrow.uturn.backward", color: Theme.warn, value: "\(Fmt.count(s.removed.recent.count)) items",
                     label: "in Recently Deleted · can still be undone")
        }
    }
}

struct StatTile: View {
    let symbol: String
    let color: Color
    let value: String
    let label: String

    var body: some View {
        HStack(spacing: 10) {
            SymbolTile(symbol: symbol, color: color, size: 36)
            VStack(alignment: .leading, spacing: 1) {
                Text(value).font(.title3.weight(.bold)).monospacedDigit()
                Text(label).font(.callout).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.cardFill))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.cardStroke, lineWidth: 1.5))
    }
}

/// Sentinel's finding row: a coloured edge, a tile, title and reason, then the actions.
struct FindingRow: View {
    let finding: Finding
    let act: () -> Void
    @State private var ignored = false

    private var color: Color {
        switch finding.kind {
        case .safety: Theme.warn
        case .job: Tab.activity.color
        case .space: Tab.cleanUp.color
        case .info: Theme.accent
        }
    }

    var body: some View {
        if !ignored {
            HStack(alignment: .top, spacing: 12) {
                SymbolTile(symbol: finding.symbol, color: color, size: 34)
                VStack(alignment: .leading, spacing: 3) {
                    Text(finding.title).font(.body.weight(.semibold))
                    Text(finding.detail).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                CapsuleButton(title: "Ignore", symbol: "eye.slash", style: .ghost) { ignored = true }
                CapsuleButton(title: finding.actionTitle, color: color, action: act)
            }
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 10).fill(color.opacity(0.07)))
            .overlay(alignment: .leading) { Rectangle().fill(color).frame(width: 4) }
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
    }
}
