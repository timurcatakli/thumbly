import AppKit
import SwiftUI

/// Dev aid: `THUMBLY_SNAPSHOT=<dir>` renders every section and the menu bar panel in light and dark
/// mode to PNGs and quits.
/// Works without Screen Recording permission because it draws the window's own view hierarchy.
enum Snapshot {
    static let active = ProcessInfo.processInfo.environment["THUMBLY_SNAPSHOT"] != nil

    @MainActor static func runIfRequested(model: AppModel) {
        guard let dir = ProcessInfo.processInfo.environment["THUMBLY_SNAPSHOT"] else { return }
        let out = URL(fileURLWithPath: dir)
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(3))
            let extra = ProcessInfo.processInfo.environment["THUMBLY_SNAPSHOT_PREFIX"] ?? ""
            for (name, appearance) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
                NSApp.appearance = NSAppearance(named: appearance)
                for section in Section.allCases {
                    model.section = section
                    await model.refresh()
                    try? await Task.sleep(for: .seconds(1))
                    guard let view = NSApp.windows.first(where: { $0.identifier?.rawValue.hasPrefix("main") ?? false })?.contentView
                    else { continue }
                    guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { continue }
                    view.cacheDisplay(in: view.bounds, to: rep)
                    try? rep.representation(using: .png, properties: [:])?
                        .write(to: out.appendingPathComponent("\(extra)\(section.rawValue)-\(name).png"))
                }
                try? menuBar(model: model, appearance: appearance)?
                    .write(to: out.appendingPathComponent("\(extra)menubar-\(name).png"))
            }
            NSApp.terminate(nil)
        }
    }

    /// The menu bar panel can't be opened from code, so draw MenuBarView in an offscreen window.
    @MainActor private static func menuBar(model: AppModel, appearance: NSAppearance.Name) -> Data? {
        let host = NSHostingView(rootView: MenuBarView().environment(model))
        host.frame.size = host.fittingSize
        let window = NSWindow(contentRect: host.frame, styleMask: .borderless, backing: .buffered, defer: false)
        window.appearance = NSAppearance(named: appearance)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return nil }
        host.cacheDisplay(in: host.bounds, to: rep)
        return rep.representation(using: .png, properties: [:])
    }
}

/// Dev aid: `THUMBLY_SELFTEST=1 THUMBLY_API_URL=http://127.0.0.1:8799` drives every write through
/// APIClient against scripts/stub_server.py and exits. Refuses to run against the real service port.
enum SelfTest {
    @MainActor static func runIfRequested() {
        guard ProcessInfo.processInfo.environment["THUMBLY_SELFTEST"] != nil else { return }
        let api = APIClient.shared
        guard api.base.port != 8770 else { print("SELFTEST refused: points at the live service"); exit(2) }
        Task { @MainActor in
            var failures = 0
            func check(_ name: String, _ body: () async throws -> Void) async {
                do { try await body(); print("PASS \(name)") } catch { failures += 1; print("FAIL \(name): \(error)") }
            }
            await check("read all") {
                _ = try await (api.status(), api.overview(), api.info(), api.clients(), api.operations(limit: 5))
            }
            await check("decide allow 30d") {
                guard let c = try await api.consents().first else { throw APIError(code: "NO_CONSENT", message: "") }
                try await api.decide(handle: c.handle, allow: true, window: "30d")
            }
            await check("expired consent surfaces server message") {
                do { try await api.decide(handle: "gone", allow: false, window: "24h"); throw APIError(code: "NO_ERROR", message: "") }
                catch let e as APIError where e.code == "CONSENT_EXPIRED" {}
            }
            await check("revoke") { try await api.revoke(clientID: "c-1") }
            await check("activity hides control events and counts") {
                let actions = try await api.operations(actionsOnly: true)
                let all = try await api.operations(actionsOnly: false)
                guard let a = actions.total, let b = all.total, a < b,
                      actions.operations?.contains(where: { $0.kind == "acquire" }) == false
                else { throw APIError(code: "BAD_FILTER", message: "") }
            }
            await check("pause") {
                try await api.pause()
                guard try await api.status().control?.paused == true else { throw APIError(code: "NOT_PAUSED", message: "") }
            }
            await check("resume") {
                try await api.resume()
                guard try await api.status().control?.paused == false else { throw APIError(code: "STILL_PAUSED", message: "") }
            }
            await check("clear activity keeps running work") {
                try await api.clearOperations()
                let left = try await api.operations(actionsOnly: false).operations ?? []
                guard left.allSatisfy({ $0.state == "QUEUED" || $0.state == "DISPATCHED" }) else {
                    throw APIError(code: "NOT_CLEARED", message: "")
                }
            }
            await check("developer mode") {
                let udid = try await api.discover().usb?.first?.udid ?? ""
                try await api.showDeveloperModeSwitch(udid: udid)
            }
            await check("diagnostics") { _ = try JSONSerialization.jsonObject(with: try await api.diagnostics()) }
            await check("reset rejects wrong name") {
                do { try await api.reset(name: "nope"); throw APIError(code: "NO_ERROR", message: "") }
                catch let e as APIError where e.code == "RESET_NAME_MISMATCH" {}
            }
            await check("reset") { try await api.reset(name: "Thumbly") }
            await check("unbind") { try await api.unbind() }
            print(failures == 0 ? "SELFTEST OK" : "SELFTEST FAILED (\(failures))")
            exit(failures == 0 ? 0 : 1)
        }
    }
}
