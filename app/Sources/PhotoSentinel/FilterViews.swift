import PhotoCore
import SwiftUI

/// Build any selection the CLI can express, see what it would free, then back it up,
/// remove it, or keep it as a clean-up of its own.
struct FilterBuilder: View {
    @ObservedObject var model: Model
    @State private var stats: RecipeStats?
    @State private var counting = false
    @State private var saveName = ""

    private var f: Binding<Filter> { $model.draft }

    var body: some View {
        Card(title: "Build your own rule", symbol: "line.3.horizontal.decrease.circle.fill", tint: Tab.cleanUp.color,
             trailing: AnyView(Button("Reset") { model.draft = Filter() }.buttonStyle(.link))) {
            VStack(alignment: .leading, spacing: 12) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 8) {
                    GridRow {
                        picker("From", f.source, sources)
                        picker("Kind", f.type, Self.types)
                        picker("Year taken", f.year, years)
                    }
                    GridRow {
                        picker("Older than", f.olderThan, Self.olderAges)
                        picker("Newer than", f.newerThan, Self.newerAges)
                        lowRes
                    }
                    GridRow {
                        picker("Bigger than", f.minMB, Self.sizes)
                        picker("Smaller than", f.maxMB, Self.sizes)
                        picker("Country", f.country, countries)
                    }
                    GridRow {
                        picker("City", f.city, cities)
                        Color.clear.frame(height: 1)
                        Color.clear.frame(height: 1)
                    }
                }
                Divider()
                preview
                HStack(spacing: 8) {
                    CapsuleButton(title: "Back up & remove from iPhone…", symbol: "iphone.and.arrow.forward", color: Tab.cleanUp.color) {
                        model.removing = Recipe(custom: model.draft)
                    }
                    if !model.draft.backups.isEmpty && (stats?.notBackedUp.count ?? 0) > 0 {
                        CapsuleButton(title: "Back up", symbol: "icloud.and.arrow.up.fill", color: Tab.backup.color, style: .soft) {
                            model.backUp(Recipe(custom: model.draft))
                        }
                    }
                    Spacer()
                    TextField("Name it to keep it", text: $saveName).textFieldStyle(.roundedBorder).frame(width: 190)
                    CapsuleButton(title: "Save", symbol: "bookmark.fill", color: Tab.cleanUp.color, style: .soft) {
                        var d = model.draft
                        d.name = saveName.trimmingCharacters(in: .whitespaces)
                        model.save(d)
                        saveName = ""
                    }
                }
                .disabled(model.draft.isEmpty)
                .help(model.draft.isEmpty ? "Pick at least one filter first: with every filter on Any this would take your whole phone" : "")
            }
        }
        .task(id: model.draft) {
            counting = true
            // Typing through pickers fires several changes; count the last one.
            try? await Task.sleep(for: .milliseconds(150))
            guard !Task.isCancelled else { return }
            stats = await model.preview(model.draft, ledger: model.ledgerPath)
            counting = false
        }
    }

    @ViewBuilder private var preview: some View {
        if model.draft.isEmpty {
            Label("Pick at least one filter to turn the buttons on. With every filter on Any the rule would take your whole phone, so it waits. For example: From WhatsApp, Older than 6 months.",
                  systemImage: "hand.point.up.left.fill")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        } else if let s = stats {
            VStack(alignment: .leading, spacing: 4) {
                Text(model.draft.summary).font(.body.weight(.semibold))
                HStack(spacing: 18) {
                    figure("On the phone", s.selected, .primary)
                    figure("In Drive, can go (est.)", s.removableNow, Theme.good)
                    figure("Not in Drive yet", s.notBackedUp, Theme.warn)
                    figure("Kept for safety", s.kept, .secondary)
                    if counting { ProgressView().controlSize(.small) }
                }
            }
        }
    }

    private func figure(_ label: String, _ t: Tally, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(Fmt.bytes(t.bytes)).font(.title3.weight(.bold)).foregroundStyle(color).monospacedDigit()
            Text("\(Fmt.count(t.count)) items").font(.caption).foregroundStyle(.secondary).monospacedDigit()
        }
    }

    /// Only photos can be suspected proxies; videos always score zero.
    private var lowRes: some View {
        HStack(spacing: 6) {
            Text("Low-res check").frame(width: 100, alignment: .leading)
            Picker("", selection: f.proxyTolerance) {
                Text("Strict").tag(0.0)
                Text("Relaxed").tag(0.5)
                Text("Off").tag(1.0)
            }
            .labelsHidden().fixedSize().frame(width: 170, alignment: .leading)
            .help("Photos that are small for their size might be low-res stand-ins whose original is only in iCloud. Strict keeps every one on the phone; Relaxed keeps the clearest cases; Off keeps none. Screenshots are often flagged without being stand-ins.")
        }
        .font(.callout)
    }

    private func picker<T: Hashable>(_ label: String, _ value: Binding<T?>, _ options: [(T, String)]) -> some View {
        HStack(spacing: 6) {
            Text(label).frame(width: 100, alignment: .leading)
            Picker("", selection: value) {
                Text("Any").tag(T?.none)
                ForEach(options, id: \.0) { Text($0.1).tag(T?.some($0.0)) }
            }
            // A menu picker keeps its own width and centres in a wider frame: pin it left so the column lines up.
            .labelsHidden().fixedSize().frame(width: 170, alignment: .leading)
        }
        .font(.callout)
    }

    private var sources: [(String, String)] {
        let channels = Channel.allCases.compactMap { c in c.cliSource.map { ($0, c.title) } }
        let known: Set<String> = ["com.apple.camera", "net.whatsapp.WhatsApp", "com.apple.springboard", "com.apple.replayd"]
        let apps = (model.library?.apps ?? []).filter { !known.contains($0.bundle) }.map { ($0.bundle, Fmt.app($0.bundle)) }
        return channels + apps
    }

    private var countries: [(String, String)] {
        (model.library?.places ?? []).map { ($0.country, $0.country) }
    }

    /// The chosen country's cities, or every city when no country is chosen.
    private var cities: [(String, String)] {
        let places = (model.library?.places ?? []).filter { model.draft.country == nil || $0.country == model.draft.country }
        var seen = Set<String>()
        return places.flatMap(\.cities).filter { seen.insert($0.city).inserted }.prefix(60).map { ($0.city, $0.city) }
    }

    private var years: [(Int, String)] {
        (model.library?.years ?? []).compactMap { Int($0.year) }.reversed().map { ($0, String($0)) }
    }

    static let types: [(String, String)] = [
        ("photo", "Photos"), ("video", "Videos"), ("live", "Live Photos"),
        ("screenshot", "Screenshots"), ("burst", "Bursts"), ("raw", "RAW"),
    ]
    static let olderAges: [(String, String)] = ["30d", "90d", "6m", "1y", "18m", "2y", "3y", "5y"].map { ($0, Age($0)!.words) }
    static let newerAges: [(String, String)] = ["7d", "30d", "90d", "6m", "1y"].map { ($0, Age($0)!.words) }
    static let sizes: [(Int, String)] = [1, 5, 10, 20, 50, 100, 250, 500, 1024, 2048].map { ($0, Filter.size($0)) }
}

/// Clean-ups the library suggests, measured against it, biggest win first.
struct SuggestionGrid: View {
    @ObservedObject var model: Model

    var body: some View {
        if !model.suggestions.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("SUGGESTED FOR YOUR LIBRARY").sectionLabel()
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 300), spacing: 12)], spacing: 12) {
                    ForEach(model.suggestions.prefix(6)) { s in card(s) }
                }
            }
        }
    }

    private func card(_ s: Suggestion) -> some View {
        let st = s.stats
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                SymbolTile(symbol: "sparkles", color: Tab.cleanUp.color, size: 26)
                Text(st.recipe.title).font(.headline)
            }
            Text(s.why).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text(st.removableNow.count > 0
                 ? "About \(Fmt.bytes(st.removableNow.bytes)) can go now" + (st.notBackedUp.bytes > 0 ? ", \(Fmt.bytes(st.notBackedUp.bytes)) more after backup" : "")
                 : "\(Fmt.bytes(st.selected.bytes)) once it is backed up")
                .font(.callout.weight(.semibold)).foregroundStyle(st.removableNow.count > 0 ? Theme.good : Theme.warn)
            HStack {
                CapsuleButton(title: "Review…", symbol: "trash.fill", color: Tab.cleanUp.color) { model.removing = st.recipe }
                CapsuleButton(title: "Adjust", symbol: "slider.horizontal.3", color: Tab.cleanUp.color, style: .soft) { model.draft = st.recipe.filter }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.cardFill))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.cardStroke, lineWidth: 1.5))
    }
}
