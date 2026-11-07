import XCTest
@testable import ChewSenseLive

@MainActor
final class SessionLibraryTests: XCTestCase {
    func testEmptyCaptureIsNeverPresentedAsSuccessfulModelOutput() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let session = root.appendingPathComponent("empty")
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        try "timestamp,ax,ay,az,gx,gy,gz,predicted_eating\n".write(
            to: session.appendingPathComponent("motion.csv"), atomically: true, encoding: .utf8)
        let library = SessionLibraryViewModel(rootURL: root)
        let summary = try XCTUnwrap(library.sessions.first)
        XCTAssertEqual(summary.motionSampleCount, 0)
        XCTAssertEqual(summary.captureStatus, "No motion captured")
        XCTAssertEqual(summary.exportedPredictionCount, 0)
    }

    func testSavedCheckShowsMeasuredSamplesAndKeepsPredictionsUnreviewed() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let session = root.appendingPathComponent("measured")
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        try "timestamp,ax,ay,az,gx,gy,gz\n100,0,0,0,0,0,0\n103,0,0,0,0,0,0\n".write(
            to: session.appendingPathComponent("motion.csv"), atomically: true, encoding: .utf8)
        try "window_end,probability\n103,0.4\n103.5,\n".write(
            to: session.appendingPathComponent("eating_detector.csv"), atomically: true, encoding: .utf8)
        let library = SessionLibraryViewModel(rootURL: root)
        let summary = try XCTUnwrap(library.sessions.first)
        XCTAssertEqual(summary.motionSampleCount, 2)
        XCTAssertEqual(summary.exportedPredictionCount, 1) // missing-motion windows are not predictions
        XCTAssertEqual(summary.captureStatus, "Unreviewed check")
        XCTAssertFalse(summary.hasReviewedMotion)
    }

    func testRecordingStoppedBeforeWarmupDoesNotShowIdlePredictions() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let session = root.appendingPathComponent("short")
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        try "timestamp,ax,ay,az,gx,gy,gz,predicted_eating\n100,0,0,0,0,0,0,0\n100.02,0,0,0,0,0,0,0\n".write(
            to: session.appendingPathComponent("motion.csv"), atomically: true, encoding: .utf8)
        try "window_start,window_end,state\n".write(
            to: session.appendingPathComponent("predictions.csv"), atomically: true, encoding: .utf8)
        let library = SessionLibraryViewModel(rootURL: root)
        let summary = try XCTUnwrap(library.sessions.first)
        XCTAssertEqual(summary.motionSampleCount, 2)
        XCTAssertEqual(summary.captureStatus, "No model output")
        XCTAssertTrue(library.predictionSpans(for: session).isEmpty)
    }
}
