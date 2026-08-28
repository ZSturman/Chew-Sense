//
//  ChewSenseCollectUITests.swift
//  ChewSenseCollectUITests
//
//  Created by Zachary Sturman on 11/13/25.
//

import XCTest

final class ChewSenseCollectUITests: XCTestCase {

    private var app: XCUIApplication!

    override func setUpWithError() throws {
        // Put setup code here. This method is called before the invocation of each test method in the class.
        continueAfterFailure = false

        app = XCUIApplication()
        app.launchArguments.append("--uitesting")
    }

    override func tearDownWithError() throws {
        app = nil
    }

    @MainActor
    private func attachScreenshot(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    // MARK: - Top-level navigation

    @MainActor
    func testInitialScreenShowsRecordingsTitle() throws {
        app.launch()

        let navBar = app.navigationBars["Recordings"]
        XCTAssertTrue(navBar.waitForExistence(timeout: 5.0), "Recordings navigation bar should be visible on launch.")
    }

    // MARK: - Recordings list empty state

    @MainActor
    func testEmptyStateShowsMessageAndNewButton() throws {
        app.launch()

        let emptyMessage = app.staticTexts["No recordings found."]
        XCTAssertTrue(emptyMessage.waitForExistence(timeout: 5.0), "Empty-state message should be shown when there are no recordings.")

        let newButton = app.buttons["New"]
        XCTAssertTrue(newButton.exists, "New button should exist in the empty-state view.")
    }
    
    @MainActor
    func testEmptyStateNewButtonNavigatesToCameraScreen() throws {
        app.launch()

        let emptyMessage = app.staticTexts["No recordings found."]
        XCTAssertTrue(emptyMessage.waitForExistence(timeout: 5.0), "Empty-state message should be visible on first launch.")

        let emptyStateNewButton = app.buttons["emptyStateNewButton"]
        XCTAssertTrue(emptyStateNewButton.waitForExistence(timeout: 5.0), "Empty-state New button should exist in the empty-state view.")
        emptyStateNewButton.tap()

        let cameraPreviewLabel = app.staticTexts["Camera preview"]
        let connectAirPodsLabel = app.staticTexts["Connect AirPods to begin"]

        let didReachCameraScreen = cameraPreviewLabel.waitForExistence(timeout: 5.0) || connectAirPodsLabel.exists
        XCTAssertTrue(didReachCameraScreen, "Tapping the empty-state New button should navigate to the camera screen.")
    }

    // MARK: - Navigation to camera screen

    @MainActor
    func testTappingNewNavigatesToCameraScreen() throws {
        app.launch()

        let newButton = app.navigationBars["Recordings"].buttons["New"]
        XCTAssertTrue(newButton.waitForExistence(timeout: 5.0), "New button should appear on the initial screen.")
        newButton.tap()

        let cameraPreviewLabel = app.staticTexts["Camera preview"]
        let connectAirPodsLabel = app.staticTexts["Connect AirPods to begin"]

        let didReachCameraScreen = cameraPreviewLabel.waitForExistence(timeout: 5.0) || connectAirPodsLabel.exists
        XCTAssertTrue(didReachCameraScreen, "Tapping New should navigate to the camera screen.")
    }

    @MainActor
    func testCameraScreenShowsConnectAirPodsHintWhenMotionUnavailable() throws {
        app.launch()

        let newButton = app.navigationBars["Recordings"].buttons["New"]
        XCTAssertTrue(newButton.waitForExistence(timeout: 5.0), "New button should appear on the initial screen.")
        newButton.tap()

        let connectAirPodsLabel = app.staticTexts["Connect AirPods to begin"]
        XCTAssertTrue(connectAirPodsLabel.waitForExistence(timeout: 5.0), "Camera screen should prompt the user to connect AirPods when motion data is unavailable.")
    }
    
    @MainActor
    func testCameraScreenDisablesButtonsWhenMotionUnavailable() throws {
        app.launch()

        let newButton = app.navigationBars["Recordings"].buttons["New"]
        XCTAssertTrue(newButton.waitForExistence(timeout: 5.0), "New button should appear on the initial screen.")
        newButton.tap()

        let connectAirPodsLabel = app.staticTexts["Connect AirPods to begin"]
        XCTAssertTrue(connectAirPodsLabel.waitForExistence(timeout: 5.0), "Camera screen should prompt the user to connect AirPods when motion data is unavailable.")

        let eatingButton = app.buttons["eatingButton"]
        let notEatingButton = app.buttons["notEatingButton"]

        XCTAssertTrue(eatingButton.exists, "Eating button should exist on the camera screen.")
        XCTAssertTrue(notEatingButton.exists, "Not Eating button should exist on the camera screen.")

        XCTAssertFalse(eatingButton.isEnabled, "Eating button should be disabled when motion is unavailable.")
        XCTAssertFalse(notEatingButton.isEnabled, "Not Eating button should be disabled when motion is unavailable.")
        XCTAssertTrue(app.staticTexts["Check capture readiness"].exists)
        XCTAssertTrue(app.staticTexts["Connect AirPods and wait for motion data before starting. Recording is intentionally disabled until it is available."].exists)
        attachScreenshot("Collect - Motion Required")
    }

    @MainActor
    func testSimulatedMotionCaptureShowsClearSaveState() throws {
        app.launchArguments.append("--ui-motion-available")
        app.launch()

        let newButton = app.navigationBars["Recordings"].buttons["New"]
        XCTAssertTrue(newButton.waitForExistence(timeout: 5.0))
        newButton.tap()

        XCTAssertTrue(app.staticTexts["AirPods motion is ready. Choose Eating or Not Eating to start; Stop saves the session."].waitForExistence(timeout: 5.0))
        let eating = app.buttons["eatingButton"]
        XCTAssertTrue(eating.isEnabled)
        eating.tap()

        let stop = app.buttons["stopRecordingButton"]
        XCTAssertTrue(stop.waitForExistence(timeout: 3.0))
        attachScreenshot("Collect - Recording")
        stop.tap()

        XCTAssertTrue(app.staticTexts["Saved"].waitForExistence(timeout: 3.0))
        XCTAssertTrue(app.staticTexts["Done when the session is saved with video and motion CSV."].exists)
        attachScreenshot("Collect - Saved")
    }

    // MARK: - Launch performance

    @MainActor
    func testLaunchPerformance() throws {
        measure(metrics: [XCTApplicationLaunchMetric()]) {
            let app = XCUIApplication()
            app.launchArguments.append("--uitesting")
            app.launch()
        }
    }
}
