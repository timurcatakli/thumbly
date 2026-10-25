import SwiftUI

struct StatusView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmPause = false

    var body: some View {
        VStack(alignment: .leading, spacing: 28) {
            PageHeader(title: "Hi there", subtitle: "Here's how your iPhone and your AI helpers are doing.")
            ConnectionCard()

            GridRowCells {
                StatTile(icon: "sparkles", value: "\(model.approvedClients.count)", label: "AI clients allowed",
                         strong: model.approvedClients.count > 0)
                VRule()
                StatTile(icon: "bell.badge.fill", value: "\(model.pendingCount)", label: "Waiting for you",
                         strong: model.pendingCount > 0)
                    .contentShape(Rectangle())
                    .onTapGesture { model.section = .access }
                VRule()
                StatTile(icon: model.isPaused ? "pause.circle.fill" : "hand.tap.fill", value: controlValue,
                         label: controlLabel, strong: model.isPaused || model.status?.control?.active == true)
            }

            Card(padding: 22) {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Emergency brake").font(Theme.display(20)).displayTracking(20)
                        Spacer()
                        if model.isPaused { Chip(text: "Paused", icon: "pause.fill", tint: Theme.accentText) }
                    }
                    Text(model.isPaused
                         ? "AI clients can't see or use your iPhone until you resume."
                         : "Pause instantly stops AI clients from seeing or using your iPhone, until you resume it here.")
                        .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                    if model.isPaused {
                        Button { Task { await model.resume() } } label: {
                            Label("Resume AI control", systemImage: "play.fill")
                        }
                        .buttonStyle(RuleButtonStyle(role: .primary, full: true))
                    } else {
                        Button { confirmPause = true } label: {
                            Label("Pause AI control", systemImage: "hand.raised.fill")
                        }
                        .buttonStyle(RuleButtonStyle(role: .danger, full: true))
                        .keyboardShortcut(".", modifiers: .command)
                    }
                }
            }
        }
        .confirmationDialog("Pause AI control?", isPresented: $confirmPause) {
            Button("Pause now", role: .destructive) { Task { await model.pause() } }
        } message: {
            Text("All AI input to your iPhone stops right away.")
        }
    }

    private var controlValue: String {
        guard let control = model.status?.control else { return "Idle" }
        if control.paused == true { return "Paused" }
        return control.active == true ? "In use" : "Idle"
    }

    private var controlLabel: String {
        guard let control = model.status?.control, control.paused != true, control.active == true else { return "Right now" }
        if let s = control.remaining_seconds, s > 0 { return "AI has control · \(Int(s))s left" }
        return "AI has control"
    }
}

private struct ConnectionCard: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let c = model.status?.connection
        Card(padding: 24) {
            HStack(alignment: .top, spacing: 24) {
                PhoneBadge(color: model.health.color, busy: model.health == .busy)
                VStack(alignment: .leading, spacing: 8) {
                    Text(model.headline).font(Theme.display(26)).displayTracking(26)
                        .fixedSize(horizontal: false, vertical: true)
                    if let message = c?.message, message.lowercased() != "connected" {
                        Text(message).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                    } else {
                        Text("AI agents can see and use it when you allow them.")
                            .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                    }
                    HStack(spacing: 6) {
                        Chip(text: "iPhone", icon: "iphone")
                        if let v = c?.product_version { Chip(text: "iOS \(v)", icon: "apple.logo") }
                        if let t = c?.transport { Chip(text: t == "usb" ? "USB cable" : t.capitalized, icon: t == "usb" ? "cable.connector" : "wifi") }
                        if c?.developer_mode == true { Chip(text: "Developer Mode", icon: "hammer.fill", tint: Theme.ink) }
                    }
                    .padding(.top, 4)
                }
                Spacer(minLength: 0)
            }
        }
    }
}

/// The phone glyph in a square 2px ink frame, with the status square on its corner. The square
/// blinks while connecting.
struct PhoneBadge: View {
    var color: Color
    var busy = false

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            Rectangle().strokeBorder(Theme.ink, lineWidth: Theme.rule)
                .frame(width: 88, height: 88)
                .overlay(Image(systemName: "iphone.gen3").font(.system(size: 38, weight: .light)).foregroundStyle(Theme.ink))
            StatusDot(color: color, size: 16, blink: busy)
                .padding(3).background(Theme.canvas)
                .offset(x: 8, y: 8)
        }
        .padding(.trailing, 8).padding(.bottom, 8)
    }
}

private struct StatTile: View {
    var icon: String
    var value: String
    var label: String
    var strong: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Image(systemName: icon).foregroundStyle(strong ? Theme.accentText : Theme.inkSoft)
            Text(value).font(Theme.display(34).monospacedDigit()).displayTracking(34)
                .foregroundStyle(strong ? Theme.accent : Theme.ink)
                .lineLimit(1).minimumScaleFactor(0.7)
            Text(label).font(Theme.text(12.5)).foregroundStyle(Theme.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 18).padding(.vertical, 18)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
