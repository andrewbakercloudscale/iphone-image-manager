import PhotoCore
import SwiftUI

/// Jobs in flight, and resuming them: the owner's ask of 2026-10-25.
struct ActivityView: View {
    @ObservedObject var model: Model

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let j = model.activeJob { hero(j) }
            Card(title: "Jobs in flight", symbol: "waveform.path.ecg", tint: Tab.activity.color,
                 trailing: AnyView(Text("each resumes where it stopped; nothing is redone").font(.callout).foregroundStyle(.secondary))) {
                VStack(alignment: .leading, spacing: 8) {
                    if model.jobs.isEmpty { Text("No backup jobs in cycle.log yet.").foregroundStyle(.secondary) }
                    ForEach(model.jobs) { j in JobRow(job: j) { model.resume(j.id) } }
                }
            }
            if !model.queue.isEmpty {
                Card(title: "Waiting to start", symbol: "clock.fill", tint: Tab.activity.color,
                     trailing: AnyView(Text("one export at a time; the next starts when the running job ends").font(.callout).foregroundStyle(.secondary))) {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(model.queue) { q in
                            HStack {
                                Text(q.title).font(.body.weight(.medium))
                                Spacer()
                                Button("Cancel") { model.cancelQueued(q) }.buttonStyle(.link)
                            }
                        }
                    }
                }
            }
            if !model.tasks.isEmpty {
                Card(title: "Checks and removals", symbol: "checkmark.shield.fill", tint: Tab.activity.color) {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(model.tasks.prefix(8), id: \.task.id) { t in TaskRow(status: t) }
                    }
                }
            }
            if let ops = model.library?.unfinishedOperations, !ops.isEmpty {
                Card(title: "Unfinished steps from earlier runs", symbol: "list.bullet.clipboard", tint: Tab.activity.color) {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(ops) { op in
                            HStack(spacing: 10) {
                                Image(systemName: op.needsPerson ? "hand.raised.fill" : "arrow.triangle.2.circlepath")
                                    .foregroundStyle(op.needsPerson ? Theme.warn : Theme.good)
                                Text(op.plain).font(.body.weight(.medium))
                                Text(local(op.startedAt)).foregroundStyle(.secondary).monospacedDigit()
                                Spacer()
                                if op.needsPerson {
                                    Text("Photos may have deleted them before the app heard back").font(.callout).foregroundStyle(.secondary)
                                    CapsuleButton(title: "Finish from latest scan", symbol: "checkmark.circle.fill", color: Tab.cleanUp.color) {
                                        model.settleRemovals()
                                    }
                                    .help("Checks the newest scan of the phone: items it no longer saw were removed and are recorded as removed; items still there are released to be planned again. Nothing on the phone is touched.")
                                } else {
                                    Text(inProgress(op, ops) ? "In progress now" : "Safe: the next run picks it up")
                                        .font(.callout).foregroundStyle(.secondary)
                                }
                            }
                        }
                        Text("A removal never resumes by itself. Copy, upload and check steps are safe to repeat: anything already verified in Drive is skipped.")
                            .font(.callout).foregroundStyle(.secondary).padding(.top, 4)
                    }
                }
            }
        }
    }

    /// The newest step of its kind, started in the last 3 hours while a job runs, is that
    /// job's current step rather than a leftover.
    private func inProgress(_ op: PhotoCore.Operation, _ ops: [PhotoCore.Operation]) -> Bool {
        guard model.activeJob != nil, ops.first(where: { $0.operation == op.operation })?.id == op.id,
              let d = ISO8601DateFormatter().date(from: op.startedAt) else { return false }
        return Date().timeIntervalSince(d) < 3 * 3600
    }

    private func local(_ iso: String) -> String {
        guard let d = ISO8601DateFormatter().date(from: iso) else { return String(iso.prefix(16)) }
        return d.formatted(date: .abbreviated, time: .shortened)
    }

    private func hero(_ j: Job) -> some View {
        let paused: String? = { if case .paused(let why) = j.state { return why }; return nil }()
        return VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 14) {
                Image(systemName: paused == nil ? "icloud.and.arrow.up.fill" : "pause.fill").font(.system(size: 36, weight: .semibold))
                VStack(alignment: .leading, spacing: 3) {
                    Text("Backing up \(j.title)" + (paused == nil ? "" : " · paused")).font(.title2.weight(.bold))
                    Text(paused ?? j.lastLine).font(.body).lineLimit(2)
                }
                Spacer()
            }
            if let p = j.progress, let step = j.step {
                HStack {
                    Text(step.prefix(1).uppercased() + step.dropFirst())
                    Spacer()
                    Text(Fmt.pct(p))
                }
                .font(.callout.weight(.semibold)).monospacedDigit()
                Meter(value: p, color: .white, height: 10).opacity(0.95)
            }
        }
        .foregroundStyle(.white)
        .padding(20)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.calmGradient))
    }
}

struct JobRow: View {
    let job: Job
    var compact = false
    let resume: () -> Void

    private var look: (color: Color, label: String, why: String?) {
        switch job.state {
        case .running: (Theme.good, "Running", nil)
        case .paused(let why): (Theme.warn, "Paused", why)
        case .stopped(let why): (Theme.bad, "Stopped", why)
        case .finished: (Color.secondary, "Finished", nil)
        }
    }

    var body: some View {
        let l = look
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(l.color).frame(width: 10, height: 10).padding(.top, 5)
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(job.title).font(.body.weight(.semibold))
                    Text(l.label).font(.callout.weight(.semibold)).foregroundStyle(l.color)
                    if job.state != .finished, let step = job.step {
                        Text("· \(step)").font(.callout).foregroundStyle(.secondary).monospacedDigit()
                    }
                }
                if let why = l.why { Text(why).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
                if !compact { Text("Last: \(job.lastAt) · \(job.lastLine)").font(.caption).foregroundStyle(.tertiary).lineLimit(1) }
                if !compact, let p = job.progress, job.state != .finished { Meter(value: p, color: l.color, height: 6).padding(.top, 2) }
            }
            Spacer(minLength: 6)
            if case .stopped = job.state {
                CapsuleButton(title: "Resume", symbol: "play.fill", color: Tab.cleanUp.color, action: resume)
                    .help("Runs cycle.sh \(job.resumeArguments.joined(separator: " ")) under caffeinate. Backup only: nothing is removed from the phone.")
            }
        }
        .padding(compact ? 6 : 10)
        .background(RoundedRectangle(cornerRadius: 10).fill(l.color.opacity(0.07)))
    }
}

struct TaskRow: View {
    let status: TaskStatus

    var body: some View {
        let failed = status.errorMessage != nil
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(status.running ? Theme.good : (failed ? Theme.bad : Color.secondary)).frame(width: 10, height: 10).padding(.top, 5)
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(status.task.title).font(.body.weight(.semibold))
                    Text(status.running ? "Running" : (failed ? "Stopped" : "Done")).font(.callout.weight(.semibold))
                        .foregroundStyle(status.running ? Theme.good : (failed ? Theme.bad : .secondary))
                }
                Text(summary).font(.callout).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
            Text(status.task.startedAt.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.tertiary)
        }
    }

    private var summary: String {
        if status.running { return status.task.kind == .remove ? "Click Delete when the dialog appears." : "Working…" }
        if let e = status.errorMessage { return e }
        switch status.task.kind {
        case .audit:
            guard let r = status.decode(AuditReport.self) else { return "" }
            return r.clean ? "\(Fmt.count(r.matched ?? 0)) items in Google Drive, every hash matches." : "Google Drive is missing items or hashes differ."
        case .plan:
            guard let p = status.decode(RemovalPlan.self) else { return "" }
            return "\(Fmt.count(p.eligible)) can go (\(Fmt.bytes(p.eligibleBytes))), \(Fmt.count(p.blocked)) stay."
        case .settle:
            guard let r = status.decode(SettleReport.self) else { return "" }
            return "\(Fmt.count(r.confirmedGone)) confirmed removed (\(Fmt.bytes(r.bytesGone))), \(Fmt.count(r.stillOnPhone)) still on the phone."
        case .recover:
            guard let r = status.decode(RecoverReport.self) else { return "" }
            return "\(Fmt.count(r.verified)) of \(Fmt.count(r.files.count)) downloaded and matched their fingerprint."
        case .refile:
            guard let r = status.decode(RefileReport.self) else { return "" }
            return "Moved \(Fmt.count(r.moved ?? 0)) of \(Fmt.count(r.toMove)) in Google Drive, each found by its fingerprint at the new place."
        case .backup:
            return "Copied off the phone, uploaded, and checked in Google Drive."
        case .places:
            guard let p = status.decode(PlacesReport.self) else { return "" }
            return p.degraded ?? "\(Fmt.count(p.located)) items placed in \(Fmt.count(p.places)) places."
        case .remove:
            guard let p = status.decode(RemovalPlan.self) else { return "" }
            return "Removed \(Fmt.count(p.removed ?? 0)), \(Fmt.bytes(p.bytesRemoved ?? 0))."
        }
    }
}
