import PhotoCore
import SwiftUI

/// First run. Phase A sits on the owner's existing setup, so the steps check rather than
/// configure: the promise, doctor's checks, and what the ledger already knows. Phase C
/// replaces step 2 with PhotoKit access and Sign in with Google.
struct OnboardingView: View {
    @ObservedObject var model: Model
    @State private var step = 0
    static let steps = 3

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
                case 1: ScrollView { DoctorList(report: model.doctor, running: model.doctorRunning) }
                default: firstLook
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
        .frame(width: 580)
    }

    private var title: String {
        switch step {
        case 0: "Free up your iPhone without losing a photo"
        case 1: "Checking this Mac"
        default: "What it found"
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

    private var firstLook: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let s = model.library {
                Text("\(Fmt.count(s.onPhone.count)) items on your iPhone, \(Fmt.bytes(s.onPhone.bytes)). \(Fmt.pct(s.coverage)) is already safe in Google Drive.")
                StackBar(parts: s.channels.map { (Theme.color($0.channel), Double($0.total.bytes)) })
                ForEach(s.recipes.filter { $0.selected.count > 0 && $0.recipe.kind != .largeVideos }) { r in
                    HStack {
                        Image(systemName: r.recipe.symbol).foregroundStyle(Tab.cleanUp.color).frame(width: 20)
                        Text(r.recipe.title)
                        Spacer()
                        Text("frees \(Fmt.bytes(r.selected.bytes))").fontWeight(.semibold).monospacedDigit()
                    }
                }
                Text("Change the ages any time in Settings.").font(.callout).foregroundStyle(.secondary)
            } else {
                ProgressView("Reading your library…")
            }
        }
    }
}
