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
        model.everything = try? Ledger.breakdown(SQLiteReader(path: model.ledgerPath))
        model.jobs = model.engine.jobs()
        model.storage = try? model.engine.storage()
        model.doctor = try? model.engine.doctor()
        model.tasks = TaskStore.all()
        if let s = model.library { model.suggestions = (try? Ledger.suggestions(SQLiteReader(path: model.ledgerPath), apps: s.apps)) ?? [] }
        model.draft = Filter(source: "whatsapp", type: "video", olderThan: "6m", minMB: 20)
        model.selection = try? Ledger.breakdown(SQLiteReader(path: model.ledgerPath), filter: model.draft)
        model.allProof = try? Ledger.proof(SQLiteReader(path: model.ledgerPath), since: "", limit: 0)

        for tab in Tab.shown {
            model.tab = tab
            let view = VStack(alignment: .leading, spacing: 12) {
                Header(model: model)
                TabBar(selection: .constant(tab), badges: [.checkup: model.findings.count])
                page(tab, model)
            }
            .padding(16)
            .disclosureGroupStyle(.expander)
            .frame(width: 1040)
            .background(Color(light: "#f5f5f7", dark: "#1c1c1f"))
            let r = ImageRenderer(content: view)
            r.scale = 1
            if let img = r.nsImage, let tiff = img.tiffRepresentation,
               let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) {
                try? png.write(to: dir.appendingPathComponent("\(tab.rawValue)-\(tab.title).png"))
            }
        }
        for recipe in model.recipes {
            let r = ImageRenderer(content: RemovalSheet(model: model, recipe: recipe, scrolls: false).frame(height: 1100))
            r.scale = 1
            if let img = r.nsImage, let tiff = img.tiffRepresentation,
               let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) {
                try? png.write(to: dir.appendingPathComponent("remove-\(recipe.id).png"))
            }
        }
        for stat in [StatDetail.Stat.verified, .freed, .recentlyDeleted] {
            let d = try? Ledger.statDetail(SQLiteReader(path: model.ledgerPath), stat)
            let r = ImageRenderer(content: StatDetailSheet(model: model, stat: stat, preset: d, scrolls: false).frame(width: 820))
            r.scale = 1
            if let img = r.nsImage, let tiff = img.tiffRepresentation,
               let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) {
                try? png.write(to: dir.appendingPathComponent("stat-\(stat.rawValue).png"))
            }
        }
        exit(0)
    }

    @ViewBuilder static func page(_ tab: Tab, _ model: Model) -> some View {
        switch tab {
        case .checkup: CheckupView(model: model)
        case .library: LibraryView(model: model)
        case .cleanUp: LibraryView(model: model)
        case .backup: BackupView(model: model)
        case .activity: ActivityView(model: model)
        case .bin: BinView(model: model)
        case .settings: SettingsView(model: model)
        }
    }
}
