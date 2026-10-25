import SwiftUI

/// The big animated card at the top of setup: a small looping scene that acts out the current step
/// (a cable plugging in, a Trust tap, the Developer Mode switch, a celebration). Still when the
/// owner turns on Reduce motion.
struct SetupHero: View {
    var step: SetupView.Step
    var index: Int
    var count: Int
    var hint: String

    var body: some View {
        HStack(spacing: 28) {
            Scene(step: step)
                .frame(width: 150, height: 150)
                .id(step)
                .transition(.scale(scale: 0.85).combined(with: .opacity))
            VStack(alignment: .leading, spacing: 10) {
                Text("STEP \(index + 1) OF \(count)")
                    .font(.system(size: 11, weight: .bold)).tracking(1.2).foregroundStyle(Theme.coralText)
                Text(step.title).font(Theme.display(26)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text(hint).font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                ProgressTrack(fraction: Double(index + 1) / Double(count)).padding(.top, 6)
            }
            Spacer(minLength: 0)
        }
        .padding(26)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            ZStack {
                RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Theme.surface)
                Glow().clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
            })
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).strokeBorder(Theme.hairline))
        .animation(.spring(response: 0.5, dampingFraction: 0.8), value: step)
    }
}

/// A slow coral glow drifting behind the hero.
private struct Glow: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var drift = false

    var body: some View {
        GeometryReader { geo in
            Circle().fill(Theme.coral.opacity(0.18)).frame(width: 260, height: 260).blur(radius: 60)
                .offset(x: drift ? geo.size.width * 0.05 : -40, y: drift ? -30 : 20)
        }
        .allowsHitTesting(false)
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 6).repeatForever(autoreverses: true)) { drift = true }
        }
    }
}

private struct ProgressTrack: View {
    var fraction: Double
    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.sunken)
                Capsule().fill(LinearGradient(colors: [Theme.coral, Theme.amber], startPoint: .leading, endPoint: .trailing))
                    .frame(width: max(8, geo.size.width * fraction))
            }
        }
        .frame(height: 6).frame(maxWidth: 280)
        .animation(.spring(response: 0.6, dampingFraction: 0.85), value: fraction)
    }
}

/// One looping illustration per step, drawn with SF Symbols and shapes.
private struct Scene: View {
    var step: SetupView.Step
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var on = false

    var body: some View {
        ZStack {
            Circle().fill(Theme.sunken)
            Circle().strokeBorder(Theme.coral.opacity(on && step != .ready ? 0.35 : 0.15), lineWidth: 2)
                .scaleEffect(on && !reduceMotion ? 1.06 : 1)
            content.clipShape(Circle())
        }
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
                RoundedRectangle(cornerRadius: 2).fill(Theme.coral).frame(width: 9, height: 12)
                RoundedRectangle(cornerRadius: 3).fill(Theme.inkSoft).frame(width: 15, height: 18)
                Rectangle().fill(Theme.inkSoft).frame(width: 4, height: 40)
            }
            .offset(y: on ? 46 : 64)
            Image(systemName: "bolt.fill").font(.system(size: 14)).foregroundStyle(Theme.amber)
                .offset(x: 22, y: 20).opacity(on ? 1 : 0).scaleEffect(on ? 1 : 0.4)
        }
    }

    /// The Trust prompt appears and a finger taps it.
    private var trust: some View {
        ZStack {
            phone
            Text("Trust").font(.system(size: 9, weight: .bold)).foregroundStyle(Theme.onAccent)
                .padding(.horizontal, 6).padding(.vertical, 3)
                .background(on ? Theme.mint : Theme.coral, in: Capsule())
            Image(systemName: "hand.point.up.left.fill").font(.system(size: 26)).foregroundStyle(Theme.ink)
                .offset(x: on ? 14 : 34, y: on ? 18 : 40).scaleEffect(on ? 0.92 : 1)
        }
    }

    /// The Developer Mode switch flips on.
    private var developer: some View {
        VStack(spacing: 12) {
            Image(systemName: "hammer.fill").font(.system(size: 30)).foregroundStyle(Theme.ink)
                .rotationEffect(.degrees(on ? -18 : 8), anchor: .bottomTrailing)
            ZStack(alignment: on ? .trailing : .leading) {
                Capsule().fill(on ? Theme.mint : Theme.inkSoft.opacity(0.4)).frame(width: 52, height: 30)
                Circle().fill(.white).frame(width: 26, height: 26).padding(2).shadow(radius: 1)
            }
        }
    }

    /// A seal with rings bursting outwards.
    private var ready: some View {
        ZStack {
            ForEach(0..<2) { ring in
                Circle().strokeBorder(Theme.mint.opacity(on ? 0 : 0.6), lineWidth: 3)
                    .scaleEffect(on ? 1.0 + Double(ring) * 0.15 : 0.5)
                    .frame(width: 110, height: 110)
            }
            Image(systemName: "checkmark.seal.fill").font(.system(size: 60)).foregroundStyle(Theme.mint)
                .scaleEffect(on ? 1.05 : 0.95)
        }
    }
}
