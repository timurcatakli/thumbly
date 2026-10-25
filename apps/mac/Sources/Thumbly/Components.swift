import SwiftUI

/// The rounded-square brand mark: ink-to-coral gradient with a thumb-on-phone glyph.
struct BrandMark: View {
    var size: CGFloat = 36
    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.28, style: .continuous)
            .fill(Theme.brandGradient)
            .overlay(
                Image(systemName: "hand.point.up.left.fill")
                    .font(.system(size: size * 0.5, weight: .semibold))
                    .foregroundStyle(.white)
                    .offset(x: size * 0.03, y: size * 0.03))
            .frame(width: size, height: size)
            .shadow(color: Theme.coral.opacity(0.25), radius: size * 0.15, y: size * 0.06)
    }
}

extension Health {
    var color: Color {
        switch self {
        case .good: Theme.mint
        case .busy: Theme.amber
        case .attention: Theme.amber
        case .offline: Theme.danger
        }
    }
}

struct StatusDot: View {
    var color: Color
    var size: CGFloat = 9
    var body: some View {
        Circle().fill(color).frame(width: size, height: size)
            .overlay(Circle().stroke(color.opacity(0.35), lineWidth: size * 0.45).scaleEffect(1.5))
    }
}

struct Card<Content: View>: View {
    var padding: CGFloat = 20
    var stroke: Color? = nil
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
                .strokeBorder(stroke ?? Theme.hairline, lineWidth: stroke == nil ? 1 : 2))
    }
}

struct Chip: View {
    var text: String
    var icon: String? = nil
    var tint: Color = Theme.inkSoft
    var body: some View {
        HStack(spacing: 4) {
            if let icon { Image(systemName: icon).imageScale(.small) }
            Text(text)
        }
        .font(.system(size: 11.5, weight: .medium))
        .foregroundStyle(tint)
        .padding(.horizontal, 9).padding(.vertical, 4)
        .background(Theme.sunken, in: Capsule())
    }
}

struct PageHeader: View {
    var title: String
    var subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(Theme.display(30)).foregroundStyle(Theme.ink)
            Text(subtitle).font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Filled pill button. `fill` decides the role: coral = primary, danger = stop, sunken = quiet.
struct PillButtonStyle: ButtonStyle {
    var fill: Color = Theme.coral
    var text: Color = Theme.onAccent
    var large = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: large ? 15 : 13, weight: .semibold))
            .foregroundStyle(text)
            .padding(.horizontal, large ? 22 : 14)
            .padding(.vertical, large ? 13 : 7)
            .frame(maxWidth: large ? .infinity : nil)
            .background(fill.opacity(configuration.isPressed ? 0.8 : 1), in: Capsule())
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
            .contentShape(Capsule())
    }
}

extension ButtonStyle where Self == PillButtonStyle {
    static var primary: PillButtonStyle { PillButtonStyle() }
    static var quiet: PillButtonStyle { PillButtonStyle(fill: Theme.sunken, text: Theme.ink) }
    static var danger: PillButtonStyle { PillButtonStyle(fill: Theme.danger, text: .white) }
}

struct EmptyNote: View {
    var icon: String
    var title: String
    var detail: String
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: icon).font(.system(size: 28)).foregroundStyle(Theme.inkSoft.opacity(0.6))
            Text(title).font(Theme.display(16, .semibold)).foregroundStyle(Theme.ink)
            Text(detail).font(.system(size: 12.5)).foregroundStyle(Theme.inkSoft).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 28)
    }
}

enum Fmt {
    static func relative(_ epoch: Double?) -> String {
        guard let epoch else { return "" }
        // The service clock can run a little ahead of this one: never say "in 0 seconds".
        if Date.now.timeIntervalSince1970 - epoch < 10 { return "just now" }
        return RelativeDateTimeFormatter().localizedString(for: Date(timeIntervalSince1970: epoch), relativeTo: .now)
    }

    static func time(_ epoch: Double) -> String {
        Date(timeIntervalSince1970: epoch).formatted(.dateTime.month(.abbreviated).day().hour().minute().second())
    }

    static func date(_ epoch: Double) -> String {
        Date(timeIntervalSince1970: epoch).formatted(.dateTime.month(.abbreviated).day().hour().minute())
    }

    static func duration(_ seconds: Double) -> String {
        let s = Int(seconds)
        if s < 60 { return "\(s)s" }
        if s < 3600 { return "\(s / 60) min" }
        if s < 86400 { return "\(s / 3600) h" }
        return "\(s / 86400) days"
    }

    static func scope(_ scope: String) -> String {
        switch scope {
        case "phone:read": "See the screen"
        case "phone:control": "Tap & type"
        case "owner:admin": "Owner admin"
        default: scope
        }
    }
}
