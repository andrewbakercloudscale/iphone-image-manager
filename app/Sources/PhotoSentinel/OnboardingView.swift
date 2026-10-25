import PhotoCore
import SwiftUI

/// First run, in the order the owner asked for: the promise, whether this Mac is ready,
/// what is going on with the library, then the options and what each would do.
struct OnboardingView: View {
    @ObservedObject var model: Model
    @State private var step = 0
    static let steps = 4

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                SymbolTile(symbol: "photo.on.rectangle.angled", color: Tab.checkup.color, size: 48)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.title2.weight(.bold))
                    Text("Step \(step + 1) of \(Self.steps)").font(.callout).foregroundStyle(.secondary)
                }
            }
            Group {
                switch step {
                case 0: promise
                case 1: ScrollView { DoctorPanel(model: model) }
                case 2: ScrollView { account }
                default: ScrollView { options }
                }
            }
            .frame(maxWidth: .infinity, minHeight: 300, alignment: .topLeading)
            HStack {
                HStack(spacing: 6) {
                    ForEach(0..<Self.steps, id: \.self) { i in Circle().fill(i == step ? Tab.cleanUp.color : Color.secondary.opacity(0.3)).frame(width: 8, height: 8) }
                }
                Spacer()
                if step > 0 { CapsuleButton(title: "Back", style: .ghost) { step -= 1 } }
                CapsuleButton(title: step == Self.steps - 1 ? "Start" : "Continue", color: Tab.cleanUp.color) {
                    if step == Self.steps - 1 { model.onboarded = true } else { step += 1 }
                }
            }
        }
        .padding(24)
        .frame(width: 720, height: 720)
    }

    private var title: String {
        switch step {
        case 0: "Free up your iPhone without losing a photo"
        case 1: "Checking this Mac"
        case 2: "What's on your iPhone"
        default: "Your options, and what each would do"
        }
    }

    private var promise: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Nothing leaves your iPhone until a copy is safely in your Google Drive, checked, and you say so.")
                .font(.title3.weight(.semibold)).foregroundStyle(.white)
                .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 12).fill(Theme.heroGradient))
            point("icloud.and.arrow.up.fill", Tab.backup.color, "Back up", "to your own Google Drive, and check every copy.")
            point("sparkles", Tab.cleanUp.color, "Clear out", "old videos, screenshots and WhatsApp clutter.")
            point("arrow.uturn.backward", Tab.bin.color, "Change your mind", "for 30 days: everything removed can come back.")
        }
    }

    private func point(_ symbol: String, _ color: Color, _ bold: String, _ rest: String) -> some View {
        HStack(spacing: 12) {
            SymbolTile(symbol: symbol, color: color, size: 34)
            (Text(bold).bold() + Text(" " + rest)).font(.body)
        }
    }

    /// Every source, WhatsApp included, with how much of it is already safe in Drive.
    @ViewBuilder private var account: some View {
        if let s = model.library {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    Kpi(label: "ON YOUR IPHONE", value: Fmt.bytes(s.onPhone.bytes), note: "\(Fmt.count(s.onPhone.count)) items")
                    Kpi(label: "SAFE IN GOOGLE DRIVE", value: Fmt.pct(s.coverage), note: Fmt.bytes(s.verified.bytes), color: Theme.good)
                    Kpi(label: "ONLY ON THE PHONE", value: Fmt.bytes(s.onPhone.bytes - s.verified.bytes), note: "not backed up yet", color: Theme.warn)
                }
                StackBar(parts: s.channels.map { (Theme.color($0.channel), Double($0.total.bytes)) })
                VStack(spacing: 8) {
                    ForEach(s.channels) { c in
                        HStack(spacing: 10) {
                            RoundedRectangle(cornerRadius: 3).fill(Theme.color(c.channel)).frame(width: 12, height: 12)
                            Text(c.channel.title).fontWeight(.semibold).frame(width: 140, alignment: .leading)
                            Text("\(Fmt.count(c.photos.count)) photos · \(Fmt.count(c.videos.count)) videos")
                                .foregroundStyle(.secondary).frame(width: 190, alignment: .leading)
                            Text(Fmt.bytes(c.total.bytes)).fontWeight(.semibold).monospacedDigit().frame(width: 70, alignment: .trailing)
                            Meter(value: c.coverage, color: c.coverage >= 0.999 ? Theme.good : Tab.backup.color, height: 6)
                            Text("\(Fmt.pct(c.coverage)) in Drive").foregroundStyle(.secondary).monospacedDigit().frame(width: 90, alignment: .trailing)
                        }
                        .font(.callout)
                    }
                }
                if let j = model.activeJob {
                    Label("Backing up \(j.title) now. It carries on in the background.", systemImage: "icloud.and.arrow.up.fill")
                        .font(.callout).foregroundStyle(Tab.backup.color)
                }
            }
        } else {
            ProgressView("Reading your library…")
        }
    }

    /// The choices, each with its effect beside it, then what the library itself suggests.
    private var options: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Pick how old things must be before they leave the phone. Every option backs up to Google Drive first, and nothing is removed until you confirm it.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            AgesCard(model: model)
            if !model.suggestions.isEmpty {
                Card(title: "Also worth a look", symbol: "sparkles", tint: Tab.cleanUp.color) {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(model.suggestions.prefix(4)) { sg in
                            HStack {
                                Text(sg.stats.recipe.title)
                                Spacer()
                                Text("frees \(Fmt.bytes(sg.stats.selected.bytes))").fontWeight(.semibold).monospacedDigit()
                                Text("· \(Fmt.bytes(sg.stats.removableNow.bytes)) today").foregroundStyle(.secondary).monospacedDigit()
                            }
                            .font(.callout)
                        }
                        Text("All of these, and your own filters, are on the Clean up tab.").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }
}
