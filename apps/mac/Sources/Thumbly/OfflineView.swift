import SwiftUI

/// Shown when the background service doesn't answer.
struct OfflineView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 10) {
                BrandMark(size: 72)
                Text("z z").font(Theme.display(22)).foregroundStyle(Theme.inkSoft)
            }
            .padding(.bottom, 6)
            Text("Thumbly isn't running").font(Theme.display(30)).displayTracking(30)
            Rule().frame(maxWidth: 520)
            Text("The Thumbly helper that talks to your iPhone is asleep. Start it and this window will light up.")
                .font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                .frame(maxWidth: 420, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                Button { Task { await model.startAgent() } } label: {
                    Label(model.starting ? "Starting…" : "Start Thumbly", systemImage: "power")
                }
                .buttonStyle(.primary).disabled(model.starting)
                Button("Try again") { Task { await model.refresh() } }.buttonStyle(.secondary)
            }
            if let output = model.startOutput {
                ScrollView {
                    Text(output).font(Theme.mono()).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxWidth: 520, maxHeight: 160)
                .padding(12)
                .background(Theme.surface)
            }
            Text("API: \(model.api.base.absoluteString)").font(Theme.text(12)).foregroundStyle(Theme.inkSoft)
        }
        .padding(48)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}
