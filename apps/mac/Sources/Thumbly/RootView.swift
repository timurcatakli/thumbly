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
        content.padding(32).frame(maxWidth: 760).frame(maxWidth: .infinity)
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
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 10) {
                BrandMark(size: 34)
                VStack(alignment: .leading, spacing: 0) {
                    Text("Thumbly").font(Theme.display(20))
                    Text("your thumb, for AI").font(.system(size: 10.5)).foregroundStyle(Theme.inkSoft)
                }
            }
            .padding(.top, 26).padding(.bottom, 20).padding(.horizontal, 6)

            ForEach(Section.allCases) { section in
                NavItem(section: section, selected: model.section == section,
                        badge: section == .access ? model.pendingCount : 0,
                        setupDot: section == .status && model.needsSetup) {
                    model.section = section
                    Task { await model.refresh() }
                }
            }
            Spacer()
            HStack(spacing: 8) {
                StatusDot(color: model.health.color, size: 8)
                Text(model.headline).font(.system(size: 11.5, weight: .medium)).foregroundStyle(Theme.inkSoft)
                    .lineLimit(2)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface.opacity(0.7), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .padding(14)
        .frame(width: 214)
        .frame(maxHeight: .infinity)
        .background(Theme.sunken)
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
                    Text("\(badge)").font(.system(size: 11, weight: .bold)).foregroundStyle(Theme.onAccent)
                        .padding(.horizontal, 7).padding(.vertical, 2).background(Theme.coral, in: Capsule())
                } else if setupDot {
                    StatusDot(color: Theme.amber, size: 7)
                }
            }
            .font(.system(size: 13.5, weight: selected ? .semibold : .regular))
            .foregroundStyle(selected ? Theme.ink : Theme.inkSoft)
            .padding(.horizontal, 12).padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 11, style: .continuous)
                    .fill(selected ? Theme.surface : (hover ? Theme.surface.opacity(0.5) : .clear)))
            .overlay(alignment: .leading) {
                if selected { Capsule().fill(Theme.coral).frame(width: 3, height: 18).offset(x: 1) }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

private struct Toast: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        if let toast = model.toast {
            Label(toast, systemImage: "checkmark.circle.fill")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Theme.ink)
                .padding(.horizontal, 16).padding(.vertical, 10)
                .background(Theme.surface, in: Capsule())
                .overlay(Capsule().strokeBorder(Theme.mint, lineWidth: 1.5))
                .shadow(color: .black.opacity(0.12), radius: 12, y: 4)
                .padding(.bottom, 22)
                .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }
}
