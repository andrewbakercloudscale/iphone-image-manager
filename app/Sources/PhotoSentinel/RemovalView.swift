import PhotoCore
import SwiftUI

/// Removing a clean-up from the phone, in the order the owner asked for:
/// prove Google Drive has it, plan it, type the phrase, remove, then show the proof.
///
/// Every step is a CLI task (TaskStore), so the sheet only reads their results. Closing it,
/// or the app, never stops a step; reopening shows where it got to.
struct RemovalSheet: View {
    @ObservedObject var model: Model
    let recipe: Recipe
    /// ImageRenderer draws nothing inside a ScrollView, so `--snapshot` turns it off.
    var scrolls = true
    @State private var typed = ""
    @State private var proof: Proof?
    @State private var routes: [Route]?

    /// An audit counts for six hours: long enough to plan and remove after it, short
    /// enough that a Drive change since is unlikely. The removal re-checks Drive anyway.
    private static let auditLife: TimeInterval = 6 * 3600
    /// A plan's count is what the phrase binds to. The removal re-plans and refuses on a
    /// different count, so an older plan only costs a retyped phrase, never a wrong removal.
    private static let planLife: TimeInterval = 3600

    private var stats: RecipeStats? { model.library?.recipes.first { $0.id == recipe.id } }
    private var audit: TaskStatus? { model.latest(.audit) }
    private var plan: TaskStatus? { model.latest(.plan, recipe: recipe) }
    private var removal: TaskStatus? {
        guard let r = model.latest(.remove, recipe: recipe) else { return nil }
        // Only the removal that follows the current plan belongs to this sheet.
        if let p = plan, r.task.startedAt < p.task.startedAt { return nil }
        return r
    }

    private var auditReport: AuditReport? { audit?.decode(AuditReport.self) }
    private var auditFresh: Bool {
        guard let a = audit, !a.running else { return false }
        return Date().timeIntervalSince(a.task.startedAt) < Self.auditLife
    }
    private var driveConfirmed: Bool { auditFresh && auditReport?.clean == true }
    /// The plan and the removal each re-read every item's hash from Drive themselves, so the
    /// whole-Drive check is extra assurance, not a gate. Only a check that found Drive
    /// missing something, or one still running, holds the removal back.
    private var driveBlocks: Bool { (audit?.running ?? false) || auditReport?.clean == false }

    private var planResult: RemovalPlan? { plan?.decode(RemovalPlan.self) }
    private var planFresh: Bool {
        guard let p = plan, !p.running else { return false }
        return Date().timeIntervalSince(p.task.startedAt) < Self.planLife
    }

    /// The phrase the CLI wants: the plan's, or the one a refused removal named because the
    /// phone changed in between.
    private var phrase: String? {
        if let msg = removal?.errorMessage, let n = RemovalPlan.expectedCount(inError: msg) { return RemovalPlan.phrase(n) }
        return planResult.map(\.phrase)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                SymbolTile(symbol: recipe.symbol, color: Tab.cleanUp.color, size: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text(recipe.title).font(.title2.weight(.bold))
                    Text(recipe.detail).font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Close") { model.removing = nil }.keyboardShortcut(.cancelAction)
            }
            .padding(18)
            Divider()
            if scrolls { ScrollView { steps } } else { steps }
        }
        .frame(width: 720, height: 760)
        .background(Color(light: "#f5f5f7", dark: "#1c1c1f"))
        .onChange(of: removal?.running) { loadProof() }
        .onChange(of: model.storage?.destinations) { routes = loadRoutes() }
        .onAppear { loadProof(); routes = loadRoutes() }
    }

    private var steps: some View {
        VStack(alignment: .leading, spacing: 14) {
            WhereTheyGo(model: model, routes: routes ?? loadRoutes())
            backupStep
            driveStep
            planStep
            removeStep
            proofStep
        }
        .padding(18)
    }

    // MARK: 1. Back up whatever is not in Drive yet

    @ViewBuilder private var backupStep: some View {
        let missing = stats?.notBackedUp ?? Tally()
        Step(number: 1, title: "Back up", done: missing.count == 0) {
            if missing.count == 0 {
                Text("Everything this clean-up selects is already in Google Drive.").font(.callout)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    Text("\(Fmt.count(missing.count)) items (\(Fmt.bytes(missing.bytes))) are only on the phone. They are never removed until Drive has them. You can still remove the rest now.")
                        .font(.callout).fixedSize(horizontal: false, vertical: true)
                    if recipe.backups.isEmpty {
                        Text("Back these up with the Camera videos and Screen recordings clean-ups.").font(.callout).foregroundStyle(.secondary)
                    } else {
                        let queued = model.queue.filter { q in recipe.backups.contains { $0.source == q.source && $0.type == q.type } }
                        let running = model.jobs.contains { j in
                            if case .running = j.state { return recipe.backups.contains { $0.source == j.source } }
                            return false
                        }
                        HStack {
                            if running {
                                Pill(text: "Backing up now · see Activity", symbol: "icloud.and.arrow.up.fill", color: Theme.good)
                            } else if !queued.isEmpty {
                                Pill(text: "Queued · starts when the running job ends", symbol: "clock.fill", color: Theme.warn)
                            } else {
                                CapsuleButton(title: "Back up to Google Drive", symbol: "icloud.and.arrow.up.fill", color: Tab.backup.color) { model.backUp(recipe) }
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: 2. Confirm Google Drive holds everything it is said to hold

    @ViewBuilder private var driveStep: some View {
        Step(number: 2, title: "Check all of Google Drive (optional)", done: driveConfirmed) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Re-reads the SHA-256 of every file in Google Drive and compares it with the hash taken when it was copied off the phone. Takes about 7 minutes. You can skip it: the plan and the removal re-check each item they remove in Drive anyway.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if let a = audit, a.running {
                    Running(text: "Asking Google Drive for every file's hash (about 7 minutes)…", since: a.task.startedAt)
                } else if let r = auditReport, auditFresh {
                    AuditSummary(report: r, at: audit!.task.startedAt)
                    if !r.clean { Text("Removal stays locked until this is clean.").font(.callout.weight(.semibold)).foregroundStyle(Theme.bad) }
                } else if let msg = audit?.errorMessage, auditFresh {
                    Text("The check could not finish: \(msg)").font(.callout).foregroundStyle(Theme.bad)
                } else if let a = audit {
                    // Without this the step shows only a button, and nothing says why 3 and 4 are locked.
                    Text("Last checked \(a.task.startedAt.formatted(date: .abbreviated, time: .shortened))\(auditReport?.clean == true ? ", and everything matched" : "").")
                        .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                if !(audit?.running ?? false) && !driveConfirmed || (auditReport != nil && !auditFresh) {
                    CapsuleButton(title: audit == nil ? "Check Google Drive" : "Check again", symbol: "checkmark.shield.fill", color: Theme.good, style: .soft) { model.runAudit() }
                }
            }
        }
    }

    // MARK: 3. The plan: what would go, and why anything stays

    @ViewBuilder private var planStep: some View {
        Step(number: 3, title: "Plan the removal", done: planFresh && planResult != nil, locked: driveBlocks) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Scans the phone now and re-reads each item's hash from Google Drive. Only items whose Drive copy matches are eligible. Takes about 4 minutes.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if let p = plan, p.running {
                    Running(text: "Scanning the phone and checking each item in Drive…", since: p.task.startedAt)
                } else if let r = planResult, planFresh {
                    PlanSummary(plan: r)
                    if let n = r.blockedReasons["flagged as a suspected proxy"], n > 0, recipe.filter.proxyTolerance < 1 {
                        lowResChoice(n)
                    }
                } else if let msg = plan?.errorMessage, planFresh {
                    Text(msg).font(.callout).foregroundStyle(Theme.bad).textSelection(.enabled)
                }
                if driveBlocks && !(plan?.running ?? false) {
                    Text((audit?.running ?? false) ? "Unlocks when the Drive check in step 2 finishes." : "Locked: the Drive check in step 2 found problems. Fix them and check again.")
                        .font(.callout).foregroundStyle(.secondary)
                }
                if !driveBlocks && !(plan?.running ?? false) && !(planFresh && planResult != nil && removal == nil) {
                    CapsuleButton(title: planResult == nil ? "Make the plan" : "Plan again", symbol: "list.bullet.clipboard.fill", color: Tab.cleanUp.color) { model.plan(recipe) }
                } else if planFresh && planResult != nil && removal == nil {
                    CapsuleButton(title: "Plan again", symbol: "arrow.clockwise", color: Tab.cleanUp.color, style: .ghost) { model.plan(recipe) }
                }
            }
        }
    }

    /// The owner's choice (SAFETY.md section 2): loosen the low-res check for this removal
    /// only. It becomes its own clean-up, so it needs its own plan.
    private func lowResChoice(_ n: Int) -> some View {
        HStack(spacing: 8) {
            Text("\(Fmt.count(n)) kept by the low-res check (\(recipe.filter.proxyTolerance == 0 ? "strict" : "relaxed")).")
                .font(.callout)
            Spacer()
            ForEach([(0.5, "Relax"), (1.0, "Turn off")].filter { $0.0 > recipe.filter.proxyTolerance }, id: \.0) { level, label in
                CapsuleButton(title: label, symbol: "slider.horizontal.3", color: Theme.warn, style: .soft) {
                    var f = recipe.filter
                    f.proxyTolerance = level
                    if f.name.isEmpty { f.name = recipe.title + (level >= 1 ? ", low-res check off" : ", low-res check relaxed") }
                    model.removing = Recipe(custom: f)
                }
            }
        }
        .help("A photo that is small for its size might be a low-res stand-in whose original is only in iCloud. Screenshots are often flagged without being stand-ins. Each item removed this way records its score.")
    }

    // MARK: 4. Remove

    @ViewBuilder private var removeStep: some View {
        let ready = !driveBlocks && planFresh && (planResult?.eligible ?? 0) > 0
        let done = removal.map { !$0.running && $0.errorMessage == nil } ?? false
        Step(number: 4, title: "Remove from iPhone", done: done, locked: !ready && removal == nil) {
            VStack(alignment: .leading, spacing: 10) {
                if let r = removal, r.running {
                    Running(text: "Removing. Click Delete when the dialog appears.", since: r.task.startedAt)
                    Text("It re-scans and re-checks Google Drive first, so the dialog comes after a few minutes.")
                        .font(.callout).foregroundStyle(.secondary)
                } else if done {
                    if let r = removal?.decode(RemovalPlan.self) {
                        Text("Removed \(Fmt.count(r.removed ?? 0)) items, \(Fmt.bytes(r.bytesRemoved ?? 0)).")
                            .font(.body.weight(.semibold)).foregroundStyle(Theme.good)
                    }
                    Text("They are in Recently Deleted on the phone for 30 days. Space comes back when that empties.")
                        .font(.callout).foregroundStyle(.secondary)
                } else if let phrase, ready || removal?.errorMessage != nil {
                    if let msg = removal?.errorMessage {
                        Text(RemovalPlan.expectedCount(inError: msg) != nil
                             ? "The phone changed since the plan, so the count changed. Type the new phrase."
                             : "The removal stopped: \(msg)")
                            .font(.callout).foregroundStyle(Theme.warn).fixedSize(horizontal: false, vertical: true)
                    }
                    Text("This deletes them from your iPhone and every device on the same iCloud account. They go to Recently Deleted on the phone for 30 days; the copies stay in \(model.storage?.accountTitle ?? "Google Drive"), in the folders above. Type the phrase to confirm:")
                        .font(.callout).fixedSize(horizontal: false, vertical: true)
                    Text(phrase).font(.body.monospaced().weight(.semibold)).textSelection(.enabled)
                    HStack {
                        TextField(phrase, text: $typed).textFieldStyle(.roundedBorder).font(.body.monospaced())
                        CapsuleButton(title: "Remove", symbol: "trash.fill", color: typed.lowercased() == phrase ? Theme.bad : .secondary) {
                            guard typed.lowercased() == phrase else { return }
                            model.remove(recipe, phrase: phrase)
                            typed = ""
                        }
                        .disabled(typed.lowercased() != phrase)
                    }
                    Text("Click Delete when the dialog appears.").font(.callout.weight(.semibold))
                } else {
                    Text("Unlocks when the plan in step 3 has items to remove.").font(.callout).foregroundStyle(.secondary)
                }
            }
        }
    }

    // MARK: 5. Proof

    @ViewBuilder private var proofStep: some View {
        if let p = proof, p.total > 0 {
            Step(number: 5, title: "Proof they are in Google Drive", done: p.allConfirmed) {
                ProofTable(proof: p)
            }
        }
    }

    private func loadRoutes() -> [Route] {
        guard let st = model.storage else { return [] }
        return (try? Ledger.routes(SQLiteReader(path: model.ledgerPath), recipe, storage: st)) ?? []
    }

    private func loadProof() {
        guard let r = removal, !r.running else { proof = nil; return }
        proof = try? Ledger.proof(SQLiteReader(path: model.ledgerPath), since: Ledger.stamp(r.task.startedAt))
    }
}

// MARK: Pieces

/// Every confirmation says where things go: which account, which folders, and how much of
/// it is there already. Both can be changed from here.
struct WhereTheyGo: View {
    @ObservedObject var model: Model
    let routes: [Route]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "externaldrive.fill.badge.icloud").foregroundStyle(Tab.backup.color)
                Text("Where they go").font(.headline)
                Text(model.storage?.accountTitle ?? "Google Drive").foregroundStyle(.secondary)
                Spacer()
                CapsuleButton(title: "Change folders", symbol: "folder.badge.gearshape", color: Tab.backup.color, style: .soft) {
                    model.removing = nil
                    model.tab = .backup
                }
                CapsuleButton(title: "Change account", symbol: "person.crop.circle.badge.questionmark", color: Tab.backup.color, style: .ghost) {
                    model.removing = nil
                    model.tab = .settings
                }
            }
            if routes.isEmpty {
                Text("Nothing on the phone matches this clean-up.").font(.callout).foregroundStyle(.secondary)
            }
            ForEach(routes) { r in
                HStack(spacing: 8) {
                    Image(systemName: "folder.fill").foregroundStyle(Tab.backup.color)
                    Text(r.folder).font(.callout.monospaced()).textSelection(.enabled).lineLimit(1).truncationMode(.middle)
                    Spacer()
                    Text(Self.detail(r)).font(.callout).foregroundStyle(.secondary).monospacedDigit()
                }
            }
            Text("Removed items go to Recently Deleted on the phone for 30 days. Nothing is deleted from Drive.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.cardFill))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Tab.backup.color.opacity(0.5), lineWidth: 1.5))
    }

    static func detail(_ r: Route) -> String {
        var parts: [String] = []
        if r.inDrive.count > 0 { parts.append("\(Fmt.count(r.inDrive.count)) there (\(Fmt.bytes(r.inDrive.bytes)))") }
        if r.toBackUp.count > 0 { parts.append("\(Fmt.count(r.toBackUp.count)) to back up (\(Fmt.bytes(r.toBackUp.bytes)))") }
        return parts.joined(separator: " · ")
    }
}

struct Step<Content: View>: View {
    let number: Int
    let title: String
    var done = false
    var locked = false
    @ViewBuilder var content: Content

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            ZStack {
                Circle().fill(done ? Theme.good : (locked ? Color.secondary.opacity(0.3) : Tab.cleanUp.color))
                if done { Image(systemName: "checkmark").font(.callout.weight(.bold)).foregroundStyle(.white) }
                else { Text("\(number)").font(.callout.weight(.bold)).foregroundStyle(.white) }
            }
            .frame(width: 28, height: 28)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.headline).foregroundStyle(locked ? .secondary : .primary)
                content.opacity(locked ? 0.55 : 1)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.cardFill))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(done ? Theme.good.opacity(0.5) : Theme.cardStroke, lineWidth: 1.5))
    }
}

struct Running: View {
    let text: String
    let since: Date

    var body: some View {
        TimelineView(.periodic(from: since, by: 1)) { ctx in
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(text).font(.callout.weight(.medium))
                Spacer()
                Text(Duration.seconds(ctx.date.timeIntervalSince(since)).formatted(.time(pattern: .minuteSecond)))
                    .font(.callout).monospacedDigit().foregroundStyle(.secondary)
            }
        }
    }
}

struct AuditSummary: View {
    let report: AuditReport
    let at: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: report.clean ? "checkmark.seal.fill" : "xmark.octagon.fill")
                    .foregroundStyle(report.clean ? Theme.good : Theme.bad)
                Text(report.clean
                     ? "\(Fmt.count(report.matched ?? 0)) of \(Fmt.count(report.claimedVerified ?? 0)) backed-up items are in Google Drive with matching hashes."
                     : "Google Drive does not hold everything the ledger says it does.")
                    .font(.callout.weight(.semibold))
            }
            Text("Checked \(at.formatted(date: .omitted, time: .shortened)) · \(Fmt.count(report.remoteFiles ?? 0)) files in Drive")
                .font(.caption).foregroundStyle(.secondary)
            ForEach((report.missing ?? []).prefix(5), id: \.id) { Text("Missing from Drive: \($0.path)").font(.caption).foregroundStyle(Theme.bad) }
            ForEach((report.hashMismatch ?? []).prefix(5), id: \.id) { Text("Hash differs: \($0.path)").font(.caption).foregroundStyle(Theme.bad) }
            ForEach(report.conflicts.prefix(5), id: \.self) { Text("\($0.kind): \($0.detail)").font(.caption).foregroundStyle(Theme.bad) }
        }
    }
}

struct PlanSummary: View {
    let plan: RemovalPlan

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.shield.fill").foregroundStyle(Theme.good)
                Text("\(Fmt.count(plan.eligible)) items, \(Fmt.bytes(plan.eligibleBytes)), can go. Each one's Drive copy was re-read just now and matched.")
                    .font(.callout.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            }
            if plan.blocked > 0 {
                Text("\(Fmt.count(plan.blocked)) stay on the phone:").font(.callout)
                ForEach(plan.blockedReasons.sorted { $0.value > $1.value }, id: \.key) { reason, n in
                    HStack {
                        Text("· \(Self.plain(reason))")
                        Spacer()
                        Text(Fmt.count(n)).monospacedDigit()
                    }
                    .font(.callout).foregroundStyle(.secondary)
                }
            }
        }
    }

    /// remove.py's block reasons in the owner's words.
    static func plain(_ r: String) -> String {
        switch r {
        case "flagged as a suspected proxy": "Might be a low-res stand-in for the real photo"
        case "no hash recorded, so the remote copy cannot be compared": "No fingerprint taken, so Drive's copy can't be checked"
        case "not verified in the cloud": "Not in Google Drive yet"
        case "not at the remote right now": "Missing from Google Drive right now"
        case "the remote copy does not match the recorded hash": "Drive's copy doesn't match"
        case "favourited": "Favourite"
        default: r.prefix(1).uppercased() + r.dropFirst()
        }
    }
}

struct ProofTable: View {
    let proof: Proof

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: proof.allConfirmed ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(proof.allConfirmed ? Theme.good : Theme.warn)
                Text("\(Fmt.count(proof.confirmed)) of \(Fmt.count(proof.total)) removed items had their Google Drive hash re-read and matched in the same run. \(Fmt.count(proof.stillVerified)) are still verified in Drive now.")
                    .font(.callout.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            }
            if !proof.rows.isEmpty {
                ScrollView {
                    Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 3) {
                        GridRow {
                            Text("File"); Text("In Google Drive at"); Text("SHA-256"); Text("")
                        }
                        .font(.caption.weight(.bold))
                        ForEach(proof.rows) { r in
                            GridRow {
                                Text(r.filename).lineLimit(1)
                                Text(r.drivePath).lineLimit(1).truncationMode(.head).foregroundStyle(.secondary)
                                Text(r.sha256.prefix(12) + "…").monospaced().foregroundStyle(.secondary)
                                Image(systemName: r.driveHashConfirmed && r.stillVerified ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                                    .foregroundStyle(r.driveHashConfirmed && r.stillVerified ? Theme.good : Theme.warn)
                            }
                            .font(.caption)
                            .textSelection(.enabled)
                        }
                    }
                }
                .frame(maxHeight: 220)
                if proof.rows.count < proof.total { Text("Showing the latest \(Fmt.count(proof.rows.count)).").font(.caption).foregroundStyle(.secondary) }
            }
        }
    }
}
