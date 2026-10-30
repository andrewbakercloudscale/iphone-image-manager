import PhotoCore
import SwiftUI

struct LibraryView: View {
    @ObservedObject var model: Model

    var body: some View {
        if let s = model.library {
            VStack(alignment: .leading, spacing: 14) {
                FilterBuilder(model: model).id("builder")
                HStack(spacing: 12) {
                    Kpi(label: "ON YOUR IPHONE", value: Fmt.bytes(s.onPhone.bytes), note: "\(Fmt.count(s.onPhone.count)) items")
                    Kpi(label: "SAFE IN GOOGLE DRIVE", value: Fmt.pct(s.coverage), note: "\(Fmt.bytes(s.verified.bytes)) checked", color: Theme.good)
                    Kpi(label: "YOU COULD FREE", value: Fmt.bytes(s.couldFree), note: "with the ages below", color: Tab.cleanUp.color)
                    Kpi(label: "GROWING", value: "\(Fmt.bytes(s.weeklyGrowth)) / wk", note: "average of the last 3 months", color: Tab.library.color)
                }
                MediaCharts(model: model)
                KeepCard(model: model, library: s)
                SuggestionGrid(model: model)
                CoverageCard(model: model, library: s)
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

struct BackupView: View {
    @ObservedObject var model: Model

    var body: some View {
        if let s = model.library {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 12) {
                    Kpi(label: "CHECKED IN GOOGLE DRIVE", value: Fmt.count(s.verified.count), note: Fmt.bytes(s.verified.bytes), color: Theme.good)
                    Kpi(label: "ONLY ON THE PHONE", value: Fmt.count(s.onPhone.count - s.verified.count), note: Fmt.bytes(s.onPhone.bytes - s.verified.bytes), color: Theme.warn)
                }
                Card(title: "Coverage by source", symbol: "icloud.and.arrow.up.fill", tint: Tab.backup.color) {
                    VStack(alignment: .leading, spacing: 12) {
                        // Most still to back up first.
                        ForEach(s.channels.sorted { $0.total.bytes - $0.verified.bytes > $1.total.bytes - $1.verified.bytes }) { c in CoverageRow(model: model, stats: c) }
                        Text("A backup copies to Google Drive and checks each file's hash. Nothing is removed from the phone by a backup. One runs at a time; the rest wait their turn.")
                            .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
                DriveCheckCard(model: model)
                LayoutCard(model: model)
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
                                Text("\(Fmt.count(p.confirmed)) of \(Fmt.count(p.total)) removals had Google Drive's hash re-read and matched at the moment of removal.")
                                    .fontWeight(.semibold)
                            }
                            Text("\(Fmt.count(p.stillVerified)) are still recorded as verified in Google Drive. Check Google Drive on the Backup tab re-reads every one of them.")
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
            StorageCard(model: model)
            GooglePhotosCard(model: model)
            WhenBackupsRunCard(model: model)
            Card(title: "Where things are", symbol: "folder.fill", tint: Tab.backup.color) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                    GridRow { Text("Ledger"); TextField("", text: $model.ledgerPath).textFieldStyle(.roundedBorder) }
                    GridRow { Text("iphone-image repo"); TextField("", text: $model.repoPath).textFieldStyle(.roundedBorder) }
                    GridRow { Text("Job log"); Text(Jobs.logPath).foregroundStyle(.secondary).textSelection(.enabled) }
                }
                .font(.callout)
            }
            PermissionsCard(permissions: model.permissions)
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

/// One source on the Backup tab: how much is in Drive, why the rest is not, and the button
/// that backs it up.
struct CoverageRow: View {
    @ObservedObject var model: Model
    let stats: ChannelStats

    var body: some View {
        let c = stats
        let left = Tally(count: c.total.count - c.verified.count, bytes: c.total.bytes - c.verified.bytes)
        let state = model.backupState(c.channel)
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(c.channel.title).fontWeight(.semibold)
                Spacer()
                Text("\(Fmt.count(c.verified.count)) of \(Fmt.count(c.total.count)) · \(Fmt.bytes(left.bytes)) to go")
                    .foregroundStyle(.secondary).monospacedDigit()
            }
            // By count, like the figures beside it: by bytes WhatsApp read 6% next to "13,693 of 48,016".
            let share = c.total.count == 0 ? 1 : Double(c.verified.count) / Double(c.total.count)
            Meter(value: share, color: share >= 0.999 ? Theme.good : Tab.backup.color)
            if left.count > 0 {
                HStack(spacing: 8) {
                    if state.running {
                        running(c)
                    } else if !state.queued.isEmpty {
                        Label("Queued: starts when the running backup ends.", systemImage: "clock")
                            .foregroundStyle(.secondary)
                    } else {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(why(c, left)).foregroundStyle(c.verified.count == 0 ? Theme.warn : .secondary)
                            if let kinds = c.notBackedUpKinds { Text(kinds).foregroundStyle(.secondary).monospacedDigit() }
                            if c.channel != .otherApps, let apps = c.notBackedUpApps {
                                Text("From: \(apps)").foregroundStyle(.secondary).monospacedDigit()
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                    Spacer(minLength: 6)
                    if state.running {
                        RowButton(title: "Show", symbol: "list.bullet", color: Tab.backup.color) { model.tab = .activity }
                    } else if !state.queued.isEmpty {
                        RowButton(title: "Cancel", symbol: "xmark", color: .secondary, style: .ghost) {
                            state.queued.forEach(model.cancelQueued)
                        }
                    } else {
                        RowButton(title: "Back up \(Fmt.count(left.count))", symbol: "icloud.and.arrow.up", color: Tab.backup.color, style: .fill) {
                            model.backUpSource(c.channel)
                        }
                        .help("Copies all \(Fmt.count(left.count)) (\(Fmt.bytes(left.bytes))) to Google Drive and checks each hash. Nothing is removed from the phone.")
                    }
                }
                .font(.caption)
                if c.channel == .otherApps, c.notBackedUpByApp.count > 1, !state.running {
                    AppBreakdown(model: model, apps: c.notBackedUpByApp)
                }
            }
        }
        .font(.callout)
    }

    /// The job's step and its own bar, and what this run covers, which can be less than
    /// everything not in Drive (WhatsApp's runs only take items older than 18 months).
    @ViewBuilder private func running(_ c: ChannelStats) -> some View {
        let job = model.jobs.first { $0.source == c.channel.cliSource && $0.state == .running }
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Image(systemName: "arrow.triangle.2.circlepath").spinning()
                Text("Backing up now" + (job?.step.map { ": \($0)" } ?? "") + ".").monospacedDigit()
            }
            .foregroundStyle(Tab.backup.color)
            if let p = job?.progress { Meter(value: p, color: Tab.backup.color, height: 5).frame(maxWidth: 320) }
            if let at = job?.uploadLast?.at {
                // rclone reports every 30 seconds; say so, or a still number looks stuck.
                Text("Counts refresh every 30 seconds. Last: \(at.formatted(date: .omitted, time: .standard)).")
                    .foregroundStyle(.tertiary)
            }
            if let job, let scope = scope(job) { Text(scope).foregroundStyle(.secondary) }
        }
    }

    private func scope(_ j: Job) -> String? {
        let age = Job.ages[j.source].flatMap(Age.init).flatMap { $0.amount > 0 ? " older than \($0.words)" : nil } ?? ""
        guard let start = j.startedLeft else { return nil }
        return "This run: \(j.title)\(age), \(Fmt.count(start)) to copy when it started. The rest waits for a Back up of its own."
    }

    private func why(_ c: ChannelStats, _ left: Tally) -> String {
        let age = c.notBackedUpAge.map { ", \($0)" } ?? ""
        if c.verified.count == 0 { return "Never backed up: no backup has been run for this source yet\(age)." }
        return "\(Fmt.count(left.count)) not in Google Drive yet\(age)."
    }
}

/// Turns a symbol round and round while it is on screen: a job under way, not a still picture.
private struct Spinning: ViewModifier {
    @State private var on = false
    func body(content: Content) -> some View {
        content
            .rotationEffect(.degrees(on ? 360 : 0))
            .animation(.linear(duration: 1.6).repeatForever(autoreverses: false), value: on)
            .onAppear { on = true }
    }
}

extension View {
    func spinning() -> some View { modifier(Spinning()) }
}

/// Other apps, one row per app: what it saved that is not in Google Drive, photos and videos
/// apart, biggest first, each with its own Back up. The six biggest show; the rest on request.
struct AppBreakdown: View {
    @ObservedObject var model: Model
    let apps: [ChannelStats.AppTally]
    @State private var all = false

    var body: some View {
        let shown = all ? apps : Array(apps.prefix(6))
        let top = Double(max(apps.first?.tally.bytes ?? 1, 1))
        VStack(alignment: .leading, spacing: 6) {
            Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 6) {
                ForEach(shown) { a in
                    GridRow {
                        Text(a.app).fontWeight(.medium).lineLimit(1)
                        Text(Fmt.bytes(a.tally.bytes)).monospacedDigit().gridColumnAlignment(.trailing)
                        Capsule().fill(Theme.series[6].opacity(0.8))
                            .frame(width: max(2, 120 * Double(a.tally.bytes) / top), height: 6)
                            .frame(width: 120, alignment: .leading)
                        Text(kinds(a)).foregroundStyle(.secondary).monospacedDigit()
                        Spacer(minLength: 0)
                        button(a)
                    }
                }
            }
            if apps.count > 6 {
                let rest = apps.dropFirst(6)
                Button(all ? "Show fewer" : "Show all \(apps.count) apps (\(rest.count) more, \(Fmt.bytes(rest.reduce(0) { $0 + $1.tally.bytes })))") {
                    all.toggle()
                }
                .buttonStyle(.link)
            }
        }
        .font(.caption)
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.06)))
    }

    /// "40 videos · 35 photos", the bigger part first.
    private func kinds(_ a: ChannelStats.AppTally) -> String {
        func part(_ t: Tally, _ one: String) -> (Int64, String)? {
            t.count == 0 ? nil : (t.bytes, "\(Fmt.count(t.count)) \(one)\(t.count == 1 ? "" : "s")")
        }
        return [part(a.videos, "video"), part(a.photos, "photo")].compactMap { $0 }
            .sorted { $0.0 > $1.0 }.map(\.1).joined(separator: " · ")
    }

    @ViewBuilder private func button(_ a: ChannelStats.AppTally) -> some View {
        if let q = model.queue.first(where: { $0.label == Model.appLabel(a) }) {
            RowButton(title: "Queued", symbol: "xmark", color: .secondary, style: .ghost) { model.cancelQueued(q) }
                .help("Starts when the running backup ends. Click to take it off the queue.")
        } else {
            RowButton(title: "Back up", symbol: "icloud.and.arrow.up", color: Tab.backup.color, style: .soft) {
                model.backUpApp(a)
            }
            .help("Copies \(a.app)'s \(Fmt.count(a.tally.count)) (\(Fmt.bytes(a.tally.bytes))) to Google Drive and checks each hash. Nothing is removed from the phone.")
        }
    }
}
