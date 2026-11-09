import AppKit
import SwiftUI
import XCTest
@testable import ChewSenseReview

/// Opt-in offscreen renders of the new panels with a real workspace, for the
/// docs. Skipped unless CHEWSENSE_SNAPSHOT_DIR and CHEWSENSE_SNAPSHOT_WORKSPACE
/// are set (xcodebuild: TEST_RUNNER_ prefix). Reads files only; never saves.
@MainActor
final class ReviewSnapshotTests: XCTestCase {
    private var environment: (output: URL, workspace: URL)? {
        let env = ProcessInfo.processInfo.environment
        guard let output = env["CHEWSENSE_SNAPSHOT_DIR"], let workspace = env["CHEWSENSE_SNAPSHOT_WORKSPACE"] else { return nil }
        return (URL(fileURLWithPath: output), URL(fileURLWithPath: workspace))
    }

    private func render<V: View>(_ view: V, size: CGSize, to url: URL) throws {
        let host = NSHostingView(rootView: view.frame(width: size.width, height: size.height, alignment: .top))
        host.frame = NSRect(origin: .zero, size: size)
        let window = NSWindow(contentRect: host.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        RunLoop.current.run(until: Date().addingTimeInterval(1.0))
        let rep = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: rep)
        try XCTUnwrap(rep.representation(using: .png, properties: [:])).write(to: url)
    }

    func testRenderChewingSpansAndTrainingStatus() throws {
        guard let (output, workspace) = environment else { throw XCTSkip("snapshot environment not set") }
        let saved = UserDefaults.standard.object(forKey: "ChewSenseReview.workspaceRoot")
        defer { UserDefaults.standard.set(saved, forKey: "ChewSenseReview.workspaceRoot") }
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

        let assistedRoot = workspace.appending(path: "loop/assisted")
        let sessions = SessionDiscovery(paths: WorkspacePaths(root: assistedRoot)).listSessions()
        let session = try XCTUnwrap(sessions.first { $0.id.hasPrefix("Guided-baseline") })
        let state = ReviewerState()
        let playback = PlaybackController()
        let assist = CoarseAssistStore()
        assist.load(session: session, workspaceRoot: assistedRoot)
        XCTAssertNotNil(assist.proposals)
        try render(CoarseAssistPanel(onPlayClip: {})
                    .environmentObject(state).environmentObject(playback)
                    .environmentObject(assist),
                   size: CGSize(width: 380, height: 860), to: output.appending(path: "review-chewing-spans.png"))

        let shared = try ProjectStatus.decode(Data(contentsOf: workspace.appending(path: "loop/project_status.json")))
        try render(ProjectStatusCard(status: shared, app: "review").padding(16)
                    .background(Color(nsColor: .windowBackgroundColor)),
                   size: CGSize(width: 560, height: 760), to: output.appending(path: "review-project-status.png"))

        let training = TrainingStatusStore()
        training.adopt(reviewRoot: assistedRoot)
        XCTAssertNotNil(training.status, training.loadError ?? "")
        try render(TrainingStatusContent(store: training, initialSelection: "Not-eating-20251212-175356")
                    .padding(20).background(Color(nsColor: .windowBackgroundColor)),
                   size: CGSize(width: 1100, height: 2700), to: output.appending(path: "review-training-status.png"))
    }

    /// Up next, then the guided review of each kind of task, rendered from the
    /// real workspace without saving: the drafts must be byte-identical afterwards.
    func testRenderGuidedReview() throws {
        guard let (output, workspace) = environment else { throw XCTSkip("snapshot environment not set") }
        let saved = UserDefaults.standard.object(forKey: "ChewSenseReview.workspaceRoot")
        defer { UserDefaults.standard.set(saved, forKey: "ChewSenseReview.workspaceRoot") }
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        let drafts = try FileManager.default.subpathsOfDirectory(atPath: workspace.appending(path: "loop").path)
            .filter { $0.hasSuffix("review-draft.json") }
            .map { workspace.appending(path: "loop/\($0)") }
        let before = try drafts.map { try Data(contentsOf: $0) }

        func scene(_ state: ReviewerState, _ assist: CoarseAssistStore) -> some View {
            let project = ProjectStatusStore()
            project.start(workspace: state.workspaceRoot)
            return RootView()
                .environmentObject(state).environmentObject(PlaybackController())
                .environmentObject(TimelineViewport()).environmentObject(assist).environmentObject(project)
        }
        let size = CGSize(width: 1440, height: 900)

        let home = ReviewerState()
        home.setWorkspaceRoot(workspace.appending(path: "loop/assisted"))
        try render(scene(home, CoarseAssistStore()), size: size, to: output.appending(path: "guided-up-next.png"))

        for (kind, prefix, name) in [("assisted", "Guided-baseline", "guided-check-suggestions.png"),
                                     ("blind", "Not-eating-20261026", "guided-retired.png")] {
            let state = ReviewerState()
            let assist = CoarseAssistStore()
            state.setWorkspaceRoot(workspace.appending(path: "loop/\(kind)"))
            let session = try XCTUnwrap(state.sessions.first { $0.id.hasPrefix(prefix) })
            state.loadSession(session)
            assist.load(session: state.isBlindReview ? nil : session, workspaceRoot: state.workspaceRoot)
            try render(scene(state, assist), size: size, to: output.appending(path: name))
            if kind == "assisted" {
                state.chosenStep = .finish
                try render(scene(state, assist), size: size, to: output.appending(path: "guided-watch-finish.png"))
                if assist.proposals != nil {
                    state.chosenStep = .spans
                    try render(scene(state, assist), size: size, to: output.appending(path: "guided-spans.png"))
                }
            }
            XCTAssertFalse(state.isDirty)
        }
        XCTAssertEqual(try drafts.map { try Data(contentsOf: $0) }, before)
    }
}
