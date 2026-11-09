import SwiftUI

@main
struct ChewSenseReviewApp: App {
    @StateObject private var state = ReviewerState()
    @StateObject private var playback = PlaybackController()
    @StateObject private var viewport = TimelineViewport()
    @StateObject private var assist = CoarseAssistStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .onOpenURL { url in
                    // chewsense-review://session/<id>?workspace=blind|assisted
                    if case .session(let id, let workspace)? = ChewSenseLink.parse(url).map({ $0.link }) {
                        state.openSession(id, workspace: workspace)
                    }
                }
                .environmentObject(state)
                .environmentObject(playback)
                .environmentObject(viewport)
                .environmentObject(assist)
                .frame(minWidth: 980, minHeight: 640)
        }
        .windowToolbarStyle(.unifiedCompact)

        Window("Pipeline", id: "pipeline") {
            PipelineView()
                .environmentObject(state)
                .environmentObject(playback)
        }

        Window("Training and Evaluation", id: "training") {
            TrainingStatusView()
                .environmentObject(state)
        }
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(replacing: .undoRedo) {
                Button("Undo") { state.undo() }
                    .keyboardShortcut("z", modifiers: [.command])
                    .disabled(!state.canUndo)
                Button("Redo") { state.redo() }
                    .keyboardShortcut("z", modifiers: [.command, .shift])
                    .disabled(!state.canRedo)
            }
            CommandGroup(replacing: .saveItem) {
                Button("Save Overrides") { state.saveOverrides() }
                    .keyboardShortcut("s", modifiers: [.command])
                    .disabled(state.selectedSession == nil)
            }
            CommandGroup(after: .sidebar) {
                Button("Toggle Sidebar") {
                    NotificationCenter.default.post(name: .toggleSidebar, object: nil)
                }
                .keyboardShortcut("1", modifiers: [.command])
                Button("Toggle Help") {
                    NotificationCenter.default.post(name: .toggleHelp, object: nil)
                }
                .keyboardShortcut("/", modifiers: [.command])
                Button("Open Review Queue") {
                    NotificationCenter.default.post(name: .toggleReviewQueue, object: nil)
                }
                .keyboardShortcut("m", modifiers: [.command])
            }
            CommandMenu("Go") {
                Button("Assisted Review") { state.openReviewWorkspace("assisted") }
                    .keyboardShortcut("1", modifiers: [.command, .option])
                Button("Blind Review") { state.openReviewWorkspace("blind") }
                    .keyboardShortcut("2", modifiers: [.command, .option])
                Divider()
                OpenWindowButton(title: "Pipeline and Next Steps", id: "pipeline")
                    .keyboardShortcut("p", modifiers: [.command, .shift])
                OpenWindowButton(title: "Training and Evaluation", id: "training")
                    .keyboardShortcut("t", modifiers: [.command, .shift])
            }
            CommandGroup(after: .appInfo) {
                Button("Open Workspace…") {
                    NotificationCenter.default.post(name: .pickWorkspace, object: nil)
                }
                .keyboardShortcut("o", modifiers: [.command])
            }
        }
    }
}

extension Notification.Name {
    static let toggleSidebar = Notification.Name("ChewSense.toggleSidebar")
    static let toggleHelp = Notification.Name("ChewSense.toggleHelp")
    static let pickWorkspace = Notification.Name("ChewSense.pickWorkspace")
    static let toggleReviewQueue = Notification.Name("ChewSense.toggleReviewQueue")
}

/// Opens a single-instance window from a menu command.
private struct OpenWindowButton: View {
    let title: String
    let id: String
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button(title) { openWindow(id: id) }
    }
}
