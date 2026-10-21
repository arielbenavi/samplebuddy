import SwiftUI

@main
struct SampleBuddyApp: App {
    @StateObject private var queue = QueueModel()
    @NSApplicationDelegateAdaptor private var delegate: AppDelegate

    var body: some Scene {
        Window("SampleBuddy", id: "main") {
            ContentView()
                .environmentObject(queue)
                .onAppear { delegate.queue = queue }
        }
        .defaultSize(width: 780, height: 560)
        Settings {
            SettingsView().environmentObject(queue)
        }
    }
}

/// Handles files dropped on the Dock icon or opened with "Open With → SampleBuddy".
final class AppDelegate: NSObject, NSApplicationDelegate {
    var queue: QueueModel?

    func application(_ application: NSApplication, open urls: [URL]) {
        Task { @MainActor in queue?.add(files: urls) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    // Quitting leaves the engine service running so queued jobs finish; it exits on its own
    // once it has been idle with no client connected (Engine.idleExitSeconds).
}
