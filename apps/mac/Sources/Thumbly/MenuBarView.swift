import SwiftUI

struct MenuBarView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                BrandMark(size: 28)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Thumbly").font(Theme.display(15))
                    HStack(spacing: 6) {
                        StatusDot(color: model.health.color, size: 7, blink: model.health == .busy)
                        Text(model.headline).font(Theme.text(12)).foregroundStyle(Theme.inkSoft)
                    }
                }
            }

            if model.pendingCount > 0 {
                Button {
                    model.handle(url: URL(string: "thumbly://access" + (model.consents.first.map { "?r=\($0.handle)" } ?? ""))!)
                } label: {
                    HStack {
                        Image(systemName: "bell.badge.fill").foregroundStyle(Theme.accentText)
                        Text(model.pendingCount == 1 ? "1 request waiting" : "\(model.pendingCount) requests waiting")
                        Spacer()
                        Image(systemName: "chevron.right").foregroundStyle(Theme.inkSoft)
                    }
                    .font(Theme.text(12.5, .semibold))
                    .padding(10)
                    .background(Theme.surface)
                    .overlay(alignment: .leading) { Rectangle().fill(Theme.accent).frame(width: 3) }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }

            if model.isPaused {
                Button { Task { await model.resume() } } label: {
                    Label("Resume AI control", systemImage: "play.fill")
                }
                .buttonStyle(RuleButtonStyle(role: .primary, full: true))
                .disabled(model.reachable != true)
            } else {
                Button { Task { await model.pause() } } label: {
                    Label("Pause AI control", systemImage: "hand.raised.fill")
                }
                .buttonStyle(RuleButtonStyle(role: .danger, full: true))
                .disabled(model.reachable != true)
            }

            Rule(color: Theme.divider)
            HStack {
                Button("Open Thumbly") { model.showWindow() }.buttonStyle(.secondary)
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }.buttonStyle(.plain).foregroundStyle(Theme.inkSoft)
                    .font(Theme.text(13))
                    .keyboardShortcut("q")
            }
        }
        .padding(14)
        .frame(width: 270)
        .foregroundStyle(Theme.ink)
        .background(Theme.canvas)
    }
}
