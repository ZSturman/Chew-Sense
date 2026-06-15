import XCTest
import SwiftUI
import AppKit
@testable import ChewSenseShowcase

final class MealSummaryTests: XCTestCase {
    func testEmptySummaryIsInsufficient() {
        let s = MealSummary(label: "x", totalSeconds: 10, bites: [])
        XCTAssertEqual(s.qualityGrade, .insufficientData)
        XCTAssertEqual(s.biteCount, 0)
        XCTAssertEqual(s.totalChews, 0)
    }

    func testTotalsAndAverages() {
        let bites = [
            BiteEvent(startSeconds: 0,  endSeconds: 4, chewCount: 12),
            BiteEvent(startSeconds: 6,  endSeconds: 9, chewCount: 9),
        ]
        let s = MealSummary(label: "x", totalSeconds: 10, bites: bites)
        XCTAssertEqual(s.biteCount, 2)
        XCTAssertEqual(s.totalChews, 21)
        XCTAssertEqual(s.avgChewsPerBite, 10.5, accuracy: 1e-6)
        XCTAssertGreaterThan(s.avgChewRateHz, 0)
    }

    func testRushedBitesFlagged() {
        // RuntimeConfig.rushedChewsPerBite default is 5.
        let bites = [
            BiteEvent(startSeconds: 0, endSeconds: 1, chewCount: 2),
            BiteEvent(startSeconds: 2, endSeconds: 5, chewCount: 12),
        ]
        let s = MealSummary(label: "x", totalSeconds: 6, bites: bites)
        XCTAssertEqual(s.rushedBiteCount, 1)
    }
}

final class BiteEventTests: XCTestCase {
    func testChewRate() {
        let b = BiteEvent(startSeconds: 0, endSeconds: 5, chewCount: 10)
        XCTAssertEqual(b.chewRateHz, 2.0, accuracy: 1e-6)
    }
    func testZeroDurationGivesZeroRate() {
        let b = BiteEvent(startSeconds: 1, endSeconds: 1, chewCount: 5)
        XCTAssertEqual(b.chewRateHz, 0)
    }
}

final class ReplayPlayerTests: XCTestCase {
    func testLoadsBundledEatingSample() throws {
        let player = ReplayPlayer()
        try player.load(resource: "eating-sample")
        XCTAssertGreaterThan(player.samples.count, 100)
        XCTAssertEqual(player.samples.first?.timestamp ?? -1, 0, accuracy: 1e-9)
        XCTAssertGreaterThan(player.totalTime, 1)
    }

    func testLoadsBundledNotEatingSample() throws {
        let player = ReplayPlayer()
        try player.load(resource: "not-eating-sample")
        XCTAssertGreaterThan(player.samples.count, 100)
    }
}

@MainActor
final class ModelInferenceTests: XCTestCase {
    func testPackagedModelLoadsAndAcceptsTwelveFeatures() throws {
        let model = ChewModel()
        XCTAssertEqual(model.state, .ready)

        let probability = try model.predictChewProbability(
            features: Array(repeating: 0, count: 12)
        )
        XCTAssertTrue(probability.isFinite)
        XCTAssertGreaterThanOrEqual(probability, 0)
        XCTAssertLessThanOrEqual(probability, 1)
    }

    func testRecordedEatingProducesSubstantiallyMoreChewingThanBaseline() throws {
        let eating = try replayMetrics(resource: "eating-sample")
        let baseline = try replayMetrics(resource: "not-eating-sample")

        XCTAssertGreaterThan(eating.startedEpisodes, 0)
        XCTAssertGreaterThan(
            eating.chewingWindows,
            max(1, baseline.chewingWindows) * 3
        )
        XCTAssertEqual(baseline.finalState, .idle)
    }

    private func replayMetrics(resource: String) throws -> ReplayMetrics {
        let player = ReplayPlayer()
        try player.load(resource: resource)
        let engine = InferenceEngine()
        var startedEpisodes = 0
        var chewingWindows = 0

        for sample in player.samples {
            guard let tick = engine.push(sample) else { continue }
            if tick.startedEpisode { startedEpisodes += 1 }
            if tick.state == .chewing { chewingWindows += 1 }
        }

        XCTAssertNil(engine.errorMessage)
        return ReplayMetrics(
            startedEpisodes: startedEpisodes,
            chewingWindows: chewingWindows,
            finalState: engine.state
        )
    }
}

private struct ReplayMetrics {
    let startedEpisodes: Int
    let chewingWindows: Int
    let finalState: ChewState
}

@MainActor
final class ShowcaseViewSmokeTests: XCTestCase {
    func testEveryPrimaryDestinationRendersWithAccessibilityContent() {
        let session = SessionStore()
        let views: [(name: String, view: AnyView)] = [
            ("root", AnyView(RootView())),
            ("tour", AnyView(PipelineTourView(onTryIt: {}))),
            ("replay", AnyView(ReplayDemoView(session: session, onShowReport: {}))),
            ("live", AnyView(LiveDemoView(session: session, onShowReport: {}))),
            ("report", AnyView(MealReportView(session: session)))
        ]

        for item in views {
            let view = item.view
            let host = NSHostingView(rootView: view)
            host.frame = NSRect(x: 0, y: 0, width: 1100, height: 720)
            host.layoutSubtreeIfNeeded()

            XCTAssertGreaterThan(host.fittingSize.width, 0)
            XCTAssertGreaterThan(host.fittingSize.height, 0)
            let hasAccessibilityContent =
                host.isAccessibilityElement()
                || !(host.accessibilityChildren()?.isEmpty ?? true)
            XCTAssertTrue(hasAccessibilityContent)

            guard
                let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds)
            else {
                XCTFail("Could not create a bitmap for \(item.name)")
                continue
            }
            host.cacheDisplay(in: host.bounds, to: bitmap)
            guard let png = bitmap.representation(using: .png, properties: [:]) else {
                XCTFail("Could not encode \(item.name) as PNG")
                continue
            }
            XCTAssertGreaterThan(png.count, 1_000)

            if ProcessInfo.processInfo.environment["CHEWSENSE_CAPTURE_UI"] == "1" {
                let url = URL(fileURLWithPath: NSTemporaryDirectory())
                    .appendingPathComponent("chewsense-showcase-\(item.name).png")
                try? png.write(to: url)
            }
        }
    }
}
