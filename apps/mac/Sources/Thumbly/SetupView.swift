import SwiftUI

/// First-run guide: Plug in → Trust → Developer Mode → Ready, driven by /api/overview + /api/status.
struct SetupView: View {
    @Environment(AppModel.self) private var model
    @State private var working = false

    enum Step: Int, CaseIterable {
        case plug, trust, developer, ready
        var title: String {
            switch self {
            case .plug: "Plug in your iPhone"
            case .trust: "Trust this Mac"
            case .developer: "Turn on Developer Mode"
            case .ready: "Ready"
            }
        }
        var icon: String {
            switch self {
            case .plug: "cable.connector"
            case .trust: "lock.open.iphone"
            case .developer: "hammer.fill"
            case .ready: "checkmark.seal.fill"
            }
        }
    }

    var body: some View {
        let current = currentStep
        VStack(alignment: .leading, spacing: 28) {
            PageHeader(title: "Let's set up your iPhone", subtitle: "Four quick steps. Keep your iPhone unlocked and nearby.")
            SetupHero(step: current, index: current.rawValue, count: Step.allCases.count, hint: hint(for: current))
            VStack(spacing: 0) {
                Rule()
                ForEach(Step.allCases, id: \.self) { step in
                    if step.rawValue > 0 { Rule(color: Theme.divider, weight: Theme.hairline) }
                    StepRow(step: step, state: step.rawValue < current.rawValue ? .done : (step == current ? .now : .later)) {
                        if step == current { action(for: step) }
                    }
                }
                Rule()
            }
            .animation(.spring(response: 0.45, dampingFraction: 0.85), value: current)
        }
    }

    private var currentStep: Step {
        let pairing = model.pairingState
        let conn = model.connectionState
        let present = model.overview?.cable == true || model.status?.connection?.device_present == true
        if (pairing == "forgotten" || (!present && ["off", "no_phone", "idle"].contains(pairing)))
            && model.overview?.bound != true { return .plug }
        if model.overview?.bound != true || conn == "trust_required" { return .trust }
        if conn == "developer_mode_required" || model.status?.connection?.developer_mode == false { return .developer }
        return .ready
    }

    private func hint(for step: Step) -> String {
        switch step {
        case .plug: plugHint
        case .trust: trustHint
        case .developer: "Thumbly needs Developer Mode. Tap Show the switch and Thumbly reveals it in Settings › Privacy & Security on your iPhone. Turn it on, then let the iPhone restart."
        case .ready: model.status?.connection?.message ?? "Almost there — Thumbly is finishing up."
        }
    }

    @ViewBuilder private func action(for step: Step) -> some View {
        if step == .developer {
            Button {
                working = true
                Task { await model.showDeveloperModeSwitch(); working = false }
            } label: {
                Label(working ? "Working…" : "Show the switch", systemImage: "wand.and.stars")
            }
            .buttonStyle(.primary).disabled(working)
        }
    }

    private var plugHint: String {
        switch model.pairingState {
        case "multiple_phones": "More than one iPhone is plugged in. Unplug all but the one you want to use."
        case "forgotten": "Thumbly forgot this iPhone. To use it again, unplug the cable and plug it back in. Or plug in a different iPhone."
        default: "Use a USB cable to connect your iPhone to this Mac."
        }
    }

    private var trustHint: String {
        switch model.pairingState {
        case "locked": "Unlock your iPhone to continue."
        case "denied": "Trust was declined. Unplug the cable, plug it back in and tap Trust."
        case "failed":
            "Pairing failed (\(model.overview?.usb_pairing?.last_error?["code"]?.string ?? "unknown")). Unplug and try again."
                + (model.overview?.usb_pairing?.retry_in.map { " Retrying in \(Int($0))s." } ?? "")
        case "pairing", "binding": "Pairing… keep your iPhone unlocked."
        default: "Unlock your iPhone and tap Trust when it asks about this Mac, then enter your passcode."
        }
    }

    private func hint(_ text: String) -> some View {
        Text(text).font(Theme.text(14)).foregroundStyle(Theme.inkSoft).fixedSize(horizontal: false, vertical: true)
    }
}

private struct StepRow<Detail: View>: View {
    enum State { case done, now, later }
    var step: SetupView.Step
    var state: State
    @ViewBuilder var detail: Detail

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            ZStack {
                Rectangle().fill(fill).frame(width: 40, height: 40)
                    .overlay { if state == .later { Rectangle().strokeBorder(Theme.divider, lineWidth: Theme.hairline) } }
                Image(systemName: state == .done ? "checkmark" : step.icon)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(iconColor)
                if state == .now { BlinkMark().offset(x: 20, y: -20) }
            }
            VStack(alignment: .leading, spacing: 8) {
                Text(step.title).font(Theme.display(18, state == .now ? .heavy : .semibold))
                    .foregroundStyle(state == .later ? Theme.inkSoft : Theme.ink)
                    .padding(.top, 9)
                detail
            }
            Spacer(minLength: 0)
            if state == .done { Chip(text: "Done", icon: "checkmark", tint: Theme.ink).padding(.top, 9) }
        }
        .padding(.horizontal, 16).padding(.vertical, 14)
        .background(state == .now ? Theme.surface : .clear)
        .overlay(alignment: .leading) {
            if state == .now { Rectangle().fill(Theme.accent).frame(width: 3) }
        }
    }

    private var fill: Color {
        switch state {
        case .done: Theme.ink
        case .now: Theme.accent
        case .later: .clear
        }
    }

    private var iconColor: Color {
        switch state {
        case .done: Theme.canvas
        case .now: Theme.onAccent
        case .later: Theme.inkSoft
        }
    }
}

/// A small accent square blinking on the current step's corner.
private struct BlinkMark: View {
    var body: some View {
        StatusDot(color: Theme.accent, size: 8, blink: true).padding(2).background(Theme.surface)
    }
}
