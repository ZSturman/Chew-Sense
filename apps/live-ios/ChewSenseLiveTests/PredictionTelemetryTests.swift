import XCTest
@testable import ChewSenseLive

@MainActor
final class PredictionTelemetryTests: XCTestCase {
    func testCoverageFillsDurationAndCoalescesOverlappingWindows() {
        let spans = [
            PredictionSpan(startTime: 0.00, endTime: 0.75, isChewing: false),
            PredictionSpan(startTime: 0.10, endTime: 0.85, isChewing: true),
            PredictionSpan(startTime: 0.20, endTime: 0.95, isChewing: true)
        ]
        let coverage = PredictionSpanResolver.coverageSpans(from: spans, duration: 1)
        XCTAssertEqual(coverage.count, 2)
        XCTAssertEqual(coverage[0].startTime, 0, accuracy: 0.001)
        XCTAssertEqual(coverage[0].endTime, 0.75, accuracy: 0.001)
        XCTAssertFalse(coverage[0].isChewing)
        XCTAssertEqual(coverage[1].startTime, 0.75, accuracy: 0.001)
        XCTAssertEqual(coverage[1].endTime, 1, accuracy: 0.001)
        XCTAssertTrue(coverage[1].isChewing)
    }

    func testEmptyPredictionsProduceNotChewingCoverage() {
        let coverage = PredictionSpanResolver.coverageSpans(from: [], duration: 3)
        XCTAssertEqual(coverage.count, 1)
        XCTAssertEqual(coverage[0].startTime, 0)
        XCTAssertEqual(coverage[0].endTime, 3)
        XCTAssertFalse(coverage[0].isChewing)
    }

    func testCSVParserPreservesQuotedFeatures() {
        let table = SessionCSV.parse(
            "timestamp,state,features_json\n1.0,chewing,\"[1,2,3]\"\n"
        )
        XCTAssertEqual(table.header, ["timestamp", "state", "features_json"])
        XCTAssertEqual(table.rows, [["1.0", "chewing", "[1,2,3]"]])
    }

    func testSessionLibraryTreatsLegacyReviewAsMigrationOnly() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("PredictionTelemetryTests-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let session = root.appendingPathComponent("FieldTest-library")
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        try "timestamp,ax,ay,az,gx,gy,gz\n100,0,0,0,0,0,0\n103,0,0,0,0,0,0\n"
            .write(to: session.appendingPathComponent("motion.csv"), atomically: true, encoding: .utf8)
        try "timestamp,window_start,window_end,state\nnow,100,101,idle\nnow,101,102,chewing\n"
            .write(to: session.appendingPathComponent("predictions.csv"), atomically: true, encoding: .utf8)
        let legacy = [["startTime": 0.5, "endTime": 1.5, "kind": "false_positive"]] as [[String: Any]]
        try JSONSerialization.data(withJSONObject: legacy)
            .write(to: session.appendingPathComponent("review_segments.json"))

        let library = SessionLibraryViewModel(rootURL: root)
        let summary = try XCTUnwrap(library.sessions.first)
        XCTAssertEqual(summary.reviewStatus, "legacy annotations present")
        XCTAssertEqual(summary.correctionCount, 1)
        XCTAssertFalse(summary.hasReviewedMotion)
    }

    func testMotionManagerLaunchStateHasNoCameraSession() {
        let manager = MotionManager()
        XCTAssertFalse(manager.isRunning)
        XCTAssertFalse(manager.isStartingSession)
        XCTAssertTrue(manager.livePredictionSpans.isEmpty)
        XCTAssertEqual(manager.liveTimelineDuration, 0, accuracy: 0.001)
        #if os(iOS)
        XCTAssertNil(manager.captureSession)
        #endif
    }
}
