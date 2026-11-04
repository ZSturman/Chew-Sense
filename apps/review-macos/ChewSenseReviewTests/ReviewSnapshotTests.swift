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
        let viewport = TimelineViewport()
        let assist = CoarseAssistStore()
        assist.load(session: session, workspaceRoot: assistedRoot)
        XCTAssertNotNil(assist.proposals)
        try render(CoarseAssistPanel(onClose: {})
                    .environmentObject(state).environmentObject(playback)
                    .environmentObject(viewport).environmentObject(assist),
                   size: CGSize(width: 380, height: 860), to: output.appending(path: "review-chewing-spans.png"))

        let training = TrainingStatusStore()
        training.adopt(reviewRoot: assistedRoot)
        XCTAssertNotNil(training.status, training.loadError ?? "")
        try render(TrainingStatusContent(store: training, initialSelection: "Not-eating-20251212-175356")
                    .padding(20).background(Color(nsColor: .windowBackgroundColor)),
                   size: CGSize(width: 1100, height: 2700), to: output.appending(path: "review-training-status.png"))
    }
}
