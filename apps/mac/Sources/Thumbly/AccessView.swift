import SwiftUI

struct AccessView: View {
    @Environment(AppModel.self) private var model
    @State private var removing: AIClient?

    var body: some View {
        VStack(alignment: .leading, spacing: 28) {
            PageHeader(title: "Access", subtitle: "Decide which AI clients may see and use your iPhone.")

            VStack(alignment: .leading, spacing: 14) {
                SectionHeading(title: "Waiting for you", count: model.consents.count)
                if model.consents.isEmpty {
                    EmptyNote(icon: "tray", title: "No requests", detail: "When an AI client asks to connect, it shows up here.")
                } else {
                    ForEach(model.consents) { RequestCard(consent: $0) }
                }
            }

            VStack(alignment: .leading, spacing: 0) {
                SectionHeading(title: "Allowed", count: model.approvedClients.count, ruled: model.approvedClients.isEmpty)
                if model.approvedClients.isEmpty {
                    EmptyNote(icon: "person.2.slash", title: "Nobody yet", detail: "Approved AI clients will be listed here.")
                } else {
                    TableHeader(columns: [("Client", nil), ("", 80)], leading: 52)
                    ForEach(Array(model.approvedClients.enumerated()), id: \.element.id) { index, client in
                        if index > 0 { Rule(color: Theme.divider, weight: Theme.hairline) }
                        ClientRow(client: client) { removing = client }
                    }
                    Rule(color: Theme.divider, weight: Theme.hairline)
                }
            }
        }
        .confirmationDialog("Remove \(removing?.client_name ?? "this client")?", isPresented: .init(
            get: { removing != nil }, set: { if !$0 { removing = nil } }), presenting: removing
        ) { client in
            Button("Remove", role: .destructive) { Task { await model.revoke(client) } }
        } message: { _ in
            Text("It loses access right away and must ask again.")
        }
    }
}

private struct RequestCard: View {
    @Environment(AppModel.self) private var model
    var consent: Consent

    var body: some View {
        let highlighted = model.highlightedHandle == consent.handle
        Card(padding: 22, stroke: highlighted ? Theme.accent : Theme.ink) {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top, spacing: 14) {
                    Image(systemName: "sparkles").font(.system(size: 20)).foregroundStyle(Theme.onAccent)
                        .frame(width: 44, height: 44).background(Theme.accent)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(consent.client_name ?? "Unnamed client").font(Theme.display(20)).displayTracking(20)
                        Text("wants to use your iPhone").font(Theme.text(14)).foregroundStyle(Theme.inkSoft)
                    }
                    Spacer()
                    if let e = consent.expires_in { Chip(text: "expires in \(Fmt.duration(e))", icon: "timer") }
                }
                HStack(spacing: 6) {
                    ForEach(consent.scopes ?? [], id: \.self) { Chip(text: Fmt.scope($0), icon: "checkmark") }
                    ForEach(consent.redirect_hosts ?? [], id: \.self) { Chip(text: $0, icon: "network") }
                }
                HStack(spacing: 8) {
                    Button("Allow for 24 hours") { decide(true, "24h") }.buttonStyle(.primary)
                    Button("30 days") { decide(true, "30d") }.buttonStyle(.secondary)
                    Button("Until I remove it") { decide(true, "never") }.buttonStyle(.secondary)
                    Spacer()
                    Button("Deny") { decide(false, "24h") }.buttonStyle(.ghost)
                }
            }
        }
    }

    private func decide(_ allow: Bool, _ window: String) {
        Task { await model.decide(consent, allow: allow, window: window) }
    }
}

private struct ClientRow: View {
    var client: AIClient
    var onRemove: () -> Void

    var body: some View {
        HStack(spacing: 14) {
            Text(String((client.client_name ?? "?").prefix(1)).uppercased())
                .font(Theme.display(16)).foregroundStyle(Theme.ink)
                .frame(width: 38, height: 38)
                .overlay(Rectangle().strokeBorder(Theme.ink, lineWidth: Theme.rule))
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(client.client_name ?? client.client_id).font(Theme.text(14, .bold))
                    if client.isSelf == true { Chip(text: "this app") }
                }
                Text(detail).font(Theme.text(12.5)).foregroundStyle(Theme.inkSoft)
            }
            Spacer()
            Button("Remove", action: onRemove).buttonStyle(.secondary).disabled(client.isSelf == true)
                .frame(width: 80, alignment: .leading)
        }
        .padding(.vertical, 12)
        .rowHover()
    }

    private var detail: String {
        var parts = [(client.scopes ?? []).map(Fmt.scope).joined(separator: ", ")]
        if client.never_expires == true {
            parts.append("until you remove it")
        } else if let until = client.active_until?["refresh"]?.number ?? client.active_until?.number {
            parts.append("until \(Fmt.date(until))" + (client.renewed_while_used == true ? ", renews while used" : ""))
        }
        if let used = client.last_used { parts.append("used \(Fmt.relative(used))") }
        return parts.filter { !$0.isEmpty }.joined(separator: " · ")
    }
}
