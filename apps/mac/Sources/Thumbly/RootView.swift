import SwiftUI

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Group {
            switch model.reachable {
            case nil: ProgressView().controlSize(.large).frame(maxWidth: .infinity, maxHeight: .infinity)
            case false?: OfflineView()
            case true?:
                HStack(spacing: 0) {
                    Sidebar()
                    if Snapshot.active {
                        // cacheDisplay can't see inside NSScrollView, so snapshots lay out flat.
                        page.frame(maxHeight: .infinity, alignment: .top)
                    } else {
                        ScrollView { page }.scrollContentBackground(.hidden)
                    }
                }
            }
        }
        .background(Theme.canvas)
        .foregroundStyle(Theme.ink)
        .overlay(alignment: .bottom) { Toast() }
        .alert("Something went wrong", isPresented: .init(
            get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.errorMessage ?? "")
        }
        .onAppear { model.windowVisible = true; Task { await model.refresh() } }
        .onDisappear { model.windowVisible = false }
    }

    private var page: some View {
        content.padding(32).frame(maxWidth: 760, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private var content: some View {
        switch model.section {
        case .status: if model.needsSetup { SetupView() } else { StatusView() }
        case .access: AccessView()
        case .activity: ActivityView()
        case .settings: SettingsView()
        }
    }
}

private struct Sidebar: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                BrandMark(size: 34)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Thumbly").font(Theme.display(20)).displayTracking(20)
                    Text("your thumb, for AI").font(Theme.text(11)).foregroundStyle(Theme.inkSoft)
                }
            }
            .padding(.horizontal, 20).padding(.top, 26).padding(.bottom, 20)

            Rule(color: Theme.divider)
            ForEach(Section.allCases) { section in
                NavItem(section: section, selected: model.section == section,
                        badge: section == .access ? model.pendingCount : 0,
                        setupDot: section == .status && model.needsSetup) {
                    model.section = section
                    Task { await model.refresh() }
                }
            }
            Spacer()
            Rule(color: Theme.divider)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                StatusDot(color: model.health.color, size: 8, blink: model.health == .busy)
                Text(model.headline).font(Theme.text(12, .medium)).foregroundStyle(Theme.ink)
                    .lineLimit(2)
            }
            .padding(.horizontal, 20).padding(.vertical, 14)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(width: 214)
        .frame(maxHeight: .infinity)
        .background(Theme.sunken)
        // Over the nav rows, and up under the title bar like the fill.
        .overlay(alignment: .trailing) { VRule().ignoresSafeArea() }
        .foregroundStyle(Theme.ink)
    }
}

private struct NavItem: View {
    var section: Section
    var selected: Bool
    var badge: Int
    var setupDot: Bool
    var action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: section.icon).frame(width: 20)
                Text(section == .status && setupDot ? "Set up" : section.title)
                Spacer()
                if badge > 0 {
                    CountBadge(count: badge)
                } else if setupDot {
                    StatusDot(color: Theme.accent, size: 7)
                }
            }
            .font(Theme.text(13.5, selected ? .heavy : .regular))
            .foregroundStyle(selected ? Theme.ink : Theme.inkSoft)
            .padding(.horizontal, 20).padding(.vertical, 10)
            .background(selected ? Theme.surface : (hover ? Theme.hover : .clear))
            .overlay(alignment: .leading) {
                if selected { Rectangle().fill(Theme.accent).frame(width: 3, height: 18) }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

private struct Toast: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        if let toast = model.toast {
            HStack(spacing: 10) {
                Rectangle().fill(Theme.accent).frame(width: 8, height: 8)
                Text(toast)
            }
            .font(Theme.text(13, .semibold))
            .foregroundStyle(Theme.ink)
            .padding(.horizontal, 16).padding(.vertical, 10)
            .background(Theme.canvas)
            .overlay(Rectangle().strokeBorder(Theme.ink, lineWidth: Theme.rule))
            .padding(.bottom, 22)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }
}
