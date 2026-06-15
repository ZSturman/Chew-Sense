import XCTest

final class ChewSenseDemoUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testIdleUIShowsVisibleAvatarAndTruthfulReadyState() {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.otherElements["faceAvatar"].waitForExistence(timeout: 3))
        XCTAssertGreaterThan(app.otherElements["faceAvatar"].frame.height, 150)
        XCTAssertEqual(app.staticTexts["eatingStatusPill"].label, "READY")
        XCTAssertTrue(app.buttons["monitoringButton"].exists)
    }

    func testWaitingStateShowsMotionGuidance() {
        let app = launch(scenario: "waiting")
        app.buttons["monitoringButton"].tap()

        XCTAssertTrue(app.staticTexts["eatingStatusPill"].waitForExistence(timeout: 2))
        XCTAssertEqual(app.staticTexts["eatingStatusPill"].label, "WAITING")
        XCTAssertTrue(app.staticTexts["motionWaitingGuidance"].waitForExistence(timeout: 2))
    }

    func testScriptedChewingReachesChewingState() {
        let app = launch(scenario: "chewing")
        app.buttons["monitoringButton"].tap()

        let pill = app.staticTexts["eatingStatusPill"]
        XCTAssertTrue(
            NSPredicate(format: "label == 'CHEWING'")
                .evaluateWithExpectation(on: pill, timeout: 4)
        )
        XCTAssertTrue(app.staticTexts["streamMetrics"].exists)
    }

    private func launch(scenario: String) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--ui-scenario", scenario]
        app.launch()
        return app
    }
}

private extension NSPredicate {
    func evaluateWithExpectation(on element: XCUIElement, timeout: TimeInterval) -> Bool {
        let expectation = XCTNSPredicateExpectation(predicate: self, object: element)
        return XCTWaiter.wait(for: [expectation], timeout: timeout) == .completed
    }
}
