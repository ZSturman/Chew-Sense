import SwiftUI

@main
struct ChewSenseShowcaseApp: App {
    var body: some Scene {
        WindowGroup("ChewSense Showcase") {
            RootView()
                .frame(minWidth: 1100, minHeight: 720)
        }
        .windowToolbarStyle(.unified)
    }
}
