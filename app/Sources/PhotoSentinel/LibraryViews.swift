import PhotoCore
import SwiftUI

struct LibraryView: View {
    @ObservedObject var model: Model

    var body: some View {
        if let s = model.library {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 12) {
                    Kpi(label: "ON YOUR IPHONE", value: Fmt.bytes(s.onPhone.bytes), note: "\(Fmt.count(s.onPhone.count)) items")
                    Kpi(label: "SAFE IN GOOGLE DRIVE", value: Fmt.pct(s.coverage), note: "\(Fmt.bytes(s.verified.bytes)) checked", color: Theme.good)
                    Kpi(label: "YOU COULD FREE", value: Fmt.bytes(s.couldFree), note: "with the ages below", color: Tab.cleanUp.color)
                    Kpi(label: "GROWING", value: "\(Fmt.bytes(s.weeklyGrowth)) / wk", note: "average of the last 3 months", color: Tab.library.color)
                }
                KeepCard(model: model, library: s)
                Card(title: "Where the space goes", symbol: "chart.pie.fill", tint: Tab.library.color) {
                    VStack(alignment: .leading, spacing: 10) {
                        StackBar(parts: s.channels.map { (Theme.color($0.channel), Double($0.total.bytes)) })
                        ForEach(s.channels) { c in
                            HStack {
                                RoundedRectangle(cornerRadius: 3).fill(Theme.color(c.channel)).frame(width: 11, height: 11)
                                Text(c.channel.title).frame(width: 150, alignment: .leading)
                                Text("\(Fmt.count(c.photos.count)) photos · \(Fmt.count(c.videos.count)) videos").foregroundStyle(.secondary)
                                Spacer()
                                Text(Fmt.bytes(c.total.bytes)).fontWeight(.semibold).monospacedDigit()
                            }
                            .font(.callout)
                        }
                    }
                }
                FreeCard(model: model, library: s)
                CoverageCard(model: model, library: s)
                Card(title: "By year taken", symbol: "calendar", tint: Tab.library.color,
                     trailing: AnyView(Text("click a year to clean it up").font(.callout).foregroundStyle(.secondary))) {
                    YearChart(years: s.years) { y in
                        model.draft = Filter(year: y)
                        model.tab = .cleanUp
                    }
                }
                PlacesCard(model: model, library: s)
                BiggestVideosCard(model: model, library: s)
                AppsCard(model: model, library: s)
            }
        }
    }
}

struct Kpi: View {
    let label: String
    let value: String
    let note: String
    var color: Color = .primary

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).sectionLabel()
            Text(value).font(.system(size: 26, weight: .bold)).foregroundStyle(color).monospacedDigit()
            Text(note).font(.callout).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.cardFill))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.cardStroke, lineWidth: 1.5))
    }
}

struct BarRow: View {
    let label: String
    let value: Double
    let text: String
    let color: Color

    var body: some View {
        HStack(spacing: 10) {
            Text(label).frame(width: 170, alignment: .leading).lineLimit(1)
            Meter(value: value, color: color)
            Text(text).fontWeight(.semibold).monospacedDigit().frame(width: 70, alignment: .trailing)
        }
        .font(.callout)
    }
}

struct StackBar: View {
    let parts: [(Color, Double)]
    var height: CGFloat = 24

    var body: some View {
        GeometryReader { g in
            let total = max(parts.reduce(0) { $0 + $1.1 }, 1)
            HStack(spacing: 0) {
                ForEach(parts.indices, id: \.self) { i in
                    Rectangle().fill(parts[i].0).frame(width: g.size.width * parts[i].1 / total)
                }
            }
        }
        .frame(height: height)
        .clipShape(RoundedRectangle(cornerRadius: 7))
    }
}

struct YearChart: View {
    let years: [(year: String, bytes: Int64, count: Int)]
    var pick: ((Int) -> Void)?

    var body: some View {
        let top = Double(years.map(\.bytes).max() ?? 1)
        let thisYear = Calendar.current.component(.year, from: Date())
        HStack(alignment: .bottom, spacing: 6) {
            ForEach(years, id: \.year) { y in
                VStack(spacing: 4) {
                    Text(Fmt.bytes(y.bytes)).font(.caption2).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.7)
                    RoundedRectangle(cornerRadius: 4)
                        .fill((Int(y.year) ?? thisYear) < thisYear - 1 ? Tab.library.color : Tab.library.color.opacity(0.45))
                        .frame(height: max(2, 140 * Double(y.bytes) / max(top, 1)))
                    Text(y.year).font(.caption).monospacedDigit()
                }
                .frame(maxWidth: .infinity)
                .contentShape(Rectangle())
                .onTapGesture { if let n = Int(y.year) { pick?(n) } }
                .help("\(y.year): \(Fmt.count(y.count)) items, \(Fmt.bytes(y.bytes)). Click to back up or remove them.")
            }
        }
        .frame(height: 190, alignment: .bottom)
    }
}

struct CleanUpView: View {
    @ObservedObject var model: Model

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            FilterBuilder(model: model)
            SuggestionGrid(model: model)
            Text("YOUR CLEAN-UPS · EVERY ONE BACKS UP TO GOOGLE DRIVE FIRST").sectionLabel()
            Text("Numbers here are estimates from the last scan. The plan in each clean-up scans the phone and re-checks Google Drive, and its numbers are the ones that count.")
                .font(.callout).foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 300), spacing: 12)], spacing: 12) {
                ForEach(model.library?.recipes ?? []) { r in
                    RecipeCard(stats: r, queued: model.queue.contains { q in r.recipe.backups.contains { $0.source == q.source && $0.type == q.type } },
                               backUp: { model.backUp(r.recipe) }, review: { model.removing = r.recipe },
                               forget: r.recipe.kind == .custom ? { model.forget(r.recipe.filter) } : nil)
                }
            }
        }
    }
}

struct RecipeCard: View {
    let stats: RecipeStats
    var queued = false
    var backUp: () -> Void = {}
    var review: () -> Void = {}
    var forget: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                SymbolTile(symbol: stats.recipe.symbol, color: Tab.cleanUp.color, size: 34)
                Text(stats.recipe.title).font(.headline)
                Spacer()
                if let forget {
                    Button(action: forget) { Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary) }
                        .buttonStyle(.plain).help("Forget this clean-up. Nothing on the phone or in Drive changes.")
                }
            }
            Text(stats.recipe.detail).font(.callout).foregroundStyle(.secondary)
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 4) {
                row("On the phone", stats.selected, .primary)
                row("In Drive, can go (est.)", stats.removableNow, Theme.good)
                row("Not in Drive yet", stats.notBackedUp, Theme.warn)
                row("Kept for safety", stats.kept, .secondary)
            }
            .font(.callout)
            HStack {
                CapsuleButton(title: "Remove from iPhone…", symbol: "trash.fill", color: Tab.cleanUp.color, action: review)
                if stats.notBackedUp.count > 0 && !stats.recipe.backups.isEmpty {
                    if queued { Pill(text: "Backup queued", symbol: "clock.fill", color: Theme.warn) }
                    else { CapsuleButton(title: "Back up", symbol: "icloud.and.arrow.up.fill", color: Tab.backup.color, style: .soft, action: backUp) }
                }
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.cardFill))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.cardStroke, lineWidth: 1.5))
    }

    private func row(_ label: String, _ t: Tally, _ color: Color) -> some View {
        GridRow {
            Text(label)
            Text(Fmt.count(t.count)).monospacedDigit().foregroundStyle(color)
            Text(Fmt.bytes(t.bytes)).monospacedDigit().foregroundStyle(color).fontWeight(.semibold)
        }
    }
}

struct BackupView: View {
    @ObservedObject var model: Model

    var body: some View {
        if let s = model.library {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 12) {
                    Kpi(label: "CHECKED IN DRIVE", value: Fmt.count(s.verified.count), note: Fmt.bytes(s.verified.bytes), color: Theme.good)
                    Kpi(label: "ONLY ON THE PHONE", value: Fmt.count(s.onPhone.count - s.verified.count), note: Fmt.bytes(s.onPhone.bytes - s.verified.bytes), color: Theme.warn)
                }
                Card(title: "Coverage by source", symbol: "icloud.and.arrow.up.fill", tint: Tab.backup.color) {
                    VStack(spacing: 10) {
                        ForEach(s.channels) { c in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(c.channel.title).fontWeight(.semibold)
                                    Spacer()
                                    Text("\(Fmt.count(c.verified.count)) of \(Fmt.count(c.total.count)) · \(Fmt.bytes(c.total.bytes - c.verified.bytes)) to go")
                                        .foregroundStyle(.secondary).monospacedDigit()
                                }
                                Meter(value: c.coverage, color: c.coverage >= 0.999 ? Theme.good : Tab.backup.color)
                            }
                            .font(.callout)
                        }
                    }
                }
                DriveCheckCard(model: model)
            }
        }
    }
}

struct BinView: View {
    @ObservedObject var model: Model

    var body: some View {
        if let s = model.library {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 12) {
                    Kpi(label: "REMOVED FROM IPHONE", value: Fmt.count(s.removed.total.count), note: Fmt.bytes(s.removed.total.bytes), color: Tab.bin.color)
                    Kpi(label: "STILL UNDOABLE", value: Fmt.count(s.removed.recent.count), note: "removed in the last 30 days", color: Theme.good)
                }
                RecoverCard(model: model, library: s)
                if let p = model.allProof, p.total > 0 {
                    Card(title: "Proof every removed item is in Google Drive", symbol: "checkmark.seal.fill", tint: Theme.good) {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(spacing: 6) {
                                Image(systemName: p.allConfirmed ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                                    .foregroundStyle(p.allConfirmed ? Theme.good : Theme.warn)
                                Text("\(Fmt.count(p.confirmed)) of \(Fmt.count(p.total)) removals had Drive's hash re-read and matched at the moment of removal.")
                                    .fontWeight(.semibold)
                            }
                            Text("\(Fmt.count(p.stillVerified)) are still recorded as verified in Drive. Check Google Drive on the Backup tab re-reads every one of them.")
                                .foregroundStyle(.secondary)
                        }
                        .font(.callout).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }
}

/// The audit, run from the app: every verified file's hash re-read from Google Drive.
struct DriveCheckCard: View {
    @ObservedObject var model: Model

    var body: some View {
        let audit = model.latest(.audit)
        Card(title: "Is everything really in Google Drive?", symbol: "checkmark.seal.fill", tint: Theme.good,
             trailing: (audit?.running ?? false) ? nil : AnyView(CapsuleButton(title: audit == nil ? "Check now" : "Check again", symbol: "arrow.clockwise", color: Theme.good, style: .soft) { model.runAudit() })) {
            VStack(alignment: .leading, spacing: 6) {
                if let a = audit, a.running {
                    Running(text: "Asking Google Drive for every file's hash (about 7 minutes)…", since: a.task.startedAt)
                } else if let r = audit?.decode(AuditReport.self), let a = audit {
                    AuditSummary(report: r, at: a.task.startedAt)
                } else if let msg = audit?.errorMessage {
                    Text("The check could not finish: \(msg)").font(.callout).foregroundStyle(Theme.bad)
                } else {
                    Text("Re-reads the SHA-256 of every file in Google Drive and compares it with the hash taken when it left the phone. Every removal also re-checks its own items in the same run.")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct SettingsView: View {
    @ObservedObject var model: Model

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Card(title: "What stays on your phone", symbol: "iphone.gen3", tint: Tab.library.color,
                 trailing: AnyView(RowButton(title: "Open Library", symbol: "chart.bar.xaxis", color: Tab.library.color) { model.tab = .library })) {
                Text("How long each type of media stays on the phone is set on the Library tab, where you see what each choice frees.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Card(title: "Where things are", symbol: "folder.fill", tint: Tab.backup.color) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                    GridRow { Text("Ledger"); TextField("", text: $model.ledgerPath).textFieldStyle(.roundedBorder) }
                    GridRow { Text("iphone-image repo"); TextField("", text: $model.repoPath).textFieldStyle(.roundedBorder) }
                    GridRow { Text("Job log"); Text(Jobs.logPath).foregroundStyle(.secondary).textSelection(.enabled) }
                }
                .font(.callout)
            }
            Card(title: "Is this Mac ready?", symbol: "stethoscope", tint: Tab.checkup.color,
                 trailing: model.doctorRunning ? nil : AnyView(CapsuleButton(title: "Run again", symbol: "arrow.clockwise", color: Tab.checkup.color, style: .soft) { model.refreshDoctor() })) {
                DoctorPanel(model: model)
            }
        }
        .onChange(of: model.ledgerPath) { model.refresh() }
    }
}

/// The ages each clean-up uses. In Settings, and in onboarding before the first look,
/// so the numbers shown there are already the owner's.
struct AgesCard: View {
    @ObservedObject var model: Model
    /// Show what each choice frees, next to it.
    var impact = true

    var body: some View {
        Card(title: "Clean-up ages", symbol: "calendar.badge.clock", tint: Tab.cleanUp.color) {
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                ageRow("Camera photos older than", $model.photoAge, .cameraPhotos)
                ageRow("Camera videos older than", $model.videoAge, .oldVideos)
                ageRow("WhatsApp older than", $model.whatsappAge, .whatsapp)
                ageRow("Screenshots older than", $model.screenshotAge, .screenshots)
                ageRow("Screen recordings older than", $model.recordingAge, .screenRecordings)
            }
        }
        .onChange(of: model.ages) { model.refresh() }
    }

    /// "frees 21.3 GB · 1.6 GB today": all of it once backed up, and what can go now.
    @ViewBuilder private func effect(_ kind: Recipe.Kind) -> some View {
        if impact, let r = model.library?.recipes.first(where: { $0.recipe.kind == kind }) {
            HStack(spacing: 6) {
                Text("frees \(Fmt.bytes(r.selected.bytes))").fontWeight(.semibold).monospacedDigit()
                Text("· \(Fmt.count(r.selected.count)) items · \(Fmt.bytes(r.removableNow.bytes)) can go today")
                    .foregroundStyle(.secondary).monospacedDigit()
                if model.loading { ProgressView().controlSize(.mini) }
            }
            .font(.callout)
        }
    }

    private func ageRow(_ label: String, _ value: Binding<String>, _ kind: Recipe.Kind) -> some View {
        GridRow {
            Text(label)
            Picker("", selection: value) {
                ForEach(KeepCard.ages, id: \.self) { Text($0 == "off" ? "never" : Age($0)?.words ?? $0).tag($0) }
            }
            .labelsHidden().frame(width: 140)
            effect(kind)
        }
    }
}
