import AppKit
import CoreText
import SwiftUI

/// Thumbly's "Modernist" palette: ink on off-white with one red accent. No second hue: status
/// collapses to ink (fine) and accent (look here). Every color has a light and a dark variant.
enum Theme {
    // Surfaces
    static let canvas = dynamic(light: 0xF3F2F2, dark: 0x181616)      // window background
    static let surface = dynamic(light: 0xEAE9E9, dark: 0x232121)     // wells, code blocks, hover fill
    static let sunken = dynamic(light: 0xEAE7E7, dark: 0x201E1D)      // sidebar

    // Text (inkSoft stays ≥ 4.5:1 on canvas, surface and sunken)
    static let ink = dynamic(light: 0x201E1D, dark: 0xF3F2F2)
    static let inkSoft = dynamic(light: 0x201E1D, dark: 0xF3F2F2, alpha: 0.72)
    static let divider = dynamic(light: 0x201E1D, dark: 0xF3F2F2, alpha: 0.40, darkAlpha: 0.32)
    static let hover = dynamic(light: 0x201E1D, dark: 0xF3F2F2, alpha: 0.04)

    // The one accent. Base red is for marks and 24pt+ numerals; small text uses accentText. Fills
    // that carry a label use accentFill, a step deeper in light mode, so the label clears 4.5:1;
    // dark mode puts dark ink on the red instead.
    static let accent = dynamic(light: 0xEC3013, dark: 0xFF563C)
    static let accentFill = dynamic(light: 0xCC2A10, dark: 0xFF563C)
    static let accentPressed = dynamic(light: 0xB82410, dark: 0xFF9783)
    static let accentText = dynamic(light: 0xAE1800, dark: 0xFF9783)
    static let onAccent = dynamic(light: 0xF3F2F2, dark: 0x181616)

    // Connected: the one place a second hue appears, so "your iPhone is connected" reads at a glance.
    static let success = dynamic(light: 0x1F9150, dark: 0x3DCB7A)      // status mark fill
    static let successText = dynamic(light: 0x166F3B, dark: 0x4FD68A)  // status headline
    static let onSuccess = dynamic(light: 0xF3F2F2, dark: 0x181616)

    // Structure: strong rules organise the page, hairlines separate rows.
    static let rule: CGFloat = 2
    static let hairline: CGFloat = 1

    // Type: Schibsted Grotesk for everything, SF Mono for code.
    static let fontName = "SchibstedGrotesk-Regular"

    /// Headings: ExtraBold by default.
    static func display(_ size: CGFloat, _ weight: Font.Weight = .heavy) -> Font { text(size, weight) }

    static func text(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        guard fontRegistered else { return .system(size: size, weight: weight) }
        // Set the variable font's weight axis directly: SwiftUI's .weight() can't reach it reliably.
        let descriptor = NSFontDescriptor(fontAttributes: [
            .name: fontName,
            .variation: [NSNumber(value: wghtAxis): NSNumber(value: axisValue(weight))],
        ])
        return NSFont(descriptor: descriptor, size: size).map { Font($0) } ?? .system(size: size, weight: weight)
    }

    static func mono(_ size: CGFloat = 12) -> Font { .system(size: size, design: .monospaced) }

    private static let wghtAxis: UInt32 = 0x7767_6874 // 'wght'
    private static func axisValue(_ weight: Font.Weight) -> Double {
        switch weight {
        case .black: 900
        case .heavy: 800
        case .bold: 700
        case .semibold: 600
        case .medium: 500
        default: 400 // The font's lightest master.
        }
    }

    nonisolated(unsafe) private(set) static var fontRegistered = false
    static func registerFonts() {
        let candidates = [
            Bundle.main.url(forResource: "SchibstedGrotesk", withExtension: "ttf"),
            URL(fileURLWithPath: #filePath).deletingLastPathComponent()
                .appendingPathComponent("../../Resources/SchibstedGrotesk.ttf").standardized,
        ].compactMap { $0 }
        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            var error: Unmanaged<CFError>?
            if CTFontManagerRegisterFontsForURL(url as CFURL, .process, &error) || NSFont(name: fontName, size: 12) != nil {
                fontRegistered = true
                return
            }
        }
    }

    private static func dynamic(light: UInt32, dark: UInt32, alpha: CGFloat = 1, darkAlpha: CGFloat? = nil) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
                ? NSColor(hex: dark).withAlphaComponent(darkAlpha ?? alpha)
                : NSColor(hex: light).withAlphaComponent(alpha)
        })
    }
}

extension View {
    /// Small uppercase label above a title or heading a table column.
    func kicker(_ color: Color = Theme.accentText) -> some View {
        font(Theme.text(11, .semibold)).tracking(0.88).textCase(.uppercase).foregroundStyle(color)
    }

    /// Display titles run tight: -0.02em.
    func displayTracking(_ size: CGFloat) -> some View { tracking(-0.02 * size) }
}

extension NSColor {
    convenience init(hex: UInt32) {
        self.init(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                  blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    }
}

extension Color {
    init(hex: UInt32) { self.init(nsColor: NSColor(hex: hex)) }
}
