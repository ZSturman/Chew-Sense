import XCTest
@testable import ChewSenseLive

final class LiveSessionContractTests: XCTestCase {
    func testMotionOnlyManifestCannotClaimVideoOrConfirmedVideoTiming() throws {
        let session = FileManager.default.temporaryDirectory
            .appendingPathComponent("MotionOnlyContract-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: session) }
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        try Data("timestamp,ax,ay,az,gx,gy,gz\n100.2,0,0,0,0,0,0\n101.2,0,0,0,0,0,0\n".utf8)
            .write(to: session.appendingPathComponent("motion.csv"))
        let snapshot = LiveCaptureSnapshot(
            sessionID: session.lastPathComponent, createdAt: "2026-11-07T00:00:00Z",
            activity: "shadow_validation", videoFileName: nil, motionFileName: "motion.csv",
            videoClockOriginHostTime: 100, usedConfirmedVideoOrigin: true,
            firstMotionHostTime: 100.2, lastMotionHostTime: 101.2,
            motionSampleCount: 2, motionSampleGapCount: 0, largestMotionSampleGapSec: 0,
            interrupted: false, interruptionCount: 0, finalized: true,
            modelID: "test", modelMode: "test"
        )
        try LiveCanonicalSessionWriter.write(snapshot: snapshot, sessionURL: session, sourceAppVersion: "test")
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(
            with: Data(contentsOf: session.appendingPathComponent("session.json"))) as? [String: Any])
        XCTAssertTrue(payload["video"] is NSNull)
        let capture = try XCTUnwrap(payload["capture"] as? [String: Any])
        XCTAssertTrue(capture["fps"] is NSNull)
        let clock = try XCTUnwrap(payload["clock_sync"] as? [String: Any])
        XCTAssertEqual(clock["method"] as? String, "capture_clock_fallback")
        XCTAssertEqual(clock["confidence"] as? String, "low")
        XCTAssertNil(clock["video_start_host_time_s"])
        let quality = try XCTUnwrap(payload["quality"] as? [String: Any])
        XCTAssertEqual(quality["video_present"] as? Bool, false)
        XCTAssertEqual(quality["motion_present"] as? Bool, true)
        let motion = try XCTUnwrap(payload["motion"] as? [String: Any])
        XCTAssertEqual((motion["sha256"] as? String)?.count, 64)
    }

    func testCanonicalManifestFreezesClockAndMediaHashes() throws {
        let session = FileManager.default.temporaryDirectory
            .appendingPathComponent("LiveSessionContractTests-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: session) }
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        try Data("video".utf8).write(to: session.appendingPathComponent("sample.mov"))
        try Data("timestamp,ax,ay,az,gx,gy,gz\n100.2,0,0,0,0,0,0\n".utf8)
            .write(to: session.appendingPathComponent("motion.csv"))

        let snapshot = LiveCaptureSnapshot(
            sessionID: session.lastPathComponent,
            createdAt: "2026-07-13T00:00:00Z",
            activity: "eating_hard",
            videoFileName: "sample.mov",
            motionFileName: "motion.csv",
            videoClockOriginHostTime: 100,
            usedConfirmedVideoOrigin: true,
            firstMotionHostTime: 100.2,
            lastMotionHostTime: 103.2,
            motionSampleCount: 301,
            motionSampleGapCount: 1,
            largestMotionSampleGapSec: 0.2,
            interrupted: true,
            interruptionCount: 1,
            finalized: true,
            modelID: "chewnet-test",
            modelMode: "temporal_tcn"
        )
        try LiveCanonicalSessionWriter.write(
            snapshot: snapshot,
            sessionURL: session,
            sourceAppVersion: "test"
        )

        let payload = try XCTUnwrap(
            JSONSerialization.jsonObject(
                with: Data(contentsOf: session.appendingPathComponent("session.json"))
            ) as? [String: Any]
        )
        XCTAssertEqual(payload["schema_version"] as? String, "2.0.0")
        XCTAssertEqual(payload["source_app"] as? String, "live")
        let clock = try XCTUnwrap(payload["clock_sync"] as? [String: Any])
        XCTAssertEqual(
            try XCTUnwrap(clock["video_clock_origin_host_time_s"] as? Double),
            100,
            accuracy: 1e-9
        )
        XCTAssertEqual(
            try XCTUnwrap(clock["motion_to_video_offset_s"] as? Double),
            0.2,
            accuracy: 1e-9
        )
        XCTAssertEqual(clock["method"] as? String, "shared_host_clock")
        let capture = try XCTUnwrap(payload["capture"] as? [String: Any])
        XCTAssertEqual((capture["motion_sample_gap_count"] as? NSNumber)?.intValue, 1)
        XCTAssertEqual((capture["interruption_count"] as? NSNumber)?.intValue, 1)
        let motion = try XCTUnwrap(payload["motion"] as? [String: Any])
        XCTAssertEqual((motion["sha256"] as? String)?.count, 64)
        let video = try XCTUnwrap(payload["video"] as? [String: Any])
        XCTAssertEqual((video["sha256"] as? String)?.count, 64)
    }

    func testMealSummaryExistsWhenSynchronousWriterReturns() throws {
        let session = FileManager.default.temporaryDirectory
            .appendingPathComponent("MealSummaryAvailability-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: session) }
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        try LiveCanonicalSessionWriter.writeMealMetrics(
            [
                "schema_version": "2.0.0",
                "texture": "mixed",
                "detected_chews": 42,
                "chew_event_model_promoted": true
            ],
            to: session
        )
        let url = session.appendingPathComponent("meal_metrics.json")
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
        let payload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any]
        )
        XCTAssertEqual((payload["detected_chews"] as? NSNumber)?.intValue, 42)
    }

    func testExplicitVerificationPersistsThreeUniqueMealsAndActivatesCadence() throws {
        let suite = "LiveSessionContractTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = PersonalBaselineStore(defaults: defaults, storageKey: "baseline")
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("CalibrationVerification-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        var receipts: [CalibrationVerificationReceipt] = []
        for (index, interval) in [0.7, 0.5, 0.6].enumerated() {
            let session = root.appendingPathComponent("meal-\(index)")
            try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
            try LiveCanonicalSessionWriter.writeMealMetrics(
                [
                    "schema_version": "2.0.0",
                    "texture": "hard",
                    "detected_chews": 30,
                    "median_inter_chew_interval_sec": interval,
                    "confidence_coverage": 0.9,
                    "chew_event_model_promoted": true
                ],
                to: session
            )
            receipts.append(
                try CalibrationMealVerifier.verify(sessionURL: session, store: store)
            )
        }

        XCTAssertEqual(receipts.map(\.verifiedMealCount), [1, 2, 3])
        XCTAssertTrue(try XCTUnwrap(receipts.last).cadenceGoalActivated)
        XCTAssertEqual(
            store.goals(
                for: .hard,
                capabilities: ModelCapabilities(
                    temporalChewEventHead: true,
                    foodEntryHead: false
                )
            )?.minimumMedianInterChewIntervalSec,
            0.6
        )

        let duplicate = try CalibrationMealVerifier.verify(
            sessionURL: root.appendingPathComponent("meal-0"),
            store: store
        )
        XCTAssertTrue(duplicate.alreadyVerified)
        XCTAssertEqual(duplicate.verifiedMealCount, 3)
    }

    @MainActor
    func testActivatedPersonalGoalDrivesCadenceCoach() {
        let tracker = MealMetricsTracker()
        tracker.configure(
            texture: .soft,
            goals: PersonalizedCoachingGoals(
                minimumMedianInterChewIntervalSec: 0.5,
                chewsPerFoodEntry: nil
            )
        )
        var cue: CadenceCue?
        for index in 0...7 {
            cue = tracker.recordChew(
                ChewStroke(
                    timestamp: Double(index) * 0.3,
                    confidence: 0.95,
                    source: .model
                )
            ) ?? cue
        }
        XCTAssertEqual(cue, .slowDown)
        XCTAssertEqual(tracker.latestCue, .slowDown)
    }
}
