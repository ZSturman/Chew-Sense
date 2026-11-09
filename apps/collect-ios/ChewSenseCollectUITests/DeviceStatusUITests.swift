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

    /// Collect has synchronized the expected catalog and shows the one-nod prompt.
    /// Opens the preparation screen only; it never presses Start Experiment.
    @MainActor
    func testPairedCollectReceivesCatalogUpdate() throws {
        #if targetEnvironment(simulator)
        throw XCTSkip("needs the paired physical device")
        #else
        guard let version = ProcessInfo.processInfo.environment["CHEWSENSE_EXPECTED_CATALOG"] else {
            throw XCTSkip("CHEWSENSE_EXPECTED_CATALOG not set")
        }
        let app = XCUIApplication()
        app.launch()
        let entry = app.buttons["collectRequiredDataButton"]
        XCTAssertTrue(entry.waitForExistence(timeout: 15))
        entry.tap()
        let footer = app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH %@", "Catalog v\(version) ")).firstMatch
        let list = app.collectionViews.firstMatch
        for _ in 0..<8 where !footer.exists { list.swipeUp() }
        XCTAssertTrue(footer.waitForExistence(timeout: 70), "Collect did not show catalog v\(version)")
        let talking = app.buttons["experiment-talking"]
        for _ in 0..<6 where !talking.isHittable { list.swipeDown() }
        XCTAssertTrue(talking.waitForExistence(timeout: 5))
        talking.tap()
        let prompt = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Nod down once")).firstMatch
        XCTAssertTrue(prompt.waitForExistence(timeout: 10), "one-nod prompt not shown")
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "collect-talking-v2"
        shot.lifetime = .keepAlways
        add(shot)
        #endif
    }
}
