// Draws Thumbly's app icon and writes AppIcon.icns.
// Usage: swift scripts/make_icon.swift <out-dir>
import AppKit

func color(_ hex: UInt32, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: a)
}

/// The Modernist brand mark: flat accent red to every edge, no gradient or shadow, with the
/// hand.point.up.left.fill glyph nudged 3% right and down — the same mark BrandMark draws in the app.
/// Full bleed on purpose: macOS 26 masks it to the system shape, where an inset tile would sit in a
/// gray frame.
func drawIcon(size: CGFloat) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = size / 1024

    let tile = NSRect(x: 0, y: 0, width: 1024 * s, height: 1024 * s)
    color(0xEC3013).setFill()   // Theme.accent (light)
    tile.fill()

    let config = NSImage.SymbolConfiguration(pointSize: tile.width * 0.42, weight: .semibold)
        .applying(.init(paletteColors: [color(0xF3F2F2)]))   // Theme.onAccent (light)
    let glyph = NSImage(systemSymbolName: "hand.point.up.left.fill", accessibilityDescription: nil)!
        .withSymbolConfiguration(config)!
    let g = glyph.size
    glyph.draw(in: NSRect(x: tile.midX - g.width / 2 + tile.width * 0.03,
                          y: tile.midY - g.height / 2 - tile.width * 0.03, width: g.width, height: g.height))

    NSGraphicsContext.restoreGraphicsState()
    return rep
}

let out = URL(fileURLWithPath: CommandLine.arguments.dropFirst().first ?? ".")
let iconset = out.appendingPathComponent("AppIcon.iconset")
try? FileManager.default.removeItem(at: iconset)
try! FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let name = scale == 1 ? "icon_\(base)x\(base).png" : "icon_\(base)x\(base)@2x.png"
        let png = drawIcon(size: CGFloat(base * scale)).representation(using: .png, properties: [:])!
        try! png.write(to: iconset.appendingPathComponent(name))
    }
}
try! drawIcon(size: 1024).representation(using: .png, properties: [:])!.write(to: out.appendingPathComponent("AppIcon-1024.png"))
let task = Process()
task.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
task.arguments = ["-c", "icns", iconset.path, "-o", out.appendingPathComponent("AppIcon.icns").path]
try! task.run()
task.waitUntilExit()
try? FileManager.default.removeItem(at: iconset)
print(task.terminationStatus == 0 ? "wrote \(out.path)/AppIcon.icns" : "iconutil failed")
