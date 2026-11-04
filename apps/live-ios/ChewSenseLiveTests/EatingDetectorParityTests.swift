import XCTest
@testable import ChewSenseLive

/// The exported eating detector must reproduce the Python reference exactly.
/// The fixture is a synthetic stream (no wearer data) with expected outputs
/// from `pipeline.eating.runtime.run_stream`; recorded-session parity runs on
/// the Mac with `chewsense-motion eating-parity`.
final class EatingDetectorParityTests: XCTestCase {
    private struct Fixture: Decodable {
        struct Window: Decodable {
            let end: Double
            let validFraction: Double
            let probability: Double?
            let smoothed: Double
            let chewing: Bool
        }

        let modelId: String
        let droppedSamples: Int
        let samples: [[Double]]
        let windows: [Window]
        let bouts: [[Double]]
    }

    private var testsDirectory: URL { URL(fileURLWithPath: #filePath).deletingLastPathComponent() }

    private func loadSpec() throws -> EatingDetectorSpec {
        try EatingDetectorSpec.load(from: testsDirectory
            .deletingLastPathComponent()
            .appendingPathComponent("ChewSenseLive/EatingDetector/eating-detector.json"))
    }

    private func loadFixture() throws -> Fixture {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(Fixture.self, from: Data(contentsOf: testsDirectory
            .appendingPathComponent("Fixtures/eating_detector_parity.json")))
    }

    func testBundledSpecificationLoadsFromAppBundle() throws {
        let spec = try EatingDetectorSpec.loadBundled(Bundle(for: EatingDetectorRuntime.self))
        XCTAssertEqual(spec.status, "experimental_candidate")
        XCTAssertEqual(spec.model.featureCount, 80)
        XCTAssertEqual(spec.decision.strideS, 0.5)
    }

    func testStreamingRuntimeMatchesPythonReferenceExactly() throws {
        let spec = try loadSpec()
        let fixture = try loadFixture()
        XCTAssertEqual(fixture.modelId, spec.modelId, "regenerate the fixture for the bundled specification")

        let runtime = EatingDetectorRuntime(spec: spec)
        var outputs: [EatingDetectorOutput] = []
        for row in fixture.samples {
            outputs += runtime.push(EatingMotionSample(timestamp: row[0], values: row[1...6].map { Float($0) }))
        }
        XCTAssertEqual(runtime.droppedSamples, fixture.droppedSamples)
        XCTAssertEqual(outputs.count, fixture.windows.count)
        for (output, expected) in zip(outputs, fixture.windows) {
            XCTAssertEqual(output.windowEnd, expected.end)
            XCTAssertEqual(output.validFraction, expected.validFraction)
            XCTAssertEqual(output.probability, expected.probability, "window ending \(expected.end)")
            XCTAssertEqual(output.smoothed, expected.smoothed)
            XCTAssertEqual(output.chewing, expected.chewing)
        }

        var bouts: [[Double]] = []
        var start: Double?
        for output in outputs {
            if output.chewing, start == nil { start = output.windowEnd }
            if !output.chewing, let begun = start {
                bouts.append([begun, output.windowEnd])
                start = nil
            }
        }
        if let begun = start, let last = outputs.last { bouts.append([begun, last.windowEnd]) }
        XCTAssertEqual(bouts, fixture.bouts)
        XCTAssertFalse(fixture.bouts.isEmpty, "the fixture should exercise bout start and end")
        XCTAssertTrue(fixture.windows.contains { $0.probability == nil }, "the fixture should exercise a sensor gap")
    }

    func testRejectsSpecificationsWithUnsupportedShape() throws {
        let url = testsDirectory.deletingLastPathComponent()
            .appendingPathComponent("ChewSenseLive/EatingDetector/eating-detector.json")
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        json["format_version"] = 2
        let broken = FileManager.default.temporaryDirectory.appendingPathComponent("eating-detector-v2.json")
        try JSONSerialization.data(withJSONObject: json).write(to: broken)
        defer { try? FileManager.default.removeItem(at: broken) }
        XCTAssertThrowsError(try EatingDetectorSpec.load(from: broken))
    }

    func testInvalidWindowsFeedZeroToTheDecoder() throws {
        let spec = try loadSpec()
        let runtime = EatingDetectorRuntime(spec: spec, firstWindowEnd: 3.0)
        // Two samples 4 s apart: every window is unmeasured.
        _ = runtime.push(EatingMotionSample(timestamp: 0, ax: 0, ay: 0, az: 0, gx: 0, gy: 0, gz: 0))
        let outputs = runtime.push(EatingMotionSample(timestamp: 4, ax: 0, ay: 0, az: 0, gx: 0, gy: 0, gz: 0))
        XCTAssertEqual(outputs.map(\.windowEnd), [3.0, 3.5, 4.0])
        XCTAssertTrue(outputs.allSatisfy { $0.probability == nil && $0.decoderInput == 0 && !$0.chewing })
    }
}
