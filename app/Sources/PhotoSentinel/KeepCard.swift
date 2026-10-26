import PhotoCore
import SwiftUI

/// "What stays on your phone": an age per type of media, and what each choice does to the
/// phone's storage, live. The same ages drive Checkup and the clean-ups.
struct KeepCard: View {
    @ObservedObject var model: Model
    let library: LibrarySnapshot

    static let ages = ["off", "30d", "90d", "6m", "1y", "18m", "2y", "3y", "5y"]
    static let kinds: [Recipe.Kind] = [.cameraPhotos, .oldVideos, .whatsapp, .screenshots, .screenRecordings]

    private func stats(_ kind: Recipe.Kind) -> RecipeStats? { library.recipes.first { $0.recipe.kind == kind } }

    /// What this type holds on the phone now.
    private func onPhone(_ kind: Recipe.Kind) -> Tally {
        func ch(_ c: Channel) -> ChannelStats? { library.channels.first { $0.channel == c } }
        switch kind {
        case .cameraPhotos: return ch(.camera)?.photos ?? Tally()
        case .oldVideos: return ch(.camera)?.videos ?? Tally()
        case .whatsapp: return ch(.whatsapp)?.total ?? Tally()
        case .screenshots: return ch(.screenshot)?.total ?? Tally()
        default: return ch(.screenRecording)?.total ?? Tally()
        }
    }

    private func name(_ kind: Recipe.Kind) -> String {
        switch kind {
        case .cameraPhotos: "Camera photos"
        case .oldVideos: "Camera videos"
        case .whatsapp: "WhatsApp"
        case .screenshots: "Screenshots"
        default: "Screen recordings"
        }
    }

    private func color(_ kind: Recipe.Kind) -> Color {
        switch kind {
        case .cameraPhotos, .oldVideos: Theme.color(.camera)
        case .whatsapp: Theme.color(.whatsapp)
        case .screenshots: Theme.color(.screenshot)
        default: Theme.color(.screenRecording)
        }
    }

    private func words(_ age: String) -> String { age == "off" ? "Keep all" : "Keep last " + (Age(age)?.words ?? age) }

    var body: some View {
        let rows = Self.kinds.compactMap(stats)
        let today = rows.reduce(Int64(0)) { $0 + $1.removableNow.bytes }
        let later = rows.reduce(Int64(0)) { $0 + $1.notBackedUp.bytes }
        let frees = rows.reduce(Int64(0)) { $0 + $1.selected.bytes }
        let after = max(library.onPhone.bytes - frees, 0)
        Card(title: "What stays on your phone", symbol: "iphone.gen3", tint: Tab.library.color,
             trailing: model.loading ? AnyView(ProgressView().controlSize(.small)) : nil) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(Fmt.bytes(library.onPhone.bytes)).font(.title.weight(.bold)).monospacedDigit()
                    Image(systemName: "arrow.right").font(.title3.weight(.bold)).foregroundStyle(.secondary)
                    Text(Fmt.bytes(after)).font(.title.weight(.bold)).foregroundStyle(Theme.good).monospacedDigit()
                    Text("on your iPhone with these ages · frees \(Fmt.bytes(frees))").font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    if later > 0 {
                        RowButton(title: "Back up all", symbol: "icloud.and.arrow.up.fill", color: Tab.backup.color) {
                            for r in rows where r.notBackedUp.count > 0 { model.backUp(r.recipe) }
                        }
                        .help("Queues a backup for every type below; one runs at a time. Nothing leaves the phone.")
                    }
                    if frees > 0 && model.plan.isEmpty {
                        RowButton(title: "Apply my plan…", symbol: "checklist", color: Tab.library.color, style: .fill) {
                            model.applyPlan(rows)
                        }
                        .help("Backs up everything these ages need, then walks you through each type's removal: the plan, the typed phrase, and Delete in the dialog.")
                    }
                }
                if !model.plan.isEmpty { planBanner }
                StackBar(parts: [(Tab.library.color.opacity(0.35), Double(after)), (Theme.good, Double(today)), (Theme.warn, Double(later))])
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                HStack(spacing: 16) {
                    legend(Tab.library.color.opacity(0.35), "Stays: \(Fmt.bytes(after))")
                    legend(Theme.good, "Already in Google Drive, can go now: \(Fmt.bytes(today))")
                    legend(Theme.warn, "Back up first: \(Fmt.bytes(later))")
                }
                .font(.caption)
                Divider()
                HStack(spacing: 12) {
                    Text("TYPE").frame(width: 210, alignment: .leading)
                    Text("KEEP ON PHONE").frame(width: 170, alignment: .leading)
                    Text("OLDER ITEMS GO TO GOOGLE DRIVE").frame(maxWidth: .infinity, alignment: .leading)
                }
                .sectionLabel()
                ForEach(Self.kinds, id: \.self) { kind in row(kind) }
                rules
                Text("Favourites, your own albums and anything not yet checked in Google Drive always stay. Removing shows the exact plan first.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .onChange(of: model.ages) { model.refresh() }
    }

    /// Where "Apply my plan" is: each type, done or next, and a way to stop.
    private var planBanner: some View {
        let next = model.plan.first { !model.planDone.contains($0.id) }
        return HStack(spacing: 10) {
            Image(systemName: "checklist").foregroundStyle(Tab.library.color)
            ForEach(model.plan) { r in
                let done = model.planDone.contains(r.id) && r.id != model.removing?.id
                Label(name(r.kind), systemImage: done ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(done ? Theme.good : .secondary)
            }
            Spacer()
            if let next, model.removing == nil {
                RowButton(title: "Next: \(name(next.kind))…", symbol: "arrow.right.circle.fill", color: Tab.library.color, style: .fill) { model.planNext() }
            }
            RowButton(title: "Stop", symbol: "xmark", color: .secondary) { model.stopPlan() }
        }
        .font(.callout)
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10).fill(Tab.library.color.opacity(0.1)))
    }

    /// The owner's own rules (saved filters), and a way to add one: by size, place, app or year.
    private var rules: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider()
            HStack {
                Text("YOUR OWN RULES").sectionLabel()
                Spacer()
                RowButton(title: "Add a rule", symbol: "plus.circle.fill", color: Tab.library.color) {
                    model.draft = Filter()
                    model.openBuilder()
                }
                .help("Build one from app, kind, age, size, year or place, and save it with a name. It shows up here.")
            }
            let custom = library.recipes.filter { $0.recipe.kind == .custom }
            if custom.isEmpty {
                Text("For example: videos bigger than 500 MB, WhatsApp from 2023, or everything taken in Greece.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(custom) { r in
                HStack(spacing: 12) {
                    SymbolTile(symbol: r.recipe.symbol, color: Tab.cleanUp.color, size: 28)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(r.recipe.title).font(.callout.weight(.semibold)).lineLimit(1)
                        Text(r.recipe.filter.summary).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Spacer()
                    Text("frees \(Fmt.bytes(r.selected.bytes)) · \(Fmt.bytes(r.removableNow.bytes)) already in Google Drive")
                        .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                    RowButton(title: "Edit", symbol: "slider.horizontal.3", color: Tab.library.color) {
                        model.draft = r.recipe.filter
                        model.openBuilder()
                    }
                    if r.selected.count > 0 {
                        RowButton(title: "Back up & remove…", symbol: "iphone.and.arrow.forward", style: .fill) { model.removing = r.recipe }
                    }
                }
            }
        }
    }

    private func legend(_ c: Color, _ text: String) -> some View {
        HStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 3).fill(c).frame(width: 10, height: 10)
            Text(text).monospacedDigit()
        }
    }

    private func row(_ kind: Recipe.Kind) -> some View {
        let total = onPhone(kind)
        let r = stats(kind)
        let frees = r?.selected.bytes ?? 0
        return HStack(spacing: 12) {
            SymbolTile(symbol: Recipe(kind: kind).symbol, color: color(kind), size: 28)
            VStack(alignment: .leading, spacing: 1) {
                Text(name(kind)).font(.callout.weight(.semibold))
                Text("\(Fmt.bytes(total.bytes)) · \(Fmt.count(total.count)) on phone").font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
            .frame(width: 170, alignment: .leading)
            Picker("", selection: model.ageBinding(kind)) {
                ForEach(Self.ages, id: \.self) { Text(words($0)).tag($0) }
            }
            .labelsHidden().fixedSize().frame(width: 170, alignment: .leading)
            .help("Keep this much on the phone. Older items can come off once Google Drive has them.")
            VStack(alignment: .leading, spacing: 3) {
                StackBar(parts: [(color(kind).opacity(0.35), Double(max(total.bytes - frees, 0))),
                                 (Theme.good, Double(r?.removableNow.bytes ?? 0)), (Theme.warn, Double(r?.notBackedUp.bytes ?? 0))], height: 8)
                    .clipShape(Capsule())
                Text(r.map { "frees \(Fmt.bytes($0.selected.bytes)) · \(Fmt.bytes($0.removableNow.bytes)) already in Google Drive · \(Fmt.bytes($0.notBackedUp.bytes)) to back up first" }
                     ?? "keeps all \(Fmt.bytes(total.bytes))")
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit().lineLimit(1)
            }
            .frame(maxWidth: .infinity)
            HStack(spacing: 6) {
                if let r, r.notBackedUp.count > 0 {
                    RowButton(title: "Back up", symbol: "icloud.and.arrow.up.fill", color: Tab.backup.color) { model.backUp(r.recipe) }
                        .help("Copy \(Fmt.bytes(r.notBackedUp.bytes)) to Google Drive. Nothing leaves the phone.")
                }
                if let r, r.selected.count > 0 {
                    RowButton(title: "Back up & remove…", symbol: "iphone.and.arrow.forward", style: .fill) { model.removing = r.recipe }
                        .help("Backs up what Google Drive doesn't have yet, then removes from the iPhone only what Google Drive has checked. You see the plan first.")
                }
            }
            .fixedSize().frame(minWidth: 250, alignment: .trailing)
        }
    }
}
