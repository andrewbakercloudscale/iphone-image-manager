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
    private var stats: RecipeStats? { model.library?.recipes.first { $0.id == recipe.id } }
    private var audit: TaskStatus? { model.latest(.audit) }
    private var lastRemoval: TaskStatus? { model.latest(.remove, recipe: recipe) }
    /// A removal that had already finished when the sheet opened. The sheet starts from a
    /// fresh Remove step then, with that one shown only as "Last time".
    @State private var spent: String?
    private var usedUp: Bool { spent != nil && lastRemoval?.task.id == spent }
    private var removal: TaskStatus? { usedUp ? nil : lastRemoval }

    private var auditReport: AuditReport? { audit?.decode(AuditReport.self) }
    private var auditFresh: Bool {
        guard let a = audit, !a.running else { return false }
        return Date().timeIntervalSince(a.task.startedAt) < Self.auditLife
    }
    private var driveConfirmed: Bool { auditFresh && auditReport?.clean == true }
    /// The removal re-reads every item's hash from Google Drive itself, so the whole-Drive check is extra assurance, not a gate. Only a check that found Drive
    /// missing something, or one still running, holds the removal back.
    private var driveBlocks: Bool { (audit?.running ?? false) || auditReport?.clean == false }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                SymbolTile(symbol: recipe.symbol, color: Tab.cleanUp.color, size: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text(recipe.title).font(.title2.weight(.bold))
                    Text(recipe.detail).font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                CapsuleButton(title: "Close", symbol: "xmark", color: Theme.bad) { model.removing = nil }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(18)
            Divider()
            if scrolls { ScrollView { steps } } else { steps }
        }
        .frame(width: 720, height: 760)
        .background(Color(light: "#f5f5f7", dark: "#1c1c1f"))
        .onChange(of: removal?.running) { loadProof() }
        .onChange(of: model.storage?.destinations) { routes = loadRoutes() }
        .onAppear {
            if let r = lastRemoval, !r.running, r.errorMessage == nil { spent = r.task.id }
            loadProof(); routes = loadRoutes()
        }
    }

    private var steps: some View {
        VStack(alignment: .leading, spacing: 14) {
            WhereTheyGo(model: model, routes: routes ?? loadRoutes())
            backupStep
            driveStep
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
                    Text("\(Fmt.count(missing.count)) items (\(Fmt.bytes(missing.bytes))) are only on the phone. They are never removed until Google Drive has them. You can still remove the rest now.")
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
                                Pill(text: "Backing up now", symbol: "icloud.and.arrow.up.fill", color: Theme.good)
                                CapsuleButton(title: "Open Activity", symbol: "waveform.path.ecg", color: Tab.activity.color, style: .ghost) {
                                    model.removing = nil
                                    model.tab = .activity
                                }
                            } else if !queued.isEmpty {
                                if let ahead = model.runningBackup {
                                    Pill(text: "Waiting for the \(ahead.title) backup to end", symbol: "clock.fill", color: Theme.warn)
                                    CapsuleButton(title: "Back these up first", symbol: "arrow.up.to.line", color: Tab.backup.color) { model.backUpFirst(recipe) }
                                        .help("Stops the \(ahead.title) backup, backs these up, then carries on with \(ahead.title) from where it was. Nothing is lost: what is already in Google Drive stays there.")
                                } else {
                                    Pill(text: "Queued · starting", symbol: "clock.fill", color: Theme.warn)
                                }
                            } else {
                                CapsuleButton(title: "Back up to Google Drive", symbol: "icloud.and.arrow.up.fill", color: Tab.backup.color) { model.backUp(recipe) }
                            }
                        }
                        if running, let step = model.jobs.first(where: { j in j.state == .running && recipe.backups.contains { $0.source == j.source } })?.step {
                            Text(step.prefix(1).uppercased() + step.dropFirst() + ".").font(.callout).foregroundStyle(.secondary)
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
                Text("Re-reads the SHA-256 of every file in Google Drive and compares it with the hash taken when it was copied off the phone. Takes about 7 minutes. You can skip it: the removal re-checks each item it removes in Google Drive anyway.")
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

    // MARK: 3. Remove: one run plans afresh, re-checks Google Drive, then removes

    @ViewBuilder private var removeStep: some View {
        let done = removal.map { !$0.running && $0.errorMessage == nil } ?? false
        let estimate = stats?.removableNow.count ?? 0
        Step(number: 3, title: "Remove from iPhone", done: done, locked: driveBlocks && removal == nil) {
            VStack(alignment: .leading, spacing: 10) {
                if let r = removal, r.running {
                    Text("Removing \(recipe.title.lowercased()) that are safe in Google Drive, and nothing else.")
                        .font(.body.weight(.semibold))
                    Running(text: (r.progressText ?? "Getting ready") + "…", since: r.task.startedAt)
                    Text("It looks through your whole library to find them and re-checks each one in Google Drive. Everything else is only read, never changed. Then macOS asks to allow deleting them, with the exact count: click Delete in that box.")
                        .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                } else if done, let r = removal?.decode(RemovalPlan.self) {
                    Text("Removed \(Fmt.count(r.removed ?? 0)) items, \(Fmt.bytes(r.bytesRemoved ?? 0)).")
                        .font(.body.weight(.semibold)).foregroundStyle(Theme.good)
                    Text("They are in Recently Deleted on the phone for 30 days. Space comes back when that empties.")
                        .font(.callout).foregroundStyle(.secondary)
                    if r.blocked > 0 { PlanSummary(plan: r, kept: true) }
                } else if driveBlocks {
                    Text((audit?.running ?? false) ? "Unlocks when the Google Drive check in step 2 finishes." : "Locked: the Google Drive check in step 2 found problems. Fix them and check again.")
                        .font(.callout).foregroundStyle(.secondary)
                } else {
                    if usedUp, let last = lastRemoval?.decode(RemovalPlan.self) {
                        Text("Last time: removed \(Fmt.count(last.removed ?? 0)) items, \(Fmt.bytes(last.bytesRemoved ?? 0)), on \(lastRemoval!.task.startedAt.formatted(date: .abbreviated, time: .shortened)).")
                            .font(.callout).foregroundStyle(Theme.good)
                    }
                    let missing = stats?.notBackedUp.count ?? 0
                    if estimate == 0 && missing > 0 {
                        // Running it now would scan the whole library for 4 minutes to say this.
                        Callout(text: "Nothing can come off yet: none of the \(Fmt.count(missing)) are in Google Drive. Back them up in step 1 above. Remove opens here when that finishes.")
                    } else {
                        if let msg = removal?.errorMessage, !usedUp {
                            Callout(text: msg.contains("nothing is safe to remove")
                                    ? "Nothing was removed: none of them are safely in Google Drive yet. Back them up in step 1 above."
                                    : "The last try stopped: \(msg)")
                        }
                        removeForm(estimate: estimate)
                    }
                }
            }
        }
    }

    @ViewBuilder private func removeForm(estimate: Int) -> some View {
        Text("Removes \(estimate > 0 ? "about \(Fmt.count(estimate)) " : "")\(recipe.title.lowercased()) whose copy in Google Drive is checked again first. Anything not safely there stays on the phone. Removed items go to Recently Deleted for 30 days, on this iPhone and every device on the same iCloud account.")
            .font(.callout).fixedSize(horizontal: false, vertical: true)
        Text("Type Confirm to start:").font(.callout)
        HStack {
            TextField("Confirm", text: $typed).textFieldStyle(.roundedBorder)
                .onSubmit { start() }
            CapsuleButton(title: "Remove", symbol: "trash.fill", color: confirmed ? Theme.bad : .secondary) { start() }
                .disabled(!confirmed)
        }
        Text("Takes about 4 minutes. Then macOS shows the exact count and asks to allow deleting them: click Delete in that box.")
            .font(.callout).foregroundStyle(.secondary)
    }

    private var confirmed: Bool { typed.trimmingCharacters(in: .whitespaces).lowercased() == "confirm" }

    private func start() {
        guard confirmed else { return }
        model.remove(recipe, phrase: RemovalPlan.currentPhrase)
        typed = ""
    }

    // MARK: 5. Proof

    @ViewBuilder private var proofStep: some View {
        if let p = proof, p.total > 0 {
            Step(number: 4, title: "Proof they are in Google Drive", done: p.allConfirmed) {
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
    @State private var opening: String?
    @State private var linkError: String?

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
                    // Only a folder something is already in exists in Google Drive to be opened.
                    if r.inDrive.count > 0, !r.folder.hasPrefix("elsewhere in ") {
                        CapsuleButton(title: opening == r.folder ? "Opening…" : "View in GDrive", symbol: "arrow.up.forward.square",
                                      color: Tab.backup.color, style: .ghost) { open(r.folder) }
                            .disabled(opening != nil)
                            .help("Opens this folder in Google Drive in your browser.")
                    }
                }
            }
            if let linkError {
                Text(linkError).font(.caption).foregroundStyle(Theme.bad)
            }
            Text("Removed items go to Recently Deleted on the phone for 30 days. Nothing is deleted from Google Drive.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.cardFill))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Tab.backup.color.opacity(0.5), lineWidth: 1.5))
    }

    private func open(_ folder: String) {
        let engine = model.engine
        opening = folder
        linkError = nil
        Task {
            switch await Task.detached(operation: { Result { try engine.folderLink(folder) } }).value {
            case .success(let url): NSWorkspace.shared.open(url)
            case .failure(let e): linkError = "Could not open it: \((e as? JobError)?.text ?? "\(e)")"
            }
            opening = nil
        }
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
                // A done step keeps its number, so "step 3" still points at it; green says done.
                Text("\(number)").font(.callout.weight(.bold)).foregroundStyle(.white)
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
            Text("Checked \(at.formatted(date: .omitted, time: .shortened)) · \(Fmt.count(report.remoteFiles ?? 0)) files in Google Drive")
                .font(.caption).foregroundStyle(.secondary)
            ForEach((report.missing ?? []).prefix(5), id: \.id) { Text("Missing from Google Drive: \($0.path)").font(.caption).foregroundStyle(Theme.bad) }
            ForEach((report.hashMismatch ?? []).prefix(5), id: \.id) { Text("Hash differs: \($0.path)").font(.caption).foregroundStyle(Theme.bad) }
            ForEach(report.conflicts.prefix(5), id: \.self) { Text("\($0.kind): \($0.detail)").font(.caption).foregroundStyle(Theme.bad) }
        }
    }
}

struct PlanSummary: View {
    let plan: RemovalPlan
    /// After a removal: only what stayed, since the headline already says what went.
    var kept = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !kept { HStack(spacing: 6) {
                Image(systemName: plan.eligible > 0 ? "checkmark.shield.fill" : "info.circle.fill")
                    .foregroundStyle(plan.eligible > 0 ? Theme.good : Color.secondary)
                Text(plan.eligible > 0
                     ? "\(Fmt.count(plan.eligible)) items, \(Fmt.bytes(plan.eligibleBytes)), can go. Each one's Google Drive copy was re-read just now and matched."
                     : "Nothing can go yet. Everything this clean-up found stays on the phone, for the reasons below.")
                    .font(.callout.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            } }
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
        case "Drive's copy is not the size of the original": "Google Drive's copy is smaller than the original, so it may be a low-res stand-in"
        case "no hash recorded, so the remote copy cannot be compared": "No fingerprint taken, so Google Drive's copy can't be checked"
        case "not verified in the cloud": "Not in Google Drive yet"
        case "not at the remote right now": "Missing from Google Drive right now"
        case "the remote copy does not match the recorded hash": "Google Drive's copy doesn't match"
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
                Text("\(Fmt.count(proof.confirmed)) of \(Fmt.count(proof.total)) removed items had their Google Drive hash re-read and matched in the same run. \(Fmt.count(proof.stillVerified)) are still verified in Google Drive now.")
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
