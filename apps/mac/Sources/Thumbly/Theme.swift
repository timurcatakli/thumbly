import AppKit
import CoreText
import SwiftUI

/// Thumbly's palette: deep ink navy, warm coral for primary actions, mint for "all good",
/// amber for "needs a hand", red for Pause. Every color has a light and a dark variant.
enum Theme {
    // Surfaces
    static let canvas = dynamic(light: 0xF7F3EC, dark: 0x0E1326)      // window background (warm paper / night ink)
    static let surface = dynamic(light: 0xFFFFFF, dark: 0x171E3A)     // cards
    static let sunken = dynamic(light: 0xEFE9DF, dark: 0x0A0F1F)      // sidebar, wells
    static let hairline = dynamic(light: 0xE2DACB, dark: 0x262F55)

    // Text (all ≥ 4.5:1 on canvas and surface)
    static let ink = dynamic(light: 0x18203D, dark: 0xF2EEE6)
    static let inkSoft = dynamic(light: 0x545D7C, dark: 0xA6AECB)

    // Brand + status
    static let coral = dynamic(light: 0xFF7A59, dark: 0xFF8B6B)       // primary button fill (ink text on top)
    static let coralText = dynamic(light: 0xB3401F, dark: 0xFF9C80)   // coral used as text
    static let mint = dynamic(light: 0x18A877, dark: 0x45DDA8)
    static let mintText = dynamic(light: 0x0E7351, dark: 0x5FE6B6)
    static let amber = dynamic(light: 0xF2A93B, dark: 0xF6BC5C)
    static let amberText = dynamic(light: 0x8A5600, dark: 0xF6C46E)
    static let danger = dynamic(light: 0xD7373D, dark: 0xF0555A)
    static let onAccent = Color(hex: 0x18203D)                          // text on coral / mint / amber fills

    static let brandGradient = LinearGradient(
        colors: [Color(hex: 0x1B2448), Color(hex: 0x3A2E5E), Color(hex: 0xFF7A59)],
        startPoint: .topLeading, endPoint: .bottomTrailing)

    // Type: Space Grotesk for headings, SF Pro for everything else.
    static let fontFamily = "Space Grotesk"
    static func display(_ size: CGFloat, _ weight: Font.Weight = .bold) -> Font {
        fontRegistered ? .custom(fontFamily, size: size).weight(weight) : .system(size: size, weight: weight, design: .rounded)
    }

    nonisolated(unsafe) private(set) static var fontRegistered = false
    static func registerFonts() {
        let candidates = [
            Bundle.main.url(forResource: "SpaceGrotesk", withExtension: "ttf"),
            URL(fileURLWithPath: #filePath).deletingLastPathComponent()
                .appendingPathComponent("../../Resources/SpaceGrotesk.ttf").standardized,
        ].compactMap { $0 }
        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            var error: Unmanaged<CFError>?
            if CTFontManagerRegisterFontsForURL(url as CFURL, .process, &error) || NSFont(name: "SpaceGrotesk-Regular", size: 12) != nil {
                fontRegistered = true
                return
            }
        }
    }

    private static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? NSColor(hex: dark) : NSColor(hex: light)
        })
    }
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
