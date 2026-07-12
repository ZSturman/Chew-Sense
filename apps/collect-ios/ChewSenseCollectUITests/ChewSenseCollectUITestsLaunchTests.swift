//
//  ChewSenseCollectUITestsLaunchTests.swift
//  ChewSenseCollectUITests
//
//  Created by Zachary Sturman on 11/13/25.
//

import XCTest

final class ChewSenseCollectUITestsLaunchTests: XCTestCase {

    override class var runsForEachTargetApplicationUIConfiguration: Bool {
        true
    }

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    @MainActor
    func testLaunch() throws {
        let app = XCUIApplication()
        app.launchArguments.append("--uitesting")
        app.launch()

        // Wait for the main UI to be visible.
        let navBar = app.navigationBars["Recordings"]
        XCTAssertTrue(navBar.waitForExistence(timeout: 5.0), "Recordings screen should be visible on launch.")

        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Launch Screen"
        attachment.lifetime = .keepAlways
        add(attachment)
    }
    
    @MainActor
    func testLaunchShowsEmptyState() throws {
        let app = XCUIApplication()
        app.launchArguments.append("--uitesting")
        app.launch()

        let emptyMessage = app.staticTexts["No recordings found."]
        XCTAssertTrue(emptyMessage.waitForExistence(timeout: 5.0), "Empty-state message should be visible on first launch.")
    }

    @MainActor
    func testLaunchInDarkMode() throws {
        let app = XCUIApplication()
        app.launchArguments.append(contentsOf: ["--uitesting", "-ui_testing_appearance", "dark"])
        app.launch()

        let navBar = app.navigationBars["Recordings"]
        XCTAssertTrue(navBar.waitForExistence(timeout: 5.0), "Recordings screen should be visible on launch in dark mode.")
    }
}
