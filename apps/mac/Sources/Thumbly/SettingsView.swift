import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmUnbind = false
    @State private var showReset = false
    @AppStorage("connectClient") private var clientID = Client.claudeCode.rawValue

    var body: some View {
        let info = model.info
        VStack(alignment: .leading, spacing: 28) {
            PageHeader(title: "Settings", subtitle: "Connect AI clients, switch phones, and keep things tidy.")

            Card {
                VStack(alignment: .leading, spacing: 14) {
                    row("Connect an AI client", "Pick yours, run or paste this once, then ask it to use your iPhone.")
                    // Negative spacing overlaps the 1px frames so neighbouring tiles share one border.
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: -Theme.hairline)],
                              spacing: -Theme.hairline) {
                        ForEach(Client.allCases) { option in
                            ClientTile(client: option, selected: option == client) { clientID = option.rawValue }
                                .zIndex(option == client ? 1 : 0)
                        }
                    }
                    Text(client.how).font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)
                    if let setup = client.setup(local: mcpURL, remote: info?.public_url.map { "\($0)/mcp" }) {
                        HStack(alignment: .center) {
                            Text(setup).font(Theme.mono()).textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer()
                            Button(client.isCommand ? "Copy command" : "Copy") { copy(setup) }.buttonStyle(.primary)
                        }
                        .padding(14)
                        .background(Theme.surface)
                    } else {
                        Chip(text: "Needs remote access: set public_url in Thumbly's settings file", icon: "globe",
                             tint: Theme.accentText)
                    }
                    HStack(spacing: 6) {
                        Chip(text: "MCP \(info?.mcp_url ?? "—")", icon: "point.3.connected.trianglepath.dotted")
                        Chip(text: "Version \(info?.version ?? "—")", icon: "tag")
                        Button("Copy URL") { copy(info?.mcp_url ?? "") }.buttonStyle(RuleButtonStyle(role: .ghost, small: true))
                            .padding(.leading, 4)
                    }
                }
            }

            Card {
                HStack {
                    row("Use another iPhone", "Forget this iPhone so you can plug in a different one.")
                    Spacer()
                    Button("Forget iPhone…") { confirmUnbind = true }.buttonStyle(.secondary)
                }
            }

            Card {
                HStack {
                    row("Diagnostics", "Save a report to share when something isn't working. Identifiers are redacted.")
                    Spacer()
                    Button("Export…") { Task { await exportDiagnostics() } }.buttonStyle(.secondary)
                }
            }

            Card(stroke: Theme.accent) {
                HStack {
                    row("Start over", "Erase the paired iPhone, every AI client and all activity.")
                    Spacer()
                    Button("Reset Thumbly…") { showReset = true }.buttonStyle(.danger)
                }
            }
        }
        .task { model.info = (try? await model.api.info()) ?? model.info }
        .confirmationDialog("Forget this iPhone?", isPresented: $confirmUnbind) {
            Button("Forget iPhone", role: .destructive) {
                Task { await model.perform("iPhone forgotten — plug in the new one") { try await model.api.unbind() } }
            }
        } message: {
            Text("AI clients keep their access, but nothing reaches a phone until you set one up again.")
        }
        .sheet(isPresented: $showReset) { ResetSheet() }
    }

    private var client: Client { Client(rawValue: clientID) ?? .claudeCode }
    private var mcpURL: String { model.info?.mcp_url ?? "http://127.0.0.1:8771/mcp" }

    private func row(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(Theme.display(18)).displayTracking(18)
            Text(detail).font(Theme.text(13)).foregroundStyle(Theme.inkSoft)
        }
    }

    private func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        model.flash("Copied")
    }

    private func exportDiagnostics() async {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.json]
        panel.nameFieldStringValue = "thumbly-diagnostics-\(Date.now.formatted(.iso8601.year().month().day())).json"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        await model.perform("Diagnostics saved") {
            try await model.api.diagnostics().write(to: url)
        }
    }
}

/// AI clients that speak MCP, with the one-time setup each needs. Clients on this Mac use the local
/// entry; cloud ones (ChatGPT) can only reach the remote entry behind `public_url`.
private enum Client: String, CaseIterable, Identifiable {
    case claudeCode, claudeDesktop, codex, gemini, cursor, vscode, windsurf, chatgpt
    var id: String { rawValue }

    var title: String {
        switch self {
        case .claudeCode: "Claude Code"
        case .claudeDesktop: "Claude Desktop"
        case .codex: "Codex"
        case .gemini: "Gemini CLI"
        case .cursor: "Cursor"
        case .vscode: "VS Code"
        case .windsurf: "Windsurf"
        case .chatgpt: "ChatGPT"
        }
    }

    /// The client's logo from Resources/ClientIcons (lobe-icons, MIT). Single-colour marks are
    /// templates, tinted with the text colour so they work in light and dark mode.
    var logo: NSImage? {
        guard let url = Bundle.main.url(forResource: rawValue, withExtension: "svg", subdirectory: "ClientIcons"),
              let image = NSImage(contentsOf: url) else { return nil }
        image.isTemplate = [.cursor, .vscode, .windsurf, .chatgpt].contains(self)
        return image
    }

    var isCommand: Bool { [.claudeCode, .codex, .gemini, .vscode].contains(self) }

    var how: String {
        switch self {
        case .claudeCode: "Run this once in your terminal, then ask Claude to use your iPhone."
        case .claudeDesktop: "Settings › Developer › Edit Config, add this to claude_desktop_config.json, then restart Claude. Needs Node.js."
        case .codex: "Run this once in your terminal; Codex opens the browser to sign in."
        case .gemini: "Run this once in your terminal, then start gemini and run /mcp auth thumbly."
        case .cursor: "Add this to ~/.cursor/mcp.json (Cursor › Settings › MCP › Add), then sign in from that list."
        case .vscode: "Run this once in your terminal; Copilot Chat in agent mode then lists Thumbly's tools."
        case .windsurf: "Add this to ~/.codeium/windsurf/mcp_config.json, then refresh MCP servers in Cascade."
        case .chatgpt: "Settings › Apps & Connectors › Advanced: turn on Developer mode, then Create a connector with this URL. ChatGPT runs in the cloud, so it needs Thumbly's remote entry."
        }
    }

    /// The text to paste, or nil when the client needs the remote entry and none is set up.
    func setup(local: String, remote: String?) -> String? {
        switch self {
        case .claudeCode: return "claude mcp add --transport http thumbly \(local)"
        case .claudeDesktop: return json(["command": "\"npx\"", "args": "[\"-y\", \"mcp-remote\", \"\(local)\"]"])
        case .codex: return "codex mcp add thumbly --url \(local)"
        case .gemini: return "gemini mcp add --transport http thumbly \(local)"
        case .cursor: return json(["url": "\"\(local)\""])
        case .vscode: return "code --add-mcp '{\"name\":\"thumbly\",\"type\":\"http\",\"url\":\"\(local)\"}'"
        case .windsurf: return json(["serverUrl": "\"\(local)\""])
        case .chatgpt: return remote
        }
    }

    private func json(_ fields: KeyValuePairs<String, String>) -> String {
        let body = fields.map { "      \"\($0.key)\": \($0.value)" }.joined(separator: ",\n")
        return "{\n  \"mcpServers\": {\n    \"thumbly\": {\n\(body)\n    }\n  }\n}"
    }
}

private struct ClientTile: View {
    var client: Client
    var selected: Bool
    var action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                Group {
                    if let logo = client.logo {
                        Image(nsImage: logo).resizable().scaledToFit()
                    } else {
                        Image(systemName: "sparkles").resizable().scaledToFit()
                    }
                }
                .frame(width: 20, height: 20)
                .foregroundStyle(Theme.ink)
                Text(client.title).font(Theme.text(13, selected ? .heavy : .semibold)).foregroundStyle(Theme.ink)
                    .minimumScaleFactor(0.8)
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 12).padding(.vertical, 11)
            .background(selected ? Theme.surface : (hover ? Theme.hover : .clear))
            .overlay(Rectangle().strokeBorder(Theme.divider, lineWidth: Theme.hairline))
            .overlay { if selected { Rectangle().strokeBorder(Theme.accent, lineWidth: Theme.rule) } }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

private struct ResetSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var typed = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 28)).foregroundStyle(Theme.accent)
            Text("Reset Thumbly?").font(Theme.display(22)).displayTracking(22)
            Rule()
            Text("This erases the paired iPhone, all AI clients and the activity log, then restarts Thumbly. Type **Thumbly** to confirm.")
                .font(Theme.text(14)).foregroundStyle(Theme.inkSoft).fixedSize(horizontal: false, vertical: true)
            TextField("Thumbly", text: $typed).textFieldStyle(.plain).font(Theme.text(14))
                .padding(.horizontal, 10).padding(.vertical, 8)
                .background(Theme.surface)
                .overlay(Rectangle().strokeBorder(Theme.divider, lineWidth: Theme.hairline))
            HStack {
                Button("Cancel") { dismiss() }.buttonStyle(.secondary).keyboardShortcut(.cancelAction)
                Button("Erase everything") {
                    let name = typed
                    dismiss()
                    Task { await model.perform("Thumbly is restarting…") { try await model.api.reset(name: name) } }
                }
                .buttonStyle(.danger)
                .disabled(typed.trimmingCharacters(in: .whitespaces).caseInsensitiveCompare("Thumbly") != .orderedSame)
            }
        }
        .padding(26)
        .frame(width: 400)
        .background(Theme.canvas)
    }
}
