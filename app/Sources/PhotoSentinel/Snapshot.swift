import AppKit
import PhotoCore
import SwiftUI

/// `PhotoSentinel --snapshot <dir>` renders every tab against the real ledger to PNGs and
/// quits, so a change can be looked at without screenshotting the desktop.
@MainActor
enum Snapshot {
    static func runIfAsked() {
        let args = CommandLine.arguments
        guard let i = args.firstIndex(of: "--snapshot"), args.count > i + 1 else { return }
        let dir = URL(fileURLWithPath: args[i + 1])
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let model = Model()
        model.library = try? model.engine.library()
        model.jobs = model.engine.jobs()
        model.doctor = try? model.engine.doctor()

        for tab in Tab.allCases {
            model.tab = tab
            let view = VStack(alignment: .leading, spacing: 12) {
                Header(model: model)
                TabBar(selection: .constant(tab), badges: [.checkup: model.findings.count])
                page(tab, model)
            }
            .padding(16)
            .frame(width: 1040)
            .background(Color(light: "#f5f5f7", dark: "#1c1c1f"))
            let r = ImageRenderer(content: view)
            r.scale = 1
            if let img = r.nsImage, let tiff = img.tiffRepresentation,
               let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) {
                try? png.write(to: dir.appendingPathComponent("\(tab.rawValue)-\(tab.title).png"))
            }
        }
        exit(0)
    }

    @ViewBuilder static func page(_ tab: Tab, _ model: Model) -> some View {
        switch tab {
        case .checkup: CheckupView(model: model)
        case .library: LibraryView(model: model)
        case .cleanUp: CleanUpView(model: model)
        case .backup: BackupView(model: model)
        case .activity: ActivityView(model: model)
        case .bin: BinView(model: model)
        case .settings: SettingsView(model: model)
        }
    }
}
