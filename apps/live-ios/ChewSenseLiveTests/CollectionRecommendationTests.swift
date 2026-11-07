//
//  CollectionRecommendationTests.swift
//  ChewSenseLiveTests
//

import XCTest
@testable import ChewSenseLive

@MainActor
final class CollectionRecommendationTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suite: String!
    func testGuidanceKeepsGroundTruthOutOfLiveCapture() {
        XCTAssertEqual(LiveGuidanceStage.prepare.title, "Prepare a field test")
        XCTAssertTrue(LiveGuidanceStage.record.nextStep.contains("does not create ground truth"))
        XCTAssertTrue(LiveGuidanceStage.export.doneWhen.contains("Mac pipeline"))
    }

    override func setUp() {
        super.setUp()
        suite = "LiveConfigurationTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        defaults = nil
        super.tearDown()
    }

    func testGeneratedRecommendationsMapToKnownPresets() {
        for recommendation in CollectionRecommendations.current.recommendations {
            XCTAssertNotNil(
                recommendation.activityPreset,
                "Generated recommendation has unknown activity slug: \(recommendation.activity)"
            )
        }
    }

    func testApplyingRecommendationSelectsPresetAndScenario() {
        let recommendation = CollectionRecommendation(
            id: "head_shake_false_positive_001",
            activity: "head_shake",
            activityLabel: "Shaking head",
            expectedLabelClass: "negative",
            priority: 1,
            requestedMinutes: 6,
            reasonCodes: ["false_positive_rate_high"],
            reason: "Head-shake negatives still trigger chewing too often.",
            currentMinutes: 1.5,
            targetMinutes: 6.0
        )

        let config = DetectionConfiguration(defaults: defaults)
        config.applyRecommendation(recommendation)

        XCTAssertEqual(config.activityPreset, .headShake)
        XCTAssertEqual(config.activitySlug, "head_shake")
        XCTAssertEqual(config.activityLabel, "Shaking head")
        XCTAssertEqual(config.testScenario, recommendation.scenarioText)
    }

    func testMotionOnlyContextCannotBecomeGroundTruthOrBenchmarkData() throws {
        let config = DetectionConfiguration(defaults: defaults)
        XCTAssertFalse(config.recordReferenceVideo)
        XCTAssertTrue(config.isPresetReadyToRecord)
        XCTAssertEqual(config.effectiveExpectedLabelClass, "unknown")
        config.activityPreset = .baseline
        config.goldLockCandidate = true
        let data = try XCTUnwrap(config.toSessionConfigJSON().data(using: .utf8))
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(payload["expected_label_class"] as? String, "unknown")
        XCTAssertEqual(payload["gold_lock_candidate"] as? Bool, false)
    }
}
