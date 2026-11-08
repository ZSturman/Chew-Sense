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
}
