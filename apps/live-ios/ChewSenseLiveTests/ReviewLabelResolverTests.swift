//
//  ReviewLabelResolverTests.swift
//  ChewSenseLiveTests
//

import XCTest
@testable import ChewSenseLive

@MainActor
final class ReviewLabelResolverTests: XCTestCase {
    private let predictions = [
        PredictionSpan(startTime: 0.0, endTime: 1.0, isChewing: false),
        PredictionSpan(startTime: 1.0, endTime: 2.0, isChewing: true),
        PredictionSpan(startTime: 2.0, endTime: 3.0, isChewing: false)
    ]

    func testUnchangedTimeAlwaysFollowsPrediction() {
        XCTAssertFalse(ReviewLabelResolver.resolvedLabel(
            at: 0.5,
            predictions: predictions,
            reviewSegments: [],
            duration: 3.0
        ))

        XCTAssertTrue(ReviewLabelResolver.resolvedLabel(
            at: 1.5,
            predictions: predictions,
            reviewSegments: [],
            duration: 3.0
        ))
    }

    func testLegacyFalsePositiveForcesNotChewingLabel() {
        let segments = [
            ReviewSegment(startTime: 1.0, endTime: 2.0, kind: .falsePositive)
        ]

        XCTAssertFalse(ReviewLabelResolver.resolvedLabel(
            at: 1.5,
            predictions: predictions,
            reviewSegments: segments,
            duration: 3.0
        ))
    }

    func testLegacyFalseNegativeForcesChewingLabel() {
        let segments = [
            ReviewSegment(startTime: 0.0, endTime: 1.0, kind: .falseNegative)
        ]

        XCTAssertTrue(ReviewLabelResolver.resolvedLabel(
            at: 0.5,
            predictions: predictions,
            reviewSegments: segments,
            duration: 3.0
        ))
    }

    func testChewingCorrectionForcesChewingAcrossPredictionChanges() {
        let segments = [
            ReviewSegment(startTime: 0.5, endTime: 2.5, kind: .chewing)
        ]

        XCTAssertTrue(ReviewLabelResolver.resolvedLabel(
            at: 0.75,
            predictions: predictions,
            reviewSegments: segments,
            duration: 3.0
        ))

        XCTAssertTrue(ReviewLabelResolver.resolvedLabel(
            at: 1.5,
            predictions: predictions,
            reviewSegments: segments,
            duration: 3.0
        ))

        XCTAssertTrue(ReviewLabelResolver.resolvedLabel(
            at: 2.25,
            predictions: predictions,
            reviewSegments: segments,
            duration: 3.0
        ))
    }

    func testNotChewingCorrectionForcesNotChewingAcrossPredictionChanges() {
        let segments = [
            ReviewSegment(startTime: 0.5, endTime: 2.5, kind: .notChewing)
        ]

        XCTAssertFalse(ReviewLabelResolver.resolvedLabel(
            at: 0.75,
            predictions: predictions,
            reviewSegments: segments,
            duration: 3.0
        ))

        XCTAssertFalse(ReviewLabelResolver.resolvedLabel(
            at: 1.5,
            predictions: predictions,
            reviewSegments: segments,
            duration: 3.0
        ))

        XCTAssertFalse(ReviewLabelResolver.resolvedLabel(
            at: 2.25,
            predictions: predictions,
            reviewSegments: segments,
            duration: 3.0
        ))
    }

    func testLegacyCorrectSegmentFollowsPredictionPerTime() {
        let segments = [
            ReviewSegment(startTime: 0.5, endTime: 2.5, kind: .correct)
        ]

        XCTAssertFalse(ReviewLabelResolver.resolvedLabel(
            at: 0.75,
            predictions: predictions,
            reviewSegments: segments,
            duration: 3.0
        ))

        XCTAssertTrue(ReviewLabelResolver.resolvedLabel(
            at: 1.5,
            predictions: predictions,
            reviewSegments: segments,
            duration: 3.0
        ))

        XCTAssertFalse(ReviewLabelResolver.resolvedLabel(
            at: 2.25,
            predictions: predictions,
            reviewSegments: segments,
            duration: 3.0
        ))
    }

    func testPredictionCoverageFillsFullDurationAndCoalescesOverlappingWindows() {
        let overlapping = [
            PredictionSpan(startTime: 0.00, endTime: 0.75, isChewing: false),
            PredictionSpan(startTime: 0.10, endTime: 0.85, isChewing: true),
            PredictionSpan(startTime: 0.20, endTime: 0.95, isChewing: true)
        ]

        let coverage = ReviewLabelResolver.coverageSpans(from: overlapping, duration: 1.0)

        XCTAssertEqual(coverage.count, 2)
        XCTAssertEqual(coverage[0].startTime, 0.0, accuracy: 0.001)
        XCTAssertEqual(coverage[0].endTime, 0.75, accuracy: 0.001)
        XCTAssertFalse(coverage[0].isChewing)
        XCTAssertEqual(coverage[1].startTime, 0.75, accuracy: 0.001)
        XCTAssertEqual(coverage[1].endTime, 1.0, accuracy: 0.001)
        XCTAssertTrue(coverage[1].isChewing)
    }

    func testMissingPredictionsProduceFullNotChewingCoverage() throws {
        let sessionURL = try makeTemporarySession()
        defer { try? FileManager.default.removeItem(at: sessionURL) }
        try FileManager.default.removeItem(at: sessionURL.appendingPathComponent("predictions.csv"))

        let viewModel = SessionReviewViewModel(sessionURL: sessionURL)

        XCTAssertEqual(viewModel.predictionSpans.count, 1)
        XCTAssertEqual(viewModel.predictionSpans[0].startTime, 0.0, accuracy: 0.001)
        XCTAssertEqual(viewModel.predictionSpans[0].endTime, 3.0, accuracy: 0.001)
        XCTAssertFalse(viewModel.predictionSpans[0].isChewing)
        XCTAssertEqual(viewModel.currentPredictionTitle, "Not Chewing")

        _ = try viewModel.prepareExport()
        let labeled = try String(contentsOf: sessionURL.appendingPathComponent("labeled_motion.csv"), encoding: .utf8)
        XCTAssertFalse(labeled.contains("true"))
    }

    func testCorrectionReplacementPreventsOverlapsAndDuplicateLabels() throws {
        let sessionURL = try makeTemporarySession()
        defer { try? FileManager.default.removeItem(at: sessionURL) }

        let viewModel = SessionReviewViewModel(sessionURL: sessionURL)
        viewModel.addSegment(kind: .chewing, startTime: 0.5, endTime: 2.5)
        viewModel.addSegment(kind: .notChewing, startTime: 1.0, endTime: 2.0)

        XCTAssertEqual(viewModel.reviewSegments.count, 3)
        XCTAssertEqual(viewModel.reviewSegments[0].kind, .chewing)
        XCTAssertEqual(viewModel.reviewSegments[0].startTime, 0.5, accuracy: 0.001)
        XCTAssertEqual(viewModel.reviewSegments[0].endTime, 1.0, accuracy: 0.001)
        XCTAssertEqual(viewModel.reviewSegments[1].kind, .notChewing)
        XCTAssertEqual(viewModel.reviewSegments[1].startTime, 1.0, accuracy: 0.001)
        XCTAssertEqual(viewModel.reviewSegments[1].endTime, 2.0, accuracy: 0.001)
        XCTAssertEqual(viewModel.reviewSegments[2].kind, .chewing)
        XCTAssertEqual(viewModel.reviewSegments[2].startTime, 2.0, accuracy: 0.001)
        XCTAssertEqual(viewModel.reviewSegments[2].endTime, 2.5, accuracy: 0.001)
    }

    func testLegacyReviewSegmentsNormalizeToBinaryCorrectionsOnLoad() throws {
        let sessionURL = try makeTemporarySession()
        defer { try? FileManager.default.removeItem(at: sessionURL) }

        let legacy = [
            ReviewSegment(startTime: 0.0, endTime: 1.0, kind: .correct),
            ReviewSegment(startTime: 1.0, endTime: 2.0, kind: .falsePositive),
            ReviewSegment(startTime: 2.0, endTime: 3.0, kind: .falseNegative)
        ]
        let data = try JSONEncoder().encode(legacy)
        try data.write(to: sessionURL.appendingPathComponent("review_segments.json"))

        let viewModel = SessionReviewViewModel(sessionURL: sessionURL)

        XCTAssertEqual(viewModel.reviewSegments.count, 2)
        XCTAssertEqual(viewModel.reviewSegments[0].kind, .notChewing)
        XCTAssertEqual(viewModel.reviewSegments[1].kind, .chewing)
    }

    func testCorrectionToggleStartsInversePredictionAndClosesAtPlayhead() throws {
        let sessionURL = try makeTemporarySession()
        defer { try? FileManager.default.removeItem(at: sessionURL) }

        let viewModel = SessionReviewViewModel(sessionURL: sessionURL)

        viewModel.seek(to: 1.25)
        XCTAssertTrue(viewModel.currentPredictionIsChewing)
        XCTAssertEqual(viewModel.currentCorrectionKind, .notChewing)
        XCTAssertEqual(viewModel.correctionButtonTitle, "Not Chewing")

        viewModel.toggleCorrection()
        XCTAssertTrue(viewModel.hasActiveCorrection)
        XCTAssertEqual(viewModel.activeCorrectionSegment?.kind, .notChewing)
        XCTAssertEqual(viewModel.correctionButtonTitle, "End Not Chewing")

        viewModel.seek(to: 1.75)
        let active = try XCTUnwrap(viewModel.activeCorrectionSegment)
        let displayTimes = viewModel.displayTimes(for: active)
        XCTAssertEqual(displayTimes.startTime, 1.25, accuracy: 0.001)
        XCTAssertEqual(displayTimes.endTime, 1.75, accuracy: 0.001)

        viewModel.toggleCorrection()
        XCTAssertFalse(viewModel.hasActiveCorrection)

        let segment = try XCTUnwrap(viewModel.reviewSegments.first)
        XCTAssertEqual(segment.kind, .notChewing)
        XCTAssertEqual(segment.startTime, 1.25, accuracy: 0.001)
        XCTAssertEqual(segment.endTime, 1.75, accuracy: 0.001)

        XCTAssertFalse(ReviewLabelResolver.resolvedLabel(
            at: 1.5,
            predictions: viewModel.predictionSpans,
            reviewSegments: viewModel.reviewSegments,
            duration: viewModel.timelineDuration
        ))
    }

    func testReviewedExportDoesNotOverwriteCaptureFeedback() throws {
        let sessionURL = try makeTemporarySession()
        defer { try? FileManager.default.removeItem(at: sessionURL) }

        let originalFeedback = """
        timestamp,event_type,predicted_state,corrected_state,window_start,window_end,note
        2026-01-01T00:00:00Z,false_positive,chewing,idle,101.000000,102.000000,capture-time feedback
        """
        try originalFeedback.write(to: sessionURL.appendingPathComponent("feedback.csv"), atomically: true, encoding: .utf8)

        let viewModel = SessionReviewViewModel(sessionURL: sessionURL)
        _ = try viewModel.prepareExport()

        let feedbackAfterExport = try String(contentsOf: sessionURL.appendingPathComponent("feedback.csv"), encoding: .utf8)
        XCTAssertEqual(feedbackAfterExport, originalFeedback)
        XCTAssertTrue(FileManager.default.fileExists(atPath: sessionURL.appendingPathComponent("labeled_motion.csv").path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: sessionURL.appendingPathComponent("review_feedback.csv").path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: sessionURL.appendingPathComponent("review_segments.json").path))
    }

    func testExportWithoutCorrectionsLabelsMotionFromPredictions() throws {
        let sessionURL = try makeTemporarySession()
        defer { try? FileManager.default.removeItem(at: sessionURL) }

        let viewModel = SessionReviewViewModel(sessionURL: sessionURL)
        _ = try viewModel.prepareExport()

        let labeled = try String(contentsOf: sessionURL.appendingPathComponent("labeled_motion.csv"), encoding: .utf8)
        let table = ReviewCSV.parse(labeled)
        let labelIndex = try XCTUnwrap(table.header.firstIndex(of: "label"))
        let labels = table.rows.compactMap { row in
            labelIndex < row.count ? row[labelIndex] : nil
        }

        XCTAssertEqual(labels, ["false", "true", "false", "false"])
    }

    func testPredictionLoaderAcceptsStateAliasesAndRelativeTimebase() throws {
        let sessionURL = try makeTemporarySession()
        defer { try? FileManager.default.removeItem(at: sessionURL) }

        let predictionsCSV = """
        timestamp,window_start,window_end,probability,smoothed,state,features_json
        2026-01-01T00:00:00Z,0.000000,1.000000,0.1000,0.1000,not_chewing,[]
        2026-01-01T00:00:00Z,1.000000,2.000000,0.9000,0.9000,true,[]
        2026-01-01T00:00:00Z,2.000000,3.000000,0.1000,0.1000,idle,[]
        """
        try predictionsCSV.write(to: sessionURL.appendingPathComponent("predictions.csv"), atomically: true, encoding: .utf8)

        let viewModel = SessionReviewViewModel(sessionURL: sessionURL)

        XCTAssertEqual(viewModel.predictionSpans.count, 3)
        XCTAssertEqual(viewModel.predictionSpans[0].startTime, 0, accuracy: 0.001)
        XCTAssertFalse(viewModel.predictionSpans[0].isChewing)
        XCTAssertTrue(viewModel.predictionSpans[1].isChewing)
        XCTAssertFalse(viewModel.predictionSpans[2].isChewing)

        viewModel.seek(to: 1.5)
        XCTAssertEqual(viewModel.currentPredictionTitle, "Chewing")
    }

    func testReviewExportWritesMissingConfigFromSessionMetadata() throws {
        let sessionURL = try makeTemporarySession()
        defer { try? FileManager.default.removeItem(at: sessionURL) }

        let metadata = """
        {
          "session_id": "FieldTest-config",
          "started_at": "2026-01-01T00:00:00Z",
          "model_id": "chewnet-test",
          "dataset_hash": "abc123",
          "dataset_status": "unit lineage",
          "model_package_sha256": "abc123model",
          "runtime_config": {
            "alpha": 0.55,
            "high_threshold": 0.70,
            "low_threshold": 0.35,
            "min_start_windows": 4,
            "min_end_windows": 3
          },
          "platform": "macCatalyst",
          "test_scenario": "config unit"
        }
        """
        try metadata.write(to: sessionURL.appendingPathComponent("session_metadata.json"), atomically: true, encoding: .utf8)

        let viewModel = SessionReviewViewModel(sessionURL: sessionURL)
        let recordingConfiguration = try XCTUnwrap(viewModel.recordingConfiguration)
        XCTAssertEqual(try XCTUnwrap(recordingConfiguration.highThreshold), 0.70, accuracy: 0.001)
        XCTAssertEqual(viewModel.recordingConfiguration?.minStartWindows, 4)
        XCTAssertEqual(viewModel.recordingConfiguration?.datasetStatus, "unit lineage")
        XCTAssertEqual(viewModel.recordingConfiguration?.modelPackageHash, "abc123model")

        _ = try viewModel.prepareExport()

        let configData = try Data(contentsOf: sessionURL.appendingPathComponent("config.json"))
        let configObject = try XCTUnwrap(JSONSerialization.jsonObject(with: configData) as? [String: Any])
        XCTAssertEqual(try XCTUnwrap(configObject["high_threshold"] as? Double), 0.70, accuracy: 0.001)

        let reviewMetadata = try String(contentsOf: sessionURL.appendingPathComponent("review_metadata.json"), encoding: .utf8)
        XCTAssertTrue(reviewMetadata.contains("chewnet-test"))
        XCTAssertTrue(reviewMetadata.contains("abc123"))
    }

    func testSessionLibrarySummarizesLegacyFieldTestBundle() throws {
        let rootURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("ChewSenseLibraryTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: rootURL) }
        try FileManager.default.createDirectory(at: rootURL, withIntermediateDirectories: true)

        let sessionURL = try makeTemporarySession(parent: rootURL, name: "FieldTest-library")
        let feedbackCSV = """
        timestamp,event_type,predicted_state,corrected_state,window_start,window_end,note
        2026-01-01T00:00:00Z,correct,chewing,chewing,101.000000,102.000000,
        """
        try feedbackCSV.write(to: sessionURL.appendingPathComponent("feedback.csv"), atomically: true, encoding: .utf8)
        let metadata = """
        {"session_id":"FieldTest-library","started_at":"2026-01-01T00:00:00Z","model_id":"chewnet-test","dataset_hash":"abc123","runtime_config":{"alpha":0.4},"platform":"iOS","test_scenario":"unit"}
        """
        try metadata.write(to: sessionURL.appendingPathComponent("session_metadata.json"), atomically: true, encoding: .utf8)

        let library = SessionLibraryViewModel(rootURL: rootURL)

        XCTAssertEqual(library.sessions.count, 1)
        let summary = try XCTUnwrap(library.sessions.first)
        XCTAssertEqual(summary.title, "FieldTest-library")
        XCTAssertEqual(summary.predictionCount, 3)
        XCTAssertFalse(summary.hasReviewedMotion)
        XCTAssertEqual(summary.reviewStatus, "unreviewed")
    }

    func testMotionManagerLaunchStateHasNoCameraSession() {
        let manager = MotionManager()
        XCTAssertFalse(manager.isRunning)
        XCTAssertTrue(manager.livePredictionSpans.isEmpty)
        XCTAssertEqual(manager.liveTimelineDuration, 0, accuracy: 0.001)
        #if os(iOS)
        XCTAssertNil(manager.captureSession)
        #endif
    }

    private func makeTemporarySession(
        parent: URL = FileManager.default.temporaryDirectory,
        name: String = "ChewSenseReviewTests-\(UUID().uuidString)"
    ) throws -> URL {
        let sessionURL = parent
            .appendingPathComponent(name, isDirectory: true)
        try FileManager.default.createDirectory(at: sessionURL, withIntermediateDirectories: true)

        let motionCSV = """
        timestamp,ax,ay,az,gx,gy,gz
        100.000000,0,0,0,0,0,0
        101.000000,0,0,0,0,0,0
        102.000000,0,0,0,0,0,0
        103.000000,0,0,0,0,0,0
        """

        let predictionsCSV = """
        timestamp,window_start,window_end,probability,smoothed,state,features_json
        2026-01-01T00:00:00Z,100.000000,101.000000,0.1000,0.1000,idle,[]
        2026-01-01T00:00:00Z,101.000000,102.000000,0.9000,0.9000,chewing,[]
        2026-01-01T00:00:00Z,102.000000,103.000000,0.1000,0.1000,idle,[]
        """

        try motionCSV.write(to: sessionURL.appendingPathComponent("motion.csv"), atomically: true, encoding: .utf8)
        try predictionsCSV.write(to: sessionURL.appendingPathComponent("predictions.csv"), atomically: true, encoding: .utf8)
        return sessionURL
    }
}
