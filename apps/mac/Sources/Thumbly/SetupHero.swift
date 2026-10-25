import SwiftUI

/// The big block at the top of setup: a small looping scene that acts out the current step
/// (a cable plugging in, a Trust tap, the Developer Mode switch, a finish mark). Still when the
/// owner turns on Reduce motion.
struct SetupHero: View {
    var step: SetupView.Step
    var index: Int
    var count: Int
    var hint: String

    var body: some View {
        HStack(alignment: .top, spacing: 28) {
            Scene(step: step)
                .frame(width: 150, height: 150)
                .id(step)
                .transition(.opacity)
            VStack(alignment: .leading, spacing: 10) {
                Text("Step \(index + 1) of \(count)").kicker()
                Text(step.title).font(Theme.display(26)).displayTracking(26).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text(hint).font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                ProgressTrack(index: index, count: count).padding(.top, 6)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 24)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .top) { Rule() }
        .animation(.spring(response: 0.5, dampingFraction: 0.8), value: step)
    }
}

/// One cell per step: done and current cells fill with ink and accent, the rest stay empty.
private struct ProgressTrack: View {
    var index: Int
    var count: Int
    var body: some View {
        HStack(spacing: Theme.rule) {
            ForEach(0..<count, id: \.self) { i in
                Rectangle().fill(i < index ? Theme.ink : (i == index ? Theme.accent : Theme.surface))
            }
        }
        .frame(height: 6).frame(maxWidth: 280)
        .animation(.easeOut(duration: 0.3), value: index)
    }
}

/// One looping illustration per step, drawn with SF Symbols and flat rectangles.
private struct Scene: View {
    var step: SetupView.Step
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var on = false

    var body: some View {
        ZStack {
            Rectangle().fill(Theme.surface)
            content.clipped()
        }
        .overlay(Rectangle().strokeBorder(Theme.ink, lineWidth: Theme.rule))
        .onAppear {
            guard !reduceMotion else { on = true; return }
            withAnimation(.easeInOut(duration: step == .ready ? 0.9 : 1.4).repeatForever(autoreverses: true)) { on = true }
        }
    }

    @ViewBuilder private var content: some View {
        switch step {
        case .plug: plug
        case .trust: trust
        case .developer: developer
        case .ready: ready
        }
    }

    private var phone: some View {
        Image(systemName: "iphone.gen3").font(.system(size: 58, weight: .light)).foregroundStyle(Theme.ink)
    }

    /// A cable rises into the phone's port and sparks on contact.
    private var plug: some View {
        ZStack {
            phone.offset(y: -14)
            VStack(spacing: 0) {
                Rectangle().fill(Theme.accent).frame(width: 9, height: 12)
                Rectangle().fill(Theme.ink).frame(width: 15, height: 18)
                Rectangle().fill(Theme.ink).frame(width: 4, height: 40)
            }
            .offset(y: on ? 46 : 64)
            Image(systemName: "bolt.fill").font(.system(size: 14)).foregroundStyle(Theme.accent)
                .offset(x: 22, y: 20).opacity(on ? 1 : 0)
        }
    }

    /// The Trust prompt appears and a finger taps it.
    private var trust: some View {
        ZStack {
            phone
            Text("Trust").font(Theme.text(9, .heavy)).foregroundStyle(on ? Theme.canvas : Theme.onAccent)
                .padding(.horizontal, 6).padding(.vertical, 3)
                .background(on ? Theme.ink : Theme.accentFill)
            Image(systemName: "hand.point.up.left.fill").font(.system(size: 26)).foregroundStyle(Theme.ink)
                .offset(x: on ? 14 : 34, y: on ? 18 : 40)
        }
    }

    /// The Developer Mode switch flips on.
    private var developer: some View {
        VStack(spacing: 12) {
            Image(systemName: "hammer.fill").font(.system(size: 30)).foregroundStyle(Theme.ink)
                .rotationEffect(.degrees(on ? -18 : 8), anchor: .bottomTrailing)
            ZStack(alignment: on ? .trailing : .leading) {
                Rectangle().fill(on ? Theme.accent : Theme.canvas)
                    .overlay(Rectangle().strokeBorder(on ? Theme.accent : Theme.ink, lineWidth: Theme.rule))
                    .frame(width: 52, height: 28)
                Rectangle().fill(on ? Theme.onAccent : Theme.ink).frame(width: 18, height: 18).padding(5)
            }
        }
    }

    /// A seal with square frames stepping outwards.
    private var ready: some View {
        ZStack {
            ForEach(0..<2) { ring in
                Rectangle().strokeBorder(Theme.accent.opacity(on ? 0 : 0.8), lineWidth: Theme.rule)
                    .scaleEffect(on ? 1.0 + Double(ring) * 0.15 : 0.5)
                    .frame(width: 100, height: 100)
            }
            Image(systemName: "checkmark.seal.fill").font(.system(size: 60)).foregroundStyle(Theme.accent)
        }
    }
}
