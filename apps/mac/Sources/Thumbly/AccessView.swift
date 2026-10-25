import SwiftUI

struct AccessView: View {
    @Environment(AppModel.self) private var model
    @State private var removing: AIClient?

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            PageHeader(title: "Access", subtitle: "Decide which AI clients may see and use your iPhone.")

            sectionTitle("Waiting for you", count: model.consents.count)
            if model.consents.isEmpty {
                Card { EmptyNote(icon: "tray", title: "No requests", detail: "When an AI client asks to connect, it shows up here.") }
            } else {
                ForEach(model.consents) { RequestCard(consent: $0) }
            }

            sectionTitle("Allowed", count: model.approvedClients.count)
            Card(padding: 6) {
                if model.approvedClients.isEmpty {
                    EmptyNote(icon: "person.2.slash", title: "Nobody yet", detail: "Approved AI clients will be listed here.")
                } else {
                    VStack(spacing: 0) {
                        ForEach(Array(model.approvedClients.enumerated()), id: \.element.id) { index, client in
                            if index > 0 { Divider().overlay(Theme.hairline).padding(.horizontal, 14) }
                            ClientRow(client: client) { removing = client }
                        }
                    }
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

    private func sectionTitle(_ title: String, count: Int) -> some View {
        HStack(spacing: 8) {
            Text(title).font(Theme.display(17, .semibold))
            Text("\(count)").font(.system(size: 11.5, weight: .semibold)).foregroundStyle(Theme.inkSoft)
                .padding(.horizontal, 7).padding(.vertical, 1).background(Theme.sunken, in: Capsule())
        }
    }
}

private struct RequestCard: View {
    @Environment(AppModel.self) private var model
    var consent: Consent
    @State private var glow = false

    var body: some View {
        let highlighted = model.highlightedHandle == consent.handle
        Card(padding: 22, stroke: highlighted ? Theme.coral : nil) {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top, spacing: 14) {
                    Image(systemName: "sparkles").font(.system(size: 20)).foregroundStyle(Theme.onAccent)
                        .frame(width: 44, height: 44).background(Theme.amber, in: RoundedRectangle(cornerRadius: 12))
                    VStack(alignment: .leading, spacing: 4) {
                        Text(consent.client_name ?? "Unnamed client").font(Theme.display(19, .semibold))
                        Text("wants to use your iPhone").font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
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
                    Button("30 days") { decide(true, "30d") }.buttonStyle(.quiet)
                    Button("Until I remove it") { decide(true, "never") }.buttonStyle(.quiet)
                    Spacer()
                    Button("Deny") { decide(false, "24h") }.buttonStyle(.plain)
                        .foregroundStyle(Theme.danger).font(.system(size: 13, weight: .semibold))
                }
            }
        }
        .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(Theme.surface)
            .shadow(color: highlighted ? Theme.coral.opacity(glow ? 0.35 : 0.1) : .clear, radius: 16))
        .onAppear { withAnimation(.easeInOut(duration: 1.2).repeatForever()) { glow = true } }
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
                .font(Theme.display(16)).foregroundStyle(Theme.onAccent)
                .frame(width: 38, height: 38).background(Theme.mint, in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(client.client_name ?? client.client_id).font(.system(size: 14, weight: .semibold))
                    if client.isSelf == true { Chip(text: "this app") }
                }
                Text(detail).font(.system(size: 12)).foregroundStyle(Theme.inkSoft)
            }
            Spacer()
            Button("Remove", action: onRemove).buttonStyle(.quiet).disabled(client.isSelf == true)
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
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
