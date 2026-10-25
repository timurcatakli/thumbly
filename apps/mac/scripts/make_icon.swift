// Draws Thumbly's app icon and writes AppIcon.icns.
// Usage: swift scripts/make_icon.swift <out-dir>
import AppKit

func color(_ hex: UInt32, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: a)
}

func drawIcon(size: CGFloat) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = size / 1024
    let ctx = NSGraphicsContext.current!.cgContext

    // Base tile on the macOS icon grid (824pt square, ~185pt corner) with a soft drop shadow.
    let tile = NSRect(x: 100 * s, y: 100 * s, width: 824 * s, height: 824 * s)
    let tilePath = NSBezierPath(roundedRect: tile, xRadius: 185 * s, yRadius: 185 * s)
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -10 * s), blur: 28 * s, color: color(0x000000, 0.35).cgColor)
    color(0x1B2448).setFill(); tilePath.fill()
    ctx.restoreGState()
    tilePath.addClip()
    NSGradient(colors: [color(0x141B38), color(0x2E2A5A), color(0x7A3F6E), color(0xFF7A59)],
               atLocations: [0, 0.45, 0.75, 1], colorSpace: .sRGB)!.draw(in: tilePath, angle: -50)

    // Phone: tilted rounded rectangle, glassy screen.
    ctx.saveGState()
    ctx.translateBy(x: 470 * s, y: 540 * s)
    ctx.rotate(by: 0.16)
    let phone = NSRect(x: -170 * s, y: -300 * s, width: 340 * s, height: 600 * s)
    let body = NSBezierPath(roundedRect: phone, xRadius: 64 * s, yRadius: 64 * s)
    color(0xF7F3EC).setFill(); body.fill()
    let screen = NSBezierPath(roundedRect: phone.insetBy(dx: 22 * s, dy: 22 * s), xRadius: 46 * s, yRadius: 46 * s)
    NSGradient(starting: color(0x18203D), ending: color(0x2B3566))!.draw(in: screen, angle: 90)
    // Dynamic-island pill
    color(0x0A0F1F).setFill()
    NSBezierPath(roundedRect: NSRect(x: -48 * s, y: 236 * s, width: 96 * s, height: 26 * s), xRadius: 13 * s, yRadius: 13 * s).fill()
    // Touch ripple in mint
    for (r, a) in [(92.0, 0.18), (62.0, 0.35), (30.0, 1.0)] as [(CGFloat, CGFloat)] {
        color(0x45DDA8, a).setFill()
        NSBezierPath(ovalIn: NSRect(x: -r * s, y: (20 - r) * s, width: 2 * r * s, height: 2 * r * s)).fill()
    }
    ctx.restoreGState()

    // Thumb: a warm peach capsule reaching in from the lower right, tip on the ripple.
    ctx.saveGState()
    ctx.translateBy(x: 430 * s, y: 490 * s)
    ctx.rotate(by: -0.78)
    ctx.setShadow(offset: CGSize(width: 0, height: -8 * s), blur: 24 * s, color: color(0x000000, 0.35).cgColor)
    let thumb = NSBezierPath(roundedRect: NSRect(x: -78 * s, y: -520 * s, width: 156 * s, height: 540 * s),
                             xRadius: 78 * s, yRadius: 78 * s)
    NSGradient(starting: color(0xFFB59A), ending: color(0xFF8B6B))!.draw(in: thumb, angle: 0)
    ctx.setShadow(offset: .zero, blur: 0, color: nil)
    // Nail
    color(0xFFF1EA, 0.9).setFill()
    NSBezierPath(roundedRect: NSRect(x: -46 * s, y: -110 * s, width: 92 * s, height: 112 * s), xRadius: 46 * s, yRadius: 46 * s).fill()
    ctx.restoreGState()

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
