import Foundation

/// One row on the Checkup tab: something worth doing, and the one action that does it.
public struct Finding: Identifiable, Hashable, Sendable {
    public enum Kind: String, Sendable { case safety, space, job, info }
    public enum Action: Hashable, Sendable {
        case resume(String)      // a job id
        case recipe(String)      // a recipe id: opens Clean up
        case openTab(String)
        case none
    }

    public let id: String
    public let kind: Kind
    public let symbol: String
    public let title: String
    public let detail: String
    public let actionTitle: String
    public let action: Action
    /// Bytes this would free, for ordering.
    public let bytes: Int64
}

public enum Detector {
    /// Safety first, then stopped jobs, then space by size. Pure: tested without a ledger.
    public static func findings(library s: LibrarySnapshot, jobs: [Job], doctor: DoctorReport?) -> [Finding] {
        var out: [Finding] = []

        for c in doctor?.results ?? [] where c.failed {
            out.append(Finding(id: "doctor.\(c.name)", kind: .safety, symbol: "exclamationmark.triangle.fill",
                               title: "\(c.name.prefix(1).uppercased())\(c.name.dropFirst()): \(c.detail)",
                               detail: c.remedy.isEmpty ? "Run doctor for details." : c.remedy,
                               actionTitle: "Open Settings", action: .openTab("Settings"), bytes: .max))
        }

        let phoneOnly = s.channels.filter { $0.channel == .camera }
            .reduce(Tally()) { Tally(count: $0.count + $1.total.count - $1.verified.count, bytes: $0.bytes + $1.total.bytes - $1.verified.bytes) }
        if phoneOnly.count > 0 {
            let running = jobs.contains { $0.source == "camera" && $0.state == .running }
            out.append(Finding(id: "camera.unprotected", kind: .safety, symbol: "exclamationmark.shield.fill",
                               title: "\(Fmt.count(phoneOnly.count)) camera photos and videos are only on your phone",
                               detail: "\(Fmt.bytes(phoneOnly.bytes)) with no copy in Google Drive." + (running ? " Backing up now." : ""),
                               actionTitle: running ? "Watch progress" : "Back up", action: .openTab("Activity"), bytes: phoneOnly.bytes))
        }

        for j in jobs {
            switch j.state {
            case .stopped(let why):
                out.append(Finding(id: "job.\(j.id)", kind: .job, symbol: "pause.circle.fill",
                                   title: "\(j.title) backup stopped", detail: why,
                                   actionTitle: "Resume", action: .resume(j.id), bytes: .max - 1))
            case .paused(let why):
                out.append(Finding(id: "job.\(j.id)", kind: .job, symbol: "pause.circle.fill",
                                   title: "\(j.title) backup is paused", detail: why,
                                   actionTitle: "Show", action: .openTab("Activity"), bytes: .max - 2))
            default: break
            }
        }

        let removals = s.unfinishedOperations.filter(\.needsPerson)
        if !removals.isEmpty {
            out.append(Finding(id: "journal.remove", kind: .job, symbol: "hand.raised.fill",
                               title: "\(removals.count) removal\(removals.count == 1 ? "" : "s") never finished",
                               detail: "Usually the Delete dialog was not clicked, and nothing was removed. It is re-planned and Drive re-checked before it runs again.",
                               actionTitle: "Review", action: .openTab("Activity"), bytes: .max - 3))
        }

        for r in s.recipes where r.recipe.kind != .largeVideos {
            if r.removableNow.count > 0 {
                out.append(Finding(id: "ready.\(r.id)", kind: .space, symbol: r.recipe.symbol,
                                   title: "About \(Fmt.count(r.removableNow.count)) \(noun(r.recipe)) are safe in Google Drive",
                                   detail: "\(Fmt.bytes(r.removableNow.bytes)) you can take off the phone now.",
                                   actionTitle: "Remove from iPhone", action: .recipe(r.id), bytes: r.removableNow.bytes))
            } else if r.notBackedUp.count > 0 {
                out.append(Finding(id: "todo.\(r.id)", kind: .space, symbol: r.recipe.symbol,
                                   title: "\(Fmt.count(r.selected.count)) \(noun(r.recipe))",
                                   detail: "\(Fmt.bytes(r.selected.bytes)). Each gets a copy in Drive first; you see them before anything goes.",
                                   actionTitle: "Clean up", action: .recipe(r.id), bytes: r.selected.bytes))
            }
        }

        return out.sorted { a, b in
            a.kind == b.kind ? a.bytes > b.bytes : rank(a.kind) < rank(b.kind)
        }
    }

    static func rank(_ k: Finding.Kind) -> Int {
        switch k { case .safety: 0; case .job: 1; case .space: 2; case .info: 3 }
    }

    static func noun(_ r: Recipe) -> String {
        let age = r.age.map { " older than \($0.words)" } ?? ""
        switch r.kind {
        case .oldVideos: return "camera videos" + age
        case .whatsapp: return "WhatsApp items" + age
        case .screenshots: return "screenshots" + age
        case .screenRecordings: return "screen recordings" + age
        case .largeVideos: return "large videos"
        case .custom: return "items in \u{201C}\(r.title)\u{201D}"
        }
    }
}
