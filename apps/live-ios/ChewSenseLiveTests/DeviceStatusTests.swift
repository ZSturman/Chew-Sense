//
//  DeviceStatusTests.swift
//  ChewSenseLiveTests
//

import SwiftUI
import XCTest
@testable import ChewSenseLive

/// Opt-in check on the paired physical iPhone: Live's own pairing fetches the
/// Mac's shared status over the pinned connection. Reads only. Run with
/// TEST_RUNNER_CHEWSENSE_EXPECTED_HEADLINE set to the headline in
/// workspace/loop/project_status.json.
@MainActor
final class DeviceStatusTests: XCTestCase {
    func testPairedLiveFetchesSharedProjectStatus() async throws {
        #if targetEnvironment(simulator)
        throw XCTSkip("needs the paired physical device")
        #else
        guard let headline = ProcessInfo.processInfo.environment["CHEWSENSE_EXPECTED_HEADLINE"] else {
            throw XCTSkip("CHEWSENSE_EXPECTED_HEADLINE not set")
        }
        let mac = LiveMacStatus(defaults: .standard)
        XCTAssertNotNil(mac.paired, "Live is not paired on this device")
        await mac.refresh()
        XCTAssertEqual(mac.state, .connected)
        let status = try XCTUnwrap(mac.project)
        XCTAssertEqual(status.headline, headline)
        if let summary = ProcessInfo.processInfo.environment["CHEWSENSE_EXPECTED_SUMMARY"] {
            XCTAssertEqual(status.summary, summary)
        }
        XCTAssertTrue(status.steps.contains { $0.app == "live" })
        if ProcessInfo.processInfo.environment["CHEWSENSE_EXPECT_HOME_SERVER"] != nil {
            // Learned from the Mac's pinned status; reachable with the Pi's public certificate.
            let config = try XCTUnwrap(mac.link.mailbox.config, "Live has not received the home server")
            XCTAssertEqual(URL(string: config.url)?.host, "kiosk-lab.tailef7acc.ts.net")
            let copy = try await mac.link.mailbox.status()
            XCTAssertNotNil(copy.status["pipeline"])
        }

        let renderer = ImageRenderer(content: ProjectStatusCard(status: status, app: "live")
            .padding().frame(width: 390).background(Color(.systemBackground)))
        renderer.scale = 3
        if let image = renderer.uiImage {
            let shot = XCTAttachment(image: image)
            shot.name = "live-project-status"
            shot.lifetime = .keepAlways
            add(shot)
        }
        #endif
    }
}
