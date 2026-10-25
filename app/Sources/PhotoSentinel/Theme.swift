import AppKit
import PhotoCore
import SwiftUI

// Copied from System Sentinel's Theme.swift (../system-sentinel/Sources/SystemSentinel), so the
// two apps look like one family. Change both together. The tabs are this app's own.

enum Theme {
    /// Sentinel's categorical series, in its fixed colour-blind-checked order.
    static let series: [Color] = zip(
        ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"],
        ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"]
    ).map { Color(light: $0, dark: $1) }

    static let other = Color(light: "#a3a29d", dark: "#6b6a66")
    static let accent = Color(light: "#5b4fd6", dark: "#8f86f0")
    static let good = Color(light: "#15803d", dark: "#4ade80")
    static let warn = Color(light: "#b45309", dark: "#fbbf24")
    static let bad = Color(light: "#b91c1c", dark: "#f87171")

    /// The Checkup hero: Sentinel's burnt orange.
    static let heroGradient = LinearGradient(
        colors: [Color(light: "#9a3412", dark: "#7c2d12"), Color(light: "#c2410c", dark: "#9a3412")],
        startPoint: .topLeading, endPoint: .bottomTrailing)
    static let calmGradient = LinearGradient(
        colors: [Color(light: "#166534", dark: "#14532d"), Color(light: "#15803d", dark: "#166534")],
        startPoint: .topLeading, endPoint: .bottomTrailing)

    static let cardFill = Color(light: "#ffffff", dark: "#25252a").opacity(0.75)
    static let cardStroke = Color(light: "#8f99aa", dark: "#5f6575")

    /// A channel keeps one colour on every chart.
    static func color(_ c: Channel) -> Color {
        switch c {
        case .camera: series[0]
        case .screenshot: series[3]
        case .whatsapp: series[2]
        case .screenRecording: series[4]
        case .otherApps: series[6]
        case .unattributed: other
        }
    }
}

extension Color {
    init(hex: String) {
        let v = UInt64(hex.dropFirst(), radix: 16) ?? 0
        self.init(.sRGB, red: Double((v >> 16) & 0xff) / 255, green: Double((v >> 8) & 0xff) / 255,
                  blue: Double(v & 0xff) / 255, opacity: 1)
    }

    /// A colour with its own light and dark step, not an automatic flip.
    init(light: String, dark: String) {
        self.init(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return NSColor(Color(hex: isDark ? dark : light))
        })
    }
}

/// A rounded panel with a tinted title band.
struct Card<Content: View>: View {
    var title: String?
    var symbol: String?
    var tint: Color = Theme.accent
    var trailing: AnyView?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let title {
                HStack(spacing: 7) {
                    if let symbol { Image(systemName: symbol).font(.callout.weight(.bold)).foregroundStyle(tint) }
                    Text(title).font(.headline)
                    Spacer()
                    trailing
                }
                .padding(.horizontal, 12).padding(.vertical, 8)
                .background(UnevenRoundedRectangle(topLeadingRadius: 12, topTrailingRadius: 12, style: .continuous).fill(tint.opacity(0.13)))
                .overlay(alignment: .bottom) { Rectangle().fill(tint.opacity(0.35)).frame(height: 1) }
            }
            content.padding(12)
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.cardFill))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Theme.cardStroke, lineWidth: 1.5))
        .shadow(color: .black.opacity(0.14), radius: 4, y: 2)
    }
}

extension View {
    /// A heading inside a card ("KEEP IT SAFE FIRST"): bold and full contrast.
    func sectionLabel() -> some View {
        font(.system(size: 11, weight: .heavy)).foregroundStyle(.primary).kerning(0.6)
    }
}

struct Pill: View {
    let text: String
    var symbol: String?
    var color: Color

    var body: some View {
        HStack(spacing: 5) {
            if let symbol { Image(systemName: symbol) }
            Text(text)
        }
        .font(.callout.weight(.semibold))
        .foregroundStyle(color)
        .padding(.horizontal, 11).padding(.vertical, 5)
        .background(Capsule().fill(color.opacity(0.14)))
        .fixedSize()
    }
}

struct SymbolTile: View {
    let symbol: String
    let color: Color
    var size: CGFloat = 30

    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.28, style: .continuous)
            .fill(LinearGradient(colors: [color, color.opacity(0.75)], startPoint: .top, endPoint: .bottom))
            .frame(width: size, height: size)
            .overlay(Image(systemName: symbol).font(.system(size: size * 0.48, weight: .semibold)).foregroundStyle(.white))
    }
}

/// Capsule buttons in Sentinel's three weights.
struct CapsuleButton: View {
    enum Style { case fill, soft, ghost }
    let title: String
    var symbol: String?
    var color: Color = Theme.accent
    var style: Style = .fill
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                if let symbol { Image(systemName: symbol) }
                Text(title)
            }
            .font(.callout.weight(.semibold))
            .foregroundStyle(style == .fill ? Color.white : (style == .ghost ? Color.secondary : color))
            .padding(.horizontal, 13).padding(.vertical, 6)
            .background(Capsule().fill(style == .fill ? color : (style == .soft ? color.opacity(0.14) : .clear)))
            .overlay(Capsule().strokeBorder(style == .ghost ? Color.secondary.opacity(0.6) : .clear, lineWidth: 1.2))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

/// A horizontal bar: a share of the track, in one colour.
struct Meter: View {
    let value: Double
    var color: Color = Theme.good
    var height: CGFloat = 10

    var body: some View {
        GeometryReader { g in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.secondary.opacity(0.15))
                Capsule().fill(color).frame(width: max(0, min(1, value)) * g.size.width)
            }
        }
        .frame(height: height)
    }
}

enum Tab: Int, CaseIterable, Identifiable {
    case checkup, library, cleanUp, backup, activity, bin, settings
    var id: Int { rawValue }

    static func named(_ name: String) -> Tab? { allCases.first { $0.title == name } }

    var title: String {
        switch self {
        case .checkup: "Checkup"
        case .library: "Library"
        case .cleanUp: "Clean up"
        case .backup: "Backup"
        case .activity: "Activity"
        case .bin: "Bin"
        case .settings: "Settings"
        }
    }

    var symbol: String {
        switch self {
        case .checkup: "stethoscope"
        case .library: "chart.bar.xaxis"
        case .cleanUp: "sparkles"
        case .backup: "icloud.and.arrow.up.fill"
        case .activity: "waveform.path.ecg"
        case .bin: "trash.fill"
        case .settings: "gearshape.fill"
        }
    }

    var color: Color {
        switch self {
        case .checkup: Color(light: "#db2777", dark: "#f472b6")
        case .library: Color(light: "#4f46e5", dark: "#818cf8")
        case .cleanUp: Color(light: "#c2410c", dark: "#fb923c")
        case .backup: Color(light: "#0369a1", dark: "#38bdf8")
        case .activity: Color(light: "#15803d", dark: "#4ade80")
        case .bin: Color(light: "#b45309", dark: "#fbbf24")
        case .settings: Color(light: "#64748b", dark: "#94a3b8")
        }
    }
}

/// Icon-over-label tabs, each with its own colour; the selected one fills.
struct TabBar: View {
    @Binding var selection: Tab
    var badges: [Tab: Int] = [:]
    @State private var hover: Tab?

    var body: some View {
        HStack(spacing: 4) {
            ForEach(Tab.allCases) { tab in
                let on = tab == selection
                Button { selection = tab } label: {
                    VStack(spacing: 3) {
                        Image(systemName: tab.symbol).font(.system(size: 14, weight: .semibold))
                        Text(tab.title).font(.caption.weight(on ? .semibold : .medium))
                    }
                    .foregroundStyle(on ? Color.white : tab.color)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                    .background(
                        RoundedRectangle(cornerRadius: 9, style: .continuous)
                            .fill(on ? AnyShapeStyle(LinearGradient(colors: [tab.color, tab.color.opacity(0.8)], startPoint: .top, endPoint: .bottom))
                                     : AnyShapeStyle(tab.color.opacity(hover == tab ? 0.14 : 0.06)))
                    )
                    .overlay(alignment: .topTrailing) {
                        if let n = badges[tab], n > 0 {
                            Text("\(n)").font(.system(size: 9, weight: .bold)).foregroundStyle(.white)
                                .padding(.horizontal, 4).padding(.vertical, 1)
                                .background(Capsule().fill(Color.red))
                                .offset(x: -6, y: 3)
                        }
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .onHover { hover = $0 ? tab : (hover == tab ? nil : hover) }
                .help(tab.title)
            }
        }
    }
}
