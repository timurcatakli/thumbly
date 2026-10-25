import AppKit
import Foundation
import Observation
import UserNotifications

enum Section: String, CaseIterable, Identifiable {
    case status, access, activity, settings
    var id: String { rawValue }
    var title: String { rawValue.capitalized }
    var icon: String {
        switch self {
        case .status: "iphone.gen3"
        case .access: "person.badge.key.fill"
        case .activity: "waveform.path.ecg"
        case .settings: "slider.horizontal.3"
        }
    }
}

/// Overall health, used for the status dot, menu bar and big card.
enum Health {
    case good, busy, attention, offline
}

@Observable @MainActor
final class AppModel {
    static let shared = AppModel()

    let api = APIClient.shared

    // Server state
    var reachable: Bool?            // nil until the first poll answers
    var status: Status?
    var overview: Overview?
    var info: Info?
    var consents: [Consent] = []
    var clients: [AIClient] = []
    var operations: [Operation] = []
    var nextCursor: String?
    var operationsTotal: Int?
    var loadingMore = false
    /// Show the take/hand-back control events around every action too.
    var showControlEvents = UserDefaults.standard.bool(forKey: "showControlEvents") {
        didSet {
            UserDefaults.standard.set(showControlEvents, forKey: "showControlEvents")
            Task { await reloadOperations() }
        }
    }

    // UI state
    var section: Section = .status
    var highlightedHandle: String?
    var windowVisible = false
    var toast: String?
    var errorMessage: String?
    var openMainWindow: (() -> Void)?
    var starting = false
    var startOutput: String?

    private var pollTask: Task<Void, Never>?
    private var autoStarted = false
    private var seenHandles: Set<String>?
    private var tick = 0

    // MARK: derived

    var connectionState: String { status?.connection?.state ?? "disconnected" }
    var pairingState: String { overview?.usb_pairing?.state ?? "off" }
    var pendingCount: Int { max(consents.count, status?.pending_consents ?? 0) }
    var approvedClients: [AIClient] { clients.filter { $0.approved ?? true } }
    var isPaused: Bool { status?.control?.paused ?? false }

    var needsSetup: Bool {
        guard reachable == true, let overview else { return false }
        if overview.bound == false { return true }
        return ["trust_required", "developer_mode_required", "ddi_required", "waiting_for_device"].contains(connectionState)
    }

    var health: Health {
        guard reachable == true else { return .offline }
        switch connectionState {
        case "connected": return isPaused ? .attention : .good
        case "connecting", "reconnecting", "switching", "mounting_ddi": return .busy
        default: return .attention
        }
    }

    var headline: String {
        guard reachable == true else { return "Thumbly isn't running" }
        switch connectionState {
        case "connected": return isPaused ? "AI control is paused" : "Your iPhone is connected"
        case "connecting": return "Connecting to your iPhone…"
        case "reconnecting": return "Reconnecting…"
        case "switching": return "Switching connection…"
        case "waiting_for_device": return "Waiting for your iPhone"
        case "trust_required": return "Tap Trust on your iPhone"
        case "developer_mode_required": return "Turn on Developer Mode"
        case "ddi_required", "mounting_ddi": return "Preparing developer tools…"
        case "phone_locked": return "Unlock your iPhone"
        case "wifi_pairing_required": return "Wi-Fi pairing needed"
        default: return "iPhone not connected"
        }
    }

    // MARK: polling

    func start() {
        guard pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh()
                let seconds = self.windowVisible ? 2.5 : 8
                try? await Task.sleep(for: .seconds(seconds))
            }
        }
    }

    func refresh() async {
        tick += 1
        do {
            async let s = api.status()
            async let o = api.overview()
            async let c = api.consents()
            let (status, overview, consents) = try await (s, o, c)
            self.status = status
            self.overview = overview
            self.consents = consents
            reachable = true
            notifyNewConsents(consents)
        } catch {
            if error is DecodingError { errorMessage = "Unexpected reply from Thumbly: \(error)" }
            reachable = false
            // Start the service (and with it the MCP entry) once per launch; a custom API URL means a stub.
            if !autoStarted, ProcessInfo.processInfo.environment["THUMBLY_API_URL"] == nil {
                autoStarted = true
                Task { await startAgent() }
            }
            return
        }
        // Slower-moving data: only while the window is up, or when it's missing.
        if windowVisible || info == nil {
            if info == nil || tick % 10 == 0 { info = try? await api.info() }
            if section == .access || section == .status || tick % 5 == 0 {
                if let c = try? await api.clients() { clients = c }
            }
            if section == .activity, operations.count <= 25 { await reloadOperations() }
        }
    }

    /// The newest page of activity, replacing what is shown.
    func reloadOperations() async {
        if let page = try? await api.operations(actionsOnly: !showControlEvents) {
            operations = page.operations ?? []
            nextCursor = page.has_more == false ? nil : page.next_cursor
            operationsTotal = page.total
        }
    }

    func loadMoreOperations() async {
        guard let cursor = nextCursor, !loadingMore else { return }
        loadingMore = true
        defer { loadingMore = false }
        if let page = try? await api.operations(cursor: cursor, actionsOnly: !showControlEvents) {
            let known = Set(operations.map(\.id))
            operations += (page.operations ?? []).filter { !known.contains($0.id) }
            nextCursor = page.has_more == false ? nil : page.next_cursor
        }
    }

    // MARK: actions

    /// Installs and starts the launchd agent with `thumbly agent install`, then waits for it to answer.
    func startAgent() async {
        guard !starting else { return }
        starting = true
        defer { starting = false }
        startOutput = nil
        let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/thumbly").path
        let result: (Int32, String) = await Task.detached {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: path)
            process.arguments = ["agent", "install"]
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            do {
                try process.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                return (process.terminationStatus, String(decoding: data, as: UTF8.self))
            } catch {
                return (-1, "Couldn't run \(path): \(error.localizedDescription)")
            }
        }.value
        if result.0 != 0 {
            startOutput = result.1.isEmpty ? "thumbly agent install exited with \(result.0)" : result.1
            return
        }
        for _ in 0..<10 {
            await refresh()
            if reachable == true { return }
            try? await Task.sleep(for: .seconds(1))
        }
        startOutput = result.1 + "\nThe agent was installed but isn't answering yet."
    }

    /// Runs a write, shows its error (if any) and refreshes.
    func perform(_ done: String? = nil, _ action: @escaping () async throws -> Void) async {
        do {
            try await action()
            if let done { flash(done) }
        } catch {
            errorMessage = error.localizedDescription
        }
        await refresh()
    }

    func pause() async { await perform("AI control paused") { try await self.api.pause() } }
    func resume() async { await perform("AI control resumed") { try await self.api.resume() } }

    func clearActivity() async {
        await perform("Activity cleared") { try await self.api.clearOperations() }
        await reloadOperations()
    }

    func decide(_ consent: Consent, allow: Bool, window: String = "24h") async {
        let name = consent.client_name ?? "Client"
        await perform(allow ? "\(name) can now use your iPhone" : "Request from \(name) denied") {
            try await self.api.decide(handle: consent.handle, allow: allow, window: window)
        }
        if highlightedHandle == consent.handle { highlightedHandle = nil }
        clients = (try? await api.clients()) ?? clients
    }

    func revoke(_ client: AIClient) async {
        await perform("\(client.client_name ?? "Client") removed") { try await self.api.revoke(clientID: client.client_id) }
        clients = (try? await api.clients()) ?? clients
    }

    func showDeveloperModeSwitch() async {
        await perform("Check your iPhone: Settings › Privacy & Security") {
            let found = try await self.api.discover()
            guard let udid = found.usb?.compactMap(\.udid).first else {
                throw APIError(code: "NO_PHONE", message: "Plug your iPhone in with a cable first.")
            }
            try await self.api.showDeveloperModeSwitch(udid: udid)
        }
    }

    func flash(_ message: String) {
        toast = message
        Task {
            try? await Task.sleep(for: .seconds(3))
            if toast == message { toast = nil }
        }
    }

    // MARK: deep links + notifications

    func handle(url: URL) {
        guard url.scheme == "thumbly" else { return }
        let host = url.host ?? url.pathComponents.dropFirst().first ?? ""
        if host == "access" {
            section = .access
            highlightedHandle = URLComponents(url: url, resolvingAgainstBaseURL: false)?
                .queryItems?.first { $0.name == "r" }?.value
        } else if let s = Section(rawValue: host) {
            section = s
        }
        showWindow()
        Task { await refresh() }
    }

    func showWindow() {
        NSApp.activate(ignoringOtherApps: true)
        if let window = NSApp.windows.first(where: { ($0.identifier?.rawValue.hasPrefix("main") ?? false) && $0.isVisible }) {
            window.makeKeyAndOrderFront(nil)
        } else {
            openMainWindow?()
        }
    }

    private func notifyNewConsents(_ consents: [Consent]) {
        let handles = Set(consents.map(\.handle))
        defer { seenHandles = (seenHandles ?? []).union(handles) }
        guard let seen = seenHandles, Bundle.main.bundleIdentifier != nil else { return }
        for consent in consents where !seen.contains(consent.handle) {
            let content = UNMutableNotificationContent()
            content.title = "\(consent.client_name ?? "An AI client") wants to use your iPhone"
            content.body = "Open Thumbly to allow or deny."
            content.sound = .default
            content.userInfo = ["handle": consent.handle]
            UNUserNotificationCenter.current().add(
                UNNotificationRequest(identifier: consent.handle, content: content, trigger: nil))
        }
    }
}
