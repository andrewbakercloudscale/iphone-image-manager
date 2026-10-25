// Draws Photo Sentinel's icon and writes Resources/AppIcon.icns.
//   swift scripts/make-icon.swift
// A rounded square in the Checkup pink-to-violet, stacked photos, and a green shield:
// the photos are safe. Redraw by running it again; the .icns is committed.
import AppKit

func draw(_ size: CGFloat) -> NSImage {
    NSImage(size: NSSize(width: size, height: size), flipped: false) { rect in
        let s = size / 1024
        let inset = rect.insetBy(dx: 100 * s, dy: 100 * s)
        let tile = NSBezierPath(roundedRect: inset, xRadius: 185 * s, yRadius: 185 * s)
        NSGraphicsContext.current?.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowBlurRadius = 24 * s
        shadow.shadowOffset = NSSize(width: 0, height: -10 * s)
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.3)
        shadow.set()
        NSColor.white.setFill()
        tile.fill()
        NSGraphicsContext.current?.restoreGraphicsState()
        NSGradient(colors: [NSColor(srgbRed: 0.93, green: 0.33, blue: 0.55, alpha: 1),
                            NSColor(srgbRed: 0.55, green: 0.30, blue: 0.86, alpha: 1)])!
            .draw(in: tile, angle: -60)

        let photos = NSImage(systemSymbolName: "photo.on.rectangle.angled", accessibilityDescription: nil)!
            .withSymbolConfiguration(.init(pointSize: 430 * s, weight: .semibold))!
        let tinted = NSImage(size: photos.size, flipped: false) { r in
            photos.draw(in: r)
            NSColor.white.set()
            r.fill(using: .sourceAtop)
            return true
        }
        let p = tinted.size
        tinted.draw(in: NSRect(x: rect.midX - p.width / 2 - 40 * s, y: rect.midY - p.height / 2 + 30 * s, width: p.width, height: p.height))

        let badge = NSRect(x: 590 * s, y: 150 * s, width: 290 * s, height: 290 * s)
        NSColor.white.setFill()
        NSBezierPath(ovalIn: badge).fill()
        NSColor(srgbRed: 0.09, green: 0.64, blue: 0.29, alpha: 1).setFill()
        NSBezierPath(ovalIn: badge.insetBy(dx: 16 * s, dy: 16 * s)).fill()
        let shield = NSImage(systemSymbolName: "checkmark.shield.fill", accessibilityDescription: nil)!
            .withSymbolConfiguration(.init(pointSize: 150 * s, weight: .bold))!
        let white = NSImage(size: shield.size, flipped: false) { r in
            shield.draw(in: r)
            NSColor.white.set()
            r.fill(using: .sourceAtop)
            return true
        }
        white.draw(in: NSRect(x: badge.midX - white.size.width / 2, y: badge.midY - white.size.height / 2,
                              width: white.size.width, height: white.size.height))
        return true
    }
}

let root = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent().deletingLastPathComponent()
let set = FileManager.default.temporaryDirectory.appendingPathComponent("AppIcon.iconset")
try? FileManager.default.removeItem(at: set)
try FileManager.default.createDirectory(at: set, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let px = base * scale
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                                   samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        draw(CGFloat(px)).draw(in: NSRect(x: 0, y: 0, width: px, height: px))
        NSGraphicsContext.restoreGraphicsState()
        let name = scale == 1 ? "icon_\(base)x\(base).png" : "icon_\(base)x\(base)@2x.png"
        try rep.representation(using: .png, properties: [:])!.write(to: set.appendingPathComponent(name))
    }
}
let out = root.appendingPathComponent("Resources/AppIcon.icns")
let p = Process()
p.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
p.arguments = ["-c", "icns", set.path, "-o", out.path]
try p.run()
p.waitUntilExit()
print(p.terminationStatus == 0 ? "wrote \(out.path)" : "iconutil failed")
try? FileManager.default.copyItem(at: set.appendingPathComponent("icon_512x512@2x.png"), to: URL(fileURLWithPath: "/tmp/appicon-preview.png"))
