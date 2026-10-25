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
        VStack(alignment: .leading, spacing: 22) {
            PageHeader(title: "Let's set up your iPhone", subtitle: "Four quick steps. Keep your iPhone unlocked and nearby.")
            SetupHero(step: current, index: current.rawValue, count: Step.allCases.count, hint: hint(for: current))
            Card(padding: 8) {
                VStack(spacing: 0) {
                    ForEach(Step.allCases, id: \.self) { step in
                        StepRow(step: step, state: step.rawValue < current.rawValue ? .done : (step == current ? .now : .later)) {
                            if step == current { action(for: step) }
                        }
                        if step != .ready {
                            Capsule().fill(step.rawValue < current.rawValue ? Theme.mint : Theme.hairline)
                                .frame(width: 2, height: 14).padding(.leading, 34)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
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
        Text(text).font(.system(size: 13)).foregroundStyle(Theme.inkSoft).fixedSize(horizontal: false, vertical: true)
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
                if state == .now { PulseRing() }
                Circle().fill(fill).frame(width: 40, height: 40)
                Image(systemName: state == .done ? "checkmark" : step.icon)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(state == .later ? Theme.inkSoft : Theme.onAccent)
            }
            VStack(alignment: .leading, spacing: 8) {
                Text(step.title).font(Theme.display(17, state == .now ? .bold : .medium))
                    .foregroundStyle(state == .later ? Theme.inkSoft : Theme.ink)
                    .padding(.top, 9)
                detail
            }
            Spacer(minLength: 0)
            if state == .done { Chip(text: "Done", icon: "checkmark", tint: Theme.mintText).padding(.top, 9) }
        }
        .padding(.horizontal, 15).padding(.vertical, 8)
        .background(state == .now ? Theme.coral.opacity(0.08) : .clear, in: RoundedRectangle(cornerRadius: 14))
    }

    private var fill: Color {
        switch state {
        case .done: Theme.mint
        case .now: Theme.coral
        case .later: Theme.sunken
        }
    }
}

/// A coral ring that keeps expanding and fading behind the current step's icon.
private struct PulseRing: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var go = false

    var body: some View {
        Circle().strokeBorder(Theme.coral, lineWidth: 2).frame(width: 40, height: 40)
            .scaleEffect(go ? 1.5 : 1).opacity(go ? 0 : 0.7)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeOut(duration: 1.6).repeatForever(autoreverses: false)) { go = true }
            }
    }
}
