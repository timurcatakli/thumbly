import SwiftUI

/// The brand mark: a flat accent square with the thumb-on-phone glyph. No gradient, no shadow.
struct BrandMark: View {
    var size: CGFloat = 36
    var body: some View {
        Rectangle().fill(Theme.accent)
            .overlay(
                Image(systemName: "hand.point.up.left.fill")
                    .font(.system(size: size * 0.5, weight: .semibold))
                    .foregroundStyle(Theme.onAccent)
                    .offset(x: size * 0.03, y: size * 0.03))
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

extension Health {
    /// Mono status: ink when all is well, the accent whenever the owner should look.
    var color: Color {
        switch self {
        case .good: Theme.ink
        case .busy, .attention, .offline: Theme.accent
        }
    }
}

/// A solid status square. `blink` flashes it while something is in progress.
struct StatusDot: View {
    var color: Color
    var size: CGFloat = 9
    var blink = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var dim = false

    var body: some View {
        Rectangle().fill(color).frame(width: size, height: size)
            .opacity(blink && dim ? 0.15 : 1)
            .onAppear {
                guard blink, !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 0.6).repeatForever()) { dim = true }
            }
    }
}

/// A full-width rule. 2px organises sections; 1px separates rows.
struct Rule: View {
    var color: Color = Theme.ink
    var weight: CGFloat = Theme.rule
    var body: some View { Rectangle().fill(color).frame(height: weight).frame(maxWidth: .infinity) }
}

/// A 2px vertical divider between grid cells.
struct VRule: View {
    var color: Color = Theme.divider
    var body: some View { Rectangle().fill(color).frame(width: Theme.rule).frame(maxHeight: .infinity) }
}

/// A ruled block: no fill, a 2px ink rule on top. `stroke` frames it on all sides instead (for a
/// highlighted or destructive block).
struct Card<Content: View>: View {
    var padding: CGFloat = 22
    var stroke: Color? = nil
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(.vertical, padding)
            .padding(.horizontal, stroke == nil ? 0 : padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(alignment: .top) { if stroke == nil { Rule() } }
            .overlay { if let stroke { Rectangle().strokeBorder(stroke, lineWidth: Theme.rule) } }
    }
}

/// Cells in a row that share their borders: 2px ink rules above and below, 2px dividers between
/// (put a `VRule()` between cells).
struct GridRowCells<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        HStack(alignment: .top, spacing: 0) { content }
            .fixedSize(horizontal: false, vertical: true)
            .overlay(alignment: .top) { Rule() }
            .overlay(alignment: .bottom) { Rule() }
    }
}

struct Chip: View {
    var text: String
    var icon: String? = nil
    var tint: Color = Theme.inkSoft
    var body: some View {
        HStack(spacing: 5) {
            if let icon { Image(systemName: icon).imageScale(.small) }
            Text(text)
        }
        .font(Theme.text(11, .semibold))
        .foregroundStyle(tint)
        .padding(.horizontal, 7).padding(.vertical, 3)
        .overlay(Rectangle().strokeBorder(Theme.divider, lineWidth: Theme.hairline))
    }
}

/// A small accent rectangle with a tabular count, for things waiting on the owner.
struct CountBadge: View {
    var count: Int
    var quiet = false
    var body: some View {
        Text("\(count)").font(Theme.text(11, .heavy).monospacedDigit())
            .foregroundStyle(quiet ? Theme.inkSoft : Theme.onAccent)
            .padding(.horizontal, 6).padding(.vertical, 1)
            .background(quiet ? Theme.surface : Theme.accentFill)
    }
}

struct PageHeader: View {
    var title: String
    var subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(Theme.display(30)).displayTracking(30).foregroundStyle(Theme.ink)
            Text(subtitle).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A section heading with an optional count, sitting on a 2px rule.
/// `ruled: false` when a TableHeader follows and brings its own rule.
struct SectionHeading: View {
    var title: String
    var count: Int? = nil
    var ruled = true
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text(title).font(Theme.display(18))
                if let count { CountBadge(count: count, quiet: true) }
            }
            if ruled { Rule() }
        }
    }
}

/// The uppercase header row of a ruled table, with a 2px rule under it.
struct TableHeader: View {
    var columns: [(String, CGFloat?)] // nil width: flexible
    var leading: CGFloat = 0
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                ForEach(Array(columns.enumerated()), id: \.offset) { _, column in
                    if let width = column.1 {
                        Text(column.0).kicker(Theme.inkSoft).frame(width: width, alignment: .leading)
                    } else {
                        Text(column.0).kicker(Theme.inkSoft).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .padding(.leading, leading).padding(.vertical, 8)
            Rule()
        }
    }
}

/// Flat rectangular button, label flush left. Roles: primary (red fill), secondary (1px frame),
/// ghost (red text), danger (red fill: red is the stop color).
struct RuleButtonStyle: ButtonStyle {
    enum Role { case primary, secondary, ghost, danger }
    var role: Role = .primary
    var full = false
    var small = false

    func makeBody(configuration: Configuration) -> some View {
        Styled(configuration: configuration, role: role, full: full, small: small)
    }

    private struct Styled: View {
        let configuration: Configuration
        let role: Role
        let full: Bool
        let small: Bool
        @Environment(\.isEnabled) private var enabled
        @Environment(\.isFocused) private var focused
        @State private var hover = false

        var body: some View {
            configuration.label
                .labelStyle(LeadingIconLabel())
                .font(Theme.text(small ? 12 : (full ? 15 : 13), filled ? .heavy : .semibold))
                .foregroundStyle(text)
                .padding(.horizontal, role == .ghost && small ? 0 : (full ? 18 : 14))
                .padding(.vertical, small ? 3 : (full ? 13 : 7))
                .frame(maxWidth: full ? .infinity : nil, alignment: .leading)
                .background(fill)
                .overlay { if role == .secondary { Rectangle().strokeBorder(Theme.divider, lineWidth: Theme.hairline) } }
                .overlay { if focused { Rectangle().strokeBorder(Theme.accent, lineWidth: Theme.rule).padding(-4) } }
                .contentShape(Rectangle())
                .opacity(enabled ? 1 : 0.45)
                .onHover { hover = $0 && enabled }
                .focusEffectDisabled()
        }

        private var filled: Bool { role == .primary || role == .danger }

        private var text: Color {
            switch role {
            case .primary, .danger: Theme.onAccent
            case .secondary: Theme.ink
            case .ghost: Theme.accentText
            }
        }

        private var fill: Color {
            switch role {
            case .primary, .danger: configuration.isPressed ? Theme.accentPressed : Theme.accentFill
            case .secondary: configuration.isPressed || hover ? Theme.surface : .clear
            case .ghost: configuration.isPressed || hover ? Theme.accent.opacity(0.1) : .clear
            }
        }
    }
}

/// Icon before title, aligned to the leading edge.
private struct LeadingIconLabel: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 8) { configuration.icon; configuration.title }
    }
}

extension ButtonStyle where Self == RuleButtonStyle {
    static var primary: RuleButtonStyle { RuleButtonStyle() }
    static var secondary: RuleButtonStyle { RuleButtonStyle(role: .secondary) }
    static var ghost: RuleButtonStyle { RuleButtonStyle(role: .ghost) }
    static var danger: RuleButtonStyle { RuleButtonStyle(role: .danger) }
}

/// A square on/off switch: a flat track with a square knob; on fills the track with the accent.
struct RuleToggleStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Button { configuration.isOn.toggle() } label: {
            HStack(spacing: 8) {
                ZStack(alignment: configuration.isOn ? .trailing : .leading) {
                    Rectangle().fill(configuration.isOn ? Theme.accent : Theme.surface)
                        .overlay(Rectangle().strokeBorder(configuration.isOn ? Theme.accent : Theme.divider,
                                                          lineWidth: Theme.hairline))
                    Rectangle().fill(configuration.isOn ? Theme.onAccent : Theme.ink).frame(width: 10, height: 10)
                        .padding(4)
                }
                .frame(width: 34, height: 18)
                configuration.label.foregroundStyle(Theme.ink)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .animation(.easeOut(duration: 0.12), value: configuration.isOn)
        .accessibilityRepresentation { Toggle(isOn: configuration.$isOn) { configuration.label } }
    }
}

/// Hover tint for table rows: ink at 4%.
struct RowHover: ViewModifier {
    @State private var hover = false
    func body(content: Content) -> some View {
        content.background(hover ? Theme.hover : .clear).onHover { hover = $0 }
    }
}

extension View {
    func rowHover() -> some View { modifier(RowHover()) }
}

struct EmptyNote: View {
    var icon: String
    var title: String
    var detail: String
    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: icon).font(.system(size: 20)).foregroundStyle(Theme.inkSoft).frame(width: 24)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(Theme.display(16)).foregroundStyle(Theme.ink)
                Text(detail).font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 22)
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
