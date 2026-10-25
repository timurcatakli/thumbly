import AppKit
import SwiftUI
import UserNotifications

@main
struct ThumblyApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel.shared

    var body: some Scene {
        Window("Thumbly", id: "main") {
            RootView()
                .environment(model)
                .frame(minWidth: 820, minHeight: 560)
        }
        .defaultSize(width: 960, height: 660)
        .windowStyle(.hiddenTitleBar)

        MenuBarExtra {
            MenuBarView().environment(model)
        } label: {
            MenuBarLabel().environment(model)
        }
        .menuBarExtraStyle(.window)
    }
}

/// The menu bar label is always alive, so it doubles as the place we grab `openWindow`.
private struct MenuBarLabel: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Image(systemName: model.isPaused ? "hand.raised.fill" : "hand.point.up.left.fill")
            .onAppear {
                model.openMainWindow = { openWindow(id: "main") }
                model.start()
            }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationWillFinishLaunching(_ notification: Notification) {
        Theme.registerFonts()
        // Handle thumbly:// ourselves so SwiftUI doesn't spawn extra windows for it.
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(handleURL(_:reply:)),
            forEventClass: AEEventClass(kInternetEventClass), andEventID: AEEventID(kAEGetURL))
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if Bundle.main.bundleIdentifier != nil {
            let center = UNUserNotificationCenter.current()
            center.delegate = self
            center.requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
        }
        Task { @MainActor in
            AppModel.shared.start()
            SelfTest.runIfRequested()
            Snapshot.runIfRequested(model: AppModel.shared)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows { Task { @MainActor in AppModel.shared.showWindow() } }
        return true
    }

    @objc func handleURL(_ event: NSAppleEventDescriptor, reply: NSAppleEventDescriptor) {
        guard let string = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue,
              let url = URL(string: string) else { return }
        Task { @MainActor in AppModel.shared.handle(url: url) }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let handle = response.notification.request.content.userInfo["handle"] as? String
        Task { @MainActor in
            var c = URLComponents(string: "thumbly://access")!
            if let handle { c.queryItems = [URLQueryItem(name: "r", value: handle)] }
            AppModel.shared.handle(url: c.url!)
        }
        completionHandler()
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}
