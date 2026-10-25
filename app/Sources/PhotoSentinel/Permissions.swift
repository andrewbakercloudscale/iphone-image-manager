import AppKit
import Photos
import SwiftUI
import UserNotifications

/// The permissions Photo Sentinel needs, asked for together so macOS does not stop a backup
/// or a removal half way to ask. The engine and its PhotoKit helper run as this app's
/// children, so what the app is granted covers them too.
@MainActor
final class Permissions: ObservableObject {
    enum State: Equatable { case granted, denied, notAsked }

    struct Item: Identifiable, Equatable {
        let id: String
        let title: String
        let why: String
        let symbol: String
        var state: State
    }

    @Published var items: [Item] = []
    /// "Not now" hides the banner for a week; then it asks again if anything is still missing.
    @AppStorage("permissionsDismissedAt") var dismissedAt = 0.0
    var dismissed: Bool { Date().timeIntervalSince1970 - dismissedAt < 7 * 86_400 }
    private var timer: Timer?

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    var missing: [Item] { items.filter { $0.state != .granted } }

    func refresh() {
        let photos: State = switch PHPhotoLibrary.authorizationStatus(for: .readWrite) {
        case .authorized: .granted
        case .notDetermined: .notAsked
        default: .denied // denied, restricted, or limited to a few photos
        }
        let list = [
            Item(id: "photos", title: "Photos",
                 why: "Read your library to back it up, and remove what Google Drive has checked. Needs full access: with only some photos allowed, the rest are never seen.",
                 symbol: "photo.on.rectangle.angled", state: photos),
            Item(id: "fda", title: "Full Disk Access",
                 why: "Read the Photos library's places and the ledger and archive on your Desktop, without macOS asking folder by folder.",
                 symbol: "lock.open.fill", state: Self.hasFullDiskAccess() ? .granted : .notAsked),
        ]
        // Notifications answer asynchronously: keep the last known state until they do.
        let old = items.first { $0.id == "notifications" }?.state ?? .notAsked
        let full = list + [Item(id: "notifications", title: "Notifications",
                                why: "Tell you when a backup finishes or stops, and when a check or a removal finishes.",
                                symbol: "bell.badge.fill", state: old)]
        if full != items { items = full }
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            let s: State = switch settings.authorizationStatus {
            case .authorized, .provisional: .granted
            case .denied: .denied
            default: .notAsked
            }
            Task { @MainActor in
                if let i = self.items.firstIndex(where: { $0.id == "notifications" }), self.items[i].state != s { self.items[i].state = s }
            }
        }
    }

    /// Asks for notifications and Photos in place, then opens Full Disk Access.
    func requestAll() {
        if let n = items.first(where: { $0.id == "notifications" }), n.state == .notAsked { request(n) }
        if let photos = items.first(where: { $0.id == "photos" }), photos.state != .granted {
            request(photos)
        } else if !Self.hasFullDiskAccess() {
            Self.showSettings("Privacy_AllFiles")
        }
    }

    func request(_ item: Item) {
        switch item.id {
        case "photos" where item.state == .notAsked:
            PHPhotoLibrary.requestAuthorization(for: .readWrite) { _ in
                Task { @MainActor in
                    self.refresh()
                    if !Self.hasFullDiskAccess() { Self.showSettings("Privacy_AllFiles") }
                }
            }
        // Once turned down, macOS never asks again: only System Settings can turn it back on.
        case "photos": Self.showSettings("Privacy_Photos")
        case "notifications" where item.state == .notAsked:
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in
                Task { @MainActor in self.refresh() }
            }
        case "notifications": Self.showURL("x-apple.systempreferences:com.apple.Notifications-Settings.extension?id=\(Bundle.main.bundleIdentifier ?? "consulting.cloudscale.photosentinel")")
        default: Self.showSettings("Privacy_AllFiles")
        }
    }

    static func showSettings(_ pane: String) {
        showURL("x-apple.systempreferences:com.apple.preference.security?\(pane)")
    }

    static func showURL(_ url: String) {
        NSWorkspace.shared.open(URL(string: url)!)
        // Opening a URL leaves System Settings behind this app's window; bring it forward.
        for delay in [0.5, 1.5] {
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
                NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.systempreferences").first?.activate()
            }
        }
    }

    /// Reading a folder only Full Disk Access opens, without triggering a prompt.
    nonisolated static func hasFullDiskAccess() -> Bool {
        let home = FileManager.default.homeDirectoryForCurrentUser
        for p in ["Library/Safari", ".Trash", "Library/Mail"] {
            let url = home.appendingPathComponent(p)
            if FileManager.default.fileExists(atPath: url.path) {
                return (try? FileManager.default.contentsOfDirectory(atPath: url.path)) != nil
            }
        }
        return false
    }
}

/// Under the tabs until everything is granted or put off. Copied from System Sentinel's.
struct PermissionsBanner: View {
    @ObservedObject var permissions: Permissions
    private let tint = Color(hex: "#7c3aed")

    var body: some View {
        let missing = permissions.missing
        if !permissions.dismissed, !missing.isEmpty {
            HStack(spacing: 12) {
                Image(systemName: "checkmark.shield.fill").font(.system(size: 24, weight: .semibold)).foregroundStyle(.white)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Finish setting up: \(missing.count) permission\(missing.count == 1 ? "" : "s") to go").font(.headline).foregroundStyle(.white)
                    Text("Grant them once now so macOS does not stop a backup to ask: \(missing.map(\.title).joined(separator: ", ")).")
                        .font(.caption).foregroundStyle(.white.opacity(0.92)).lineLimit(2)
                }
                Spacer(minLength: 6)
                Button { permissions.dismissedAt = Date().timeIntervalSince1970 } label: {
                    Text("Not now").font(.callout.weight(.medium)).foregroundStyle(.white)
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(Capsule().strokeBorder(.white.opacity(0.8), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .help("Hide this banner for a week. Settings, Permissions has the same buttons")
                Button { permissions.requestAll() } label: {
                    Label("Grant all", systemImage: "hand.raised.fill")
                        .font(.callout.weight(.semibold)).foregroundStyle(tint)
                        .padding(.horizontal, 12).padding(.vertical, 6)
                        .background(Capsule().fill(.white))
                }
                .buttonStyle(.plain)
                .help("Ask macOS for notifications and Photos access, then open Full Disk Access in System Settings")
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 10).fill(tint))
        }
    }
}

/// Settings: each permission, whether it is granted, and a button to fix it.
struct PermissionsCard: View {
    @ObservedObject var permissions: Permissions

    var body: some View {
        Card(title: "Permissions", symbol: "checkmark.shield.fill", tint: Color(hex: "#7c3aed"),
             trailing: AnyView(Button("Check again") { permissions.refresh() }.buttonStyle(.link).font(.caption))) {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(permissions.items) { p in
                    HStack(spacing: 10) {
                        Image(systemName: p.state == .granted ? "checkmark.circle.fill" : (p.state == .denied ? "xmark.circle.fill" : "circle.dashed"))
                            .font(.title3).foregroundStyle(p.state == .granted ? Theme.good : (p.state == .denied ? Theme.bad : .secondary))
                        VStack(alignment: .leading, spacing: 1) {
                            Text(p.title).font(.callout.weight(.semibold))
                            Text(p.why).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 8)
                        if p.state != .granted {
                            Button(p.id != "fda" && p.state == .notAsked ? "Allow" : "Open settings") { permissions.request(p) }
                        }
                    }
                }
                Text("After turning on Full Disk Access, quit and reopen Photo Sentinel. Permissions stay granted across rebuilds because the app keeps the same signature.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// Banners from macOS when work the user is waiting on moves on. Silent when not allowed.
enum Notify {
    static func send(_ title: String, _ body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }
}
