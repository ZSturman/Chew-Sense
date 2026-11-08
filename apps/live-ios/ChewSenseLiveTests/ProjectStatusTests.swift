//
//  ProjectStatusTests.swift
//  ChewSenseLiveTests
//

import XCTest
@testable import ChewSenseLive

/// The shared "where we are" message: Live decodes exactly what the loop
/// service writes, keeps the last copy offline and never needs a Mac to start.
@MainActor
final class ProjectStatusTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suite: String!

    override func setUp() {
        super.setUp()
        suite = "ProjectStatusTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    private func fixture() throws -> Data {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "project_status_synthetic", withExtension: "json"))
        return try Data(contentsOf: url)
    }

    func testDecodesServiceFixture() throws {
        let status = try ProjectStatus.decode(fixture())
        XCTAssertEqual(status.phase, "label")
        XCTAssertEqual(status.stages.map(\.title), ["Collect", "Label", "Train", "Test"])
        XCTAssertEqual(status.stages.filter(\.current).map(\.id), ["label"])
        XCTAssertEqual(status.steps.first?.app, "review")
        XCTAssertEqual(status.steps.first { $0.app == "collect" }?.experimentId, "baseline-90s")
        XCTAssertEqual(status.steps.last?.optional, true)
        XCTAssertEqual(status.checks.map(\.id), ["test_model", "vision", "more_data"])
        XCTAssertNotNil(status.updatedDate)
    }

    func testStepsSayWhereToActFromEachApp() {
        XCTAssertEqual(ProjectStatus.place(of: "live", in: "live"), "This app")
        XCTAssertEqual(ProjectStatus.place(of: "review", in: "live"), "In Review on the Mac")
        XCTAssertEqual(ProjectStatus.place(of: "collect", in: "review"), "In Collect")
        XCTAssertEqual(ProjectStatus.place(of: "mac", in: "collect"), "Automatic on the Mac")
    }

    func testUnpairedLiveShowsLastReceivedMessageWithoutNetwork() async throws {
        let status = try ProjectStatus.decode(fixture())
        defaults.set(try status.encoded(), forKey: "LiveLoopProjectStatus")
        let mac = LiveMacStatus(defaults: defaults)
        XCTAssertEqual(mac.project, status)
        XCTAssertNil(mac.paired)
        await mac.refresh()
        XCTAssertEqual(mac.state, .unpaired)
        XCTAssertFalse(mac.isCurrent)
    }
}
