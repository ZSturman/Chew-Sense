//
//  CollectionRecommendationTests.swift
//  ChewSenseLiveTests
//

import XCTest
@testable import ChewSenseLive

@MainActor
final class CollectionRecommendationTests: XCTestCase {
    override func setUp() {
        super.setUp()
        clearConfigurationDefaults()
    }

    override func tearDown() {
        clearConfigurationDefaults()
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

        let config = DetectionConfiguration()
        config.applyRecommendation(recommendation)

        XCTAssertEqual(config.activityPreset, .headShake)
        XCTAssertEqual(config.activitySlug, "head_shake")
        XCTAssertEqual(config.activityLabel, "Shaking head")
        XCTAssertEqual(config.testScenario, recommendation.scenarioText)
    }

    private func clearConfigurationDefaults() {
        let defaults = UserDefaults.standard
        for key in [
            "config.testScenario",
            "config.activityPreset",
            "config.customActivity",
            "config.goldLockCandidate",
        ] {
            defaults.removeObject(forKey: key)
        }
    }
}
