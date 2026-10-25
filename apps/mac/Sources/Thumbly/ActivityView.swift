import AppKit
import SwiftUI

struct ActivityView: View {
    @Environment(AppModel.self) private var model
    @State private var expanded: String?
    @State private var confirmClear = false

    var body: some View {
        @Bindable var model = model
        VStack(alignment: .leading, spacing: 18) {
            PageHeader(title: "Activity", subtitle: "Everything AI clients did on your iPhone, newest first.")
            HStack(spacing: 14) {
                Chip(text: countText, icon: "list.bullet")
                Toggle("Show control events", isOn: $model.showControlEvents)
                    .toggleStyle(RuleToggleStyle()).font(Theme.text(13))
                    .help("Also list each time an AI client took or handed back control.")
                Spacer()
                Button("Clear…") { confirmClear = true }.buttonStyle(.secondary)
                    .disabled(model.operations.isEmpty)
            }
            VStack(alignment: .leading, spacing: 0) {
                if model.operations.isEmpty {
                    Rule()
                    EmptyNote(icon: "clock", title: "Nothing yet",
                              detail: "Taps, swipes, typing and opened apps will appear here.")
                } else {
                    TableHeader(columns: [("Action", nil), ("Status", 100), ("When", 96), ("", 10)], leading: 48)
                    LazyVStack(spacing: 0) {
                        ForEach(Array(model.operations.enumerated()), id: \.element.id) { index, op in
                            if index > 0 { Rule(color: Theme.divider, weight: Theme.hairline) }
                            OperationRow(op: op, expanded: expanded == op.id) {
                                withAnimation(.easeOut(duration: 0.15)) { expanded = expanded == op.id ? nil : op.id }
                            }
                        }
                    }
                    Rule(color: Theme.divider, weight: Theme.hairline)
                }
            }
            if model.nextCursor != nil {
                Button(model.loadingMore ? "Loading…" : "Show older") { Task { await model.loadMoreOperations() } }
                    .buttonStyle(.secondary)
            }
        }
        .task { await model.reloadOperations() }
        .confirmationDialog("Clear all activity?", isPresented: $confirmClear) {
            Button("Clear activity", role: .destructive) { Task { await model.clearActivity() } }
        } message: {
            Text("Removes every finished entry. Actions still running stay until they finish.")
        }
    }

    private var countText: String {
        guard let total = model.operationsTotal else { return "\(model.operations.count) shown" }
        return total == 1 ? "1 entry" : "\(total) entries"
    }
}

private struct OperationRow: View {
    var op: Operation
    var expanded: Bool
    var toggle: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: toggle) {
                HStack(spacing: 14) {
                    Image(systemName: icon).font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.ink)
                        .frame(width: 34, height: 34).background(Theme.surface)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title).font(Theme.text(14, .bold))
                        Text(subtitle).font(Theme.text(12.5)).foregroundStyle(Theme.inkSoft).lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    stateChip.frame(width: 100, alignment: .leading)
                    Text(Fmt.relative(op.created)).font(Theme.text(12.5).monospacedDigit()).foregroundStyle(Theme.inkSoft)
                        .frame(width: 96, alignment: .leading)
                    Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Theme.inkSoft).rotationEffect(.degrees(expanded ? 90 : 0))
                        .frame(width: 10)
                }
                .padding(.vertical, 10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if expanded { details.padding(.leading, 48).padding(.bottom, 14) }
        }
        .rowHover()
    }

    private var details: some View {
        Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 5) {
            row("Client", op.client ?? "Thumbly")
            if let created = op.created { row("Started", Fmt.time(created)) }
            if let created = op.created, let updated = op.updated, updated >= created {
                row("Took", updated - created < 1 ? "\(Int((updated - created) * 1000)) ms"
                    : String(format: "%.1f s", updated - created))
            }
            if let app = op.app { row("App", app) }
            if let url = op.url { row("Link", url) }
            if let code = op.error_code { row("Error", code) }
            GridRow {
                Text("ID").foregroundStyle(Theme.inkSoft)
                HStack(spacing: 6) {
                    Text(op.id).font(Theme.mono()).textSelection(.enabled)
                        .padding(.horizontal, 6).padding(.vertical, 2).background(Theme.surface)
                    Button("Copy") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(op.id, forType: .string)
                    }
                    .buttonStyle(RuleButtonStyle(role: .ghost, small: true))
                }
            }
            Text("Typed text and screenshots are never stored.").font(Theme.text(11.5)).foregroundStyle(Theme.inkSoft)
                .gridCellColumns(2).padding(.top, 2)
        }
        .font(Theme.text(12.5))
    }

    private func row(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label).foregroundStyle(Theme.inkSoft)
            Text(value).textSelection(.enabled)
        }
    }

    private var title: String {
        switch op.kind ?? "" {
        case "launch_app": "Opened an app"
        case "open_url", "open_link": "Opened a link"
        case "type_text": "Typed text"
        case "send_text": "Pasted text"
        case "long_press": "Long press"
        case "acquire": "Took control"
        case "release": "Handed back control"
        case "pause": "Paused AI control"
        case "resume": "Resumed AI control"
        case "autopair": "Paired the iPhone"
        case "unbind": "Forgot the iPhone"
        default: (op.kind ?? "action").replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    /// Who did it and, when known, on what.
    private var subtitle: String {
        let what = op.app.map(Self.appName) ?? op.url
        return [op.client ?? "Thumbly", what].compactMap { $0 }.joined(separator: " · ")
    }

    /// "com.apple.mobilenotes" → "Mobilenotes": readable enough without asking the phone.
    static func appName(_ bundleID: String) -> String {
        let known = ["com.apple.mobilenotes": "Notes", "com.apple.Preferences": "Settings",
                     "com.apple.mobilesafari": "Safari", "com.apple.MobileSMS": "Messages",
                     "net.whatsapp.WhatsApp": "WhatsApp", "com.burbn.instagram": "Instagram",
                     "com.google.chrome.ios": "Chrome"]
        return known[bundleID] ?? (bundleID.split(separator: ".").last.map { $0.prefix(1).uppercased() + $0.dropFirst() } ?? bundleID)
    }

    private var icon: String {
        switch op.kind ?? "" {
        case "tap": "hand.tap"
        case "long_press": "hand.point.up"
        case "swipe": "hand.draw"
        case "type_text", "send_text", "key", "shortcut": "keyboard"
        case "button": "button.horizontal.top.press"
        case "screenshot", "full_page": "camera.viewfinder"
        case "launch_app": "app.badge"
        case "open_url", "open_link": "link"
        case "acquire": "hand.raised"
        case "release": "hand.wave"
        case "pause": "pause.circle"
        case "resume": "play.circle"
        case "autopair": "cable.connector"
        case "unbind": "iphone.slash"
        default: "sparkle"
        }
    }

    @ViewBuilder private var stateChip: some View {
        let s = (op.state ?? "").uppercased()
        switch s {
        case "SUCCEEDED": Chip(text: "Done", icon: "checkmark", tint: Theme.ink)
        case "FAILED", "REJECTED", "OUTCOME_UNKNOWN": Chip(text: s == "OUTCOME_UNKNOWN" ? "Unknown" : "Failed", icon: "xmark", tint: Theme.accentText)
        case "CANCELLED", "CANCELED": Chip(text: "Cancelled", tint: Theme.inkSoft)
        case "QUEUED": Chip(text: "Queued", icon: "clock", tint: Theme.accentText)
        default: Chip(text: "Running", icon: "ellipsis", tint: Theme.accentText)
        }
    }
}
