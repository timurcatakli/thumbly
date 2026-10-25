import SwiftUI

/// Shown when the background service doesn't answer.
struct OfflineView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 18) {
            ZStack(alignment: .topTrailing) {
                BrandMark(size: 96).rotationEffect(.degrees(-8)).saturation(0.4)
                Text("z z").font(Theme.display(22)).foregroundStyle(Theme.inkSoft).offset(x: 30, y: -24)
            }
            .padding(.bottom, 6)
            Text("Thumbly isn't running").font(Theme.display(30))
            Text("The Thumbly helper that talks to your iPhone is asleep. Start it and this window will light up.")
                .font(.system(size: 14)).foregroundStyle(Theme.inkSoft).multilineTextAlignment(.center)
                .frame(maxWidth: 380)
            HStack(spacing: 10) {
                Button { Task { await model.startAgent() } } label: {
                    Label(model.starting ? "Starting…" : "Start Thumbly", systemImage: "power")
                }
                .buttonStyle(.primary).disabled(model.starting)
                Button("Try again") { Task { await model.refresh() } }.buttonStyle(.quiet)
            }
            if let output = model.startOutput {
                ScrollView {
                    Text(output).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxWidth: 520, maxHeight: 160)
                .padding(12)
                .background(Theme.sunken, in: RoundedRectangle(cornerRadius: 12))
            }
            Text("API: \(model.api.base.absoluteString)").font(.system(size: 11)).foregroundStyle(Theme.inkSoft)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
