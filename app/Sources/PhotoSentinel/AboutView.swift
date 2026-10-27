import AppKit
import PhotoCore
import SwiftUI

/// Who made it, where it comes from and which version this is. Laid out like System Sentinel's About.
struct AboutView: View {
    @ObservedObject var model: Model
    @State private var copied = false

    static let repo = "andrewbakercloudscale/photo-sentinel"
    static let linkedIn = URL(string: "https://www.linkedin.com/in/andrew-baker-ninja/")!
    static let blog = URL(string: "https://andrewbaker.ninja/")!
    static let cloudScale = URL(string: "https://cloudscale.consulting/")!
    static let gitHubProfile = URL(string: "https://github.com/andrewbakercloudscale")!
    static let repoPage = URL(string: "https://github.com/\(repo)")!
    static let issues = URL(string: "https://github.com/\(repo)/issues/new")!
    static let safety = URL(string: "https://github.com/\(repo)/blob/main/docs/SAFETY.md")!
    static let licence = URL(string: "https://github.com/\(repo)/blob/main/LICENSE")!

    static var version: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev" }
    static var build: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?" }

    /// What leaves the Mac, said plainly.
    static let privacy = "Your photos and videos go to one place: your own Google Drive (through rclone), and your own S3 bucket if you add one. Nothing about you or your library is sent anywhere else, and there are no analytics. The app is not notarised by Apple yet; the source is public so anyone can check what it does."

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                hero
                author
                project
                details
                Text("Copyright © 2026 Andrew Baker. Free and open source under the MIT licence: use it, change it and share it.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            .padding(20)
        }
        .frame(width: 560)
        .frame(minHeight: 520, idealHeight: 760)
        .background(Color(light: "#f5f5f7", dark: "#1c1c1f"))
    }

    private var hero: some View {
        HStack(spacing: 16) {
            Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 84, height: 84)
            VStack(alignment: .leading, spacing: 4) {
                Text("Photo Sentinel").font(.largeTitle.weight(.bold))
                Text("Version \(Self.version) (build \(Self.build))").font(.title3.weight(.medium)).monospacedDigit()
                UpdatesRow(updates: model.updates, busy: model.backupBusy).padding(.vertical, 4)
                Text("The backup manager for your iPhone: everything copied to your own Google Drive and checked, then cleared off the phone with proof for every item.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Button { ShareApp.show() } label: {
                    Label("Share with a friend", systemImage: "square.and.arrow.up")
                }
                .buttonStyle(.borderedProminent).tint(Tab.checkup.color).padding(.top, 4)
                .help("Send a link by Messages, Mail, WhatsApp, AirDrop and more")
            }
        }
    }

    private var author: some View {
        Card(title: "Made by", symbol: "person.crop.circle", tint: Tab.library.color) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Andrew Baker").font(.title3.weight(.semibold))
                Text("Group Chief Information Officer at Capitec Bank, and the person behind CloudScale Consulting. He writes about technology, leadership and the tools he builds at andrewbaker.ninja.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Text("Photo Sentinel started with an iPhone too full to use and a rule: nothing comes off the phone until Google Drive has a copy whose fingerprint matches.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            VStack(spacing: 6) {
                linkRow("LinkedIn", "linkedin.com/in/andrew-baker-ninja", "person.2.fill", Color(hex: "#0a66c2"), Self.linkedIn)
                linkRow("Blog", "andrewbaker.ninja", "text.book.closed.fill", Theme.good, Self.blog)
                linkRow("CloudScale Consulting", "cloudscale.consulting", "cloud.fill", Tab.backup.color, Self.cloudScale)
                linkRow("GitHub", "github.com/andrewbakercloudscale", "chevron.left.forwardslash.chevron.right", Tab.library.color, Self.gitHubProfile)
            }
            .padding(.top, 8)
        }
    }

    private var project: some View {
        Card(title: "The project", symbol: "shippingbox", tint: Tab.cleanUp.color) {
            VStack(spacing: 6) {
                linkRow("Source code", "github.com/\(Self.repo)", "curlybraces", Tab.library.color, Self.repoPage)
                linkRow("How it keeps your photos safe", "The rules every removal follows", "checkmark.shield.fill", Theme.good, Self.safety)
                Button { model.onboarded = false } label: {
                    linkLabel("Welcome tour", "The four-step introduction from the first launch", "hand.wave.fill", Tab.checkup.color)
                }
                .buttonStyle(.plain)
                Button { reportProblem() } label: {
                    linkLabel("Report a problem", "Copies the details below and opens a GitHub issue", "exclamationmark.bubble.fill", Theme.warn)
                }
                .buttonStyle(.plain)
                .help("Paste the details into the issue. To ask for a feature, open an issue the same way.")
                linkRow("Licence", "MIT", "checkmark.seal.fill", Tab.settings.color, Self.licence)
            }
            Text(Self.privacy)
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true).padding(.top, 8)
        }
    }

    private var details: some View {
        Card(title: "Details", symbol: "info.circle", tint: Tab.settings.color,
             trailing: AnyView(Button {
                 copyDetails()
                 copied = true
                 DispatchQueue.main.asyncAfter(deadline: .now() + 2) { copied = false }
             } label: {
                 Label(copied ? "Copied" : "Copy details", systemImage: copied ? "checkmark" : "doc.on.doc")
             }
             .buttonStyle(.bordered))) {
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
                ForEach(rows, id: \.0) { row in
                    GridRow {
                        Text(row.0).font(.callout).foregroundStyle(.secondary)
                        Text(row.1).font(.callout).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            HStack(spacing: 8) {
                Button { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: Bundle.main.bundlePath)]) } label: {
                    Label("Show the app in Finder", systemImage: "folder")
                }
                Button { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: model.ledgerPath)]) } label: {
                    Label("Show the ledger", systemImage: "externaldrive")
                }
                .help("Every item, backup and removal is recorded here: " + model.ledgerPath)
            }
            .buttonStyle(.bordered).padding(.top, 8)
        }
    }

    // MARK: Pieces

    private var rows: [(String, String)] {
        let v = ProcessInfo.processInfo.operatingSystemVersion
        return [
            ("Version", "\(Self.version) (build \(Self.build))"),
            ("Installed at", Bundle.main.bundlePath),
            ("App ID", Bundle.main.bundleIdentifier ?? "?"),
            ("Engine", model.repoPath),
            ("Ledger", model.ledgerPath),
            ("macOS", "\(v.majorVersion).\(v.minorVersion).\(v.patchVersion)"),
        ]
    }

    private func copyDetails() {
        let text = (["Photo Sentinel"] + rows.map { "\($0.0): \($0.1)" } + ["Source: \(Self.repoPage.absoluteString)"]).joined(separator: "\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    private func reportProblem() {
        copyDetails()
        NSWorkspace.shared.open(Self.issues)
    }

    private func linkRow(_ title: String, _ detail: String, _ symbol: String, _ color: Color, _ url: URL) -> some View {
        Button { NSWorkspace.shared.open(url) } label: { linkLabel(title, detail, symbol, color) }
            .buttonStyle(.plain)
            .help(url.absoluteString)
    }

    private func linkLabel(_ title: String, _ detail: String, _ symbol: String, _ color: Color) -> some View {
        HStack(spacing: 10) {
            Image(systemName: symbol).font(.body.weight(.semibold)).foregroundStyle(color).frame(width: 22)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.callout.weight(.semibold)).foregroundStyle(.primary)
                Text(detail).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            Image(systemName: "arrow.up.right").font(.callout.weight(.semibold)).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(RoundedRectangle(cornerRadius: 8).fill(color.opacity(0.08)))
        .contentShape(Rectangle())
    }
}

/// About in its own window, like a normal Mac app's About box.
@MainActor
enum AboutWindow {
    private static var window: NSWindow?

    static func show(_ model: Model) {
        if window == nil {
            let w = NSWindow(contentViewController: NSHostingController(rootView: AboutView(model: model)))
            w.title = "About Photo Sentinel"
            w.styleMask = [.titled, .closable, .miniaturizable, .resizable]
            w.isReleasedWhenClosed = false
            w.setContentSize(NSSize(width: 560, height: 760))
            w.center()
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }
}

/// "Share with a friend": a message through the Mac's own share menu (Messages, Mail, WhatsApp,
/// AirDrop, Notes and anything else that takes text).
@MainActor
enum ShareApp {
    nonisolated static let subject = "Photo Sentinel: free iPhone photo backup for your Mac"

    static var message: String {
        """
        I've been using Photo Sentinel, a free, open-source Mac app that backs up your iPhone's photos and videos to your own Google Drive, checks every copy, and then clears space on the phone, with proof for every item it removes.

        It's free and the source is public: https://github.com/\(AboutView.repo)
        """
    }

    private static let delegate = PickerDelegate()

    /// Opens the share menu under the mouse, in the window that was clicked.
    static func show() {
        guard let window = NSApp.keyWindow ?? NSApp.windows.first(where: \.isVisible), let view = window.contentView else { return }
        let point = view.convert(window.mouseLocationOutsideOfEventStream, from: nil)
        let picker = NSSharingServicePicker(items: [message])
        picker.delegate = delegate
        picker.show(relativeTo: NSRect(x: point.x, y: point.y, width: 1, height: 1), of: view, preferredEdge: .minY)
    }

    private final class PickerDelegate: NSObject, NSSharingServicePickerDelegate {
        // Mail and others take a subject line.
        func sharingServicePicker(_ picker: NSSharingServicePicker, didChoose service: NSSharingService?) {
            service?.subject = ShareApp.subject
        }
    }
}
