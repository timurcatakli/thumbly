import SwiftUI

struct StatusView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmPause = false

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            PageHeader(title: "Hi there", subtitle: "Here's how your iPhone and your AI helpers are doing.")
            ConnectionCard()

            HStack(spacing: 14) {
                StatTile(icon: "sparkles", value: "\(model.approvedClients.count)", label: "AI clients allowed",
                         tint: Theme.coralText)
                StatTile(icon: "bell.badge.fill", value: "\(model.pendingCount)", label: "Waiting for you",
                         tint: model.pendingCount > 0 ? Theme.amberText : Theme.inkSoft)
                    .onTapGesture { model.section = .access }
                StatTile(icon: model.isPaused ? "pause.circle.fill" : "hand.tap.fill", value: controlValue,
                         label: controlLabel, tint: model.isPaused ? Theme.danger : Theme.mintText)
            }

            Card(padding: 22) {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Emergency brake").font(Theme.display(18, .semibold))
                        Spacer()
                        if model.isPaused { Chip(text: "Paused", icon: "pause.fill", tint: Theme.danger) }
                    }
                    Text(model.isPaused
                         ? "AI clients can't see or use your iPhone until you resume."
                         : "Pause instantly stops AI clients from seeing or using your iPhone, until you resume it here.")
                        .font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
                    if model.isPaused {
                        Button { Task { await model.resume() } } label: {
                            Label("Resume AI control", systemImage: "play.fill")
                        }
                        .buttonStyle(PillButtonStyle(fill: Theme.mint, large: true))
                    } else {
                        Button { confirmPause = true } label: {
                            Label("Pause AI control", systemImage: "hand.raised.fill")
                        }
                        .buttonStyle(PillButtonStyle(fill: Theme.danger, text: .white, large: true))
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
        Card(padding: 26) {
            HStack(spacing: 24) {
                PhoneBadge(color: model.health.color, busy: model.health == .busy)
                VStack(alignment: .leading, spacing: 8) {
                    Text(model.headline).font(Theme.display(26)).fixedSize(horizontal: false, vertical: true)
                    if let message = c?.message, message.lowercased() != "connected" {
                        Text(message).font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
                    } else {
                        Text("AI agents can see and use it when you allow them.")
                            .font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
                    }
                    HStack(spacing: 6) {
                        Chip(text: "iPhone", icon: "iphone")
                        if let v = c?.product_version { Chip(text: "iOS \(v)", icon: "apple.logo") }
                        if let t = c?.transport { Chip(text: t == "usb" ? "USB cable" : t.capitalized, icon: t == "usb" ? "cable.connector" : "wifi") }
                        if c?.developer_mode == true { Chip(text: "Developer Mode", icon: "hammer.fill", tint: Theme.mintText) }
                    }
                    .padding(.top, 4)
                }
                Spacer(minLength: 0)
            }
        }
    }
}

/// A phone glyph sitting in a softly glowing ring that breathes while connecting.
struct PhoneBadge: View {
    var color: Color
    var busy = false
    @State private var pulse = false

    var body: some View {
        ZStack {
            Circle().fill(color.opacity(0.14)).frame(width: 104, height: 104)
                .scaleEffect(busy && pulse ? 1.08 : 1)
            Circle().strokeBorder(color.opacity(0.5), lineWidth: 2).frame(width: 82, height: 82)
            Image(systemName: "iphone.gen3").font(.system(size: 38, weight: .light)).foregroundStyle(Theme.ink)
            Circle().fill(color).frame(width: 18, height: 18)
                .overlay(Circle().strokeBorder(Theme.surface, lineWidth: 3))
                .offset(x: 30, y: 30)
        }
        .onAppear { withAnimation(.easeInOut(duration: 1.1).repeatForever()) { pulse = true } }
    }
}

private struct StatTile: View {
    var icon: String
    var value: String
    var label: String
    var tint: Color

    var body: some View {
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 6) {
                Image(systemName: icon).foregroundStyle(tint)
                Text(value).font(Theme.display(24)).lineLimit(1).minimumScaleFactor(0.6)
                Text(label).font(.system(size: 12)).foregroundStyle(Theme.inkSoft)
            }
        }
    }
}
