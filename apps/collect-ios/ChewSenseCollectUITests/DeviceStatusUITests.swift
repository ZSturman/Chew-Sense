import XCTest

/// Opt-in check on a paired physical device: Collect, launched normally (not in
/// fixture mode), shows the Mac's shared project status. It only navigates and
/// reads; it never starts a recording or changes settings. Run with
/// TEST_RUNNER_CHEWSENSE_EXPECTED_HEADLINE set to the headline in
/// workspace/loop/project_status.json.
final class DeviceStatusUITests: XCTestCase {
    @MainActor
    func testPairedCollectShowsSharedProjectStatus() throws {
        #if targetEnvironment(simulator)
        throw XCTSkip("needs the paired physical device")
        #else
        guard let headline = ProcessInfo.processInfo.environment["CHEWSENSE_EXPECTED_HEADLINE"] else {
            throw XCTSkip("CHEWSENSE_EXPECTED_HEADLINE not set")
        }
        let app = XCUIApplication()
        app.launch()
        let entry = app.buttons["collectRequiredDataButton"]
        XCTAssertTrue(entry.waitForExistence(timeout: 15))
        entry.tap()
        let title = app.staticTexts[headline]
        XCTAssertTrue(title.waitForExistence(timeout: 30), "shared headline not shown: \(headline)")
        XCTAssertTrue(app.staticTexts["Same message in Collect, Review and Live."].exists
                      || app.staticTexts.containing(NSPredicate(format: "label CONTAINS 'Same message in Collect'")).count > 0)
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "collect-project-status"
        shot.lifetime = .keepAlways
        add(shot)
        #endif
    }

    /// Links between the installed apps: Collect opens an experiment's preparation
    /// (never Start), and the Live link brings Chew Sense Live to the front.
    @MainActor
    func testLinksOpenTheRightInstalledApp() throws {
        #if targetEnvironment(simulator)
        throw XCTSkip("needs the paired physical device with Collect and Live installed")
        #else
        guard ProcessInfo.processInfo.environment["CHEWSENSE_DEVICE_LINKS"] == "1" else {
            throw XCTSkip("CHEWSENSE_DEVICE_LINKS not set")
        }
        let collect = XCUIApplication()
        collect.launch()
        XCUIDevice.shared.system.open(URL(string: "chewsense-collect://experiment/talking")!)
        let close = collect.buttons["Close"]
        XCTAssertTrue(close.waitForExistence(timeout: 15), "link did not open the experiment sheet")
        let prompt = collect.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Nod down once")).firstMatch
        XCTAssertTrue(prompt.waitForExistence(timeout: 10), "Talking preparation not shown")
        XCTAssertFalse(collect.buttons["Stop"].exists)  // preparation only, nothing recording
        let shot = XCTAttachment(screenshot: collect.screenshot())
        shot.name = "collect-link-talking"
        shot.lifetime = .keepAlways
        add(shot)
        close.tap()

        let live = XCUIApplication(bundleIdentifier: "zachary-sturman.ChewSenseDebuggingModel")
        XCUIDevice.shared.system.open(URL(string: "chewsense-live://status")!)
        XCTAssertTrue(live.wait(for: .runningForeground, timeout: 15), "Chew Sense Live did not open")
        let liveShot = XCTAttachment(screenshot: live.screenshot())
        liveShot.name = "live-from-link"
        liveShot.lifetime = .keepAlways
        add(liveShot)
        #endif
    }
}
