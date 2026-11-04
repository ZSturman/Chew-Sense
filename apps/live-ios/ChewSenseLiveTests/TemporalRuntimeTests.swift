import XCTest
@testable import ChewSenseLive

final class TemporalRuntimeTests: XCTestCase {
    private struct Golden: Decodable {
        struct Decoder: Decodable {
            let eventThreshold: Double
            let eatingThreshold: Double
            let minGapCoverage: Double
            let refractorySec: Double

            enum CodingKeys: String, CodingKey {
                case eventThreshold = "event_threshold"
                case eatingThreshold = "eating_threshold"
                case minGapCoverage = "min_gap_coverage"
                case refractorySec = "refractory_sec"
            }
        }

        struct Row: Decodable {
            let timestamp: Double
            let eatingLogit: Double
            let chewEventLogit: Double
            let phaseSin: Double
            let phaseCos: Double
            let phaseConfidence: Double
            let gapCoverage: Double
            let eatingProbability: Double
            let chewEventProbability: Double

            enum CodingKeys: String, CodingKey {
                case timestamp
                case eatingLogit = "eating_logit"
                case chewEventLogit = "chew_event_logit"
                case phaseSin = "phase_sin"
                case phaseCos = "phase_cos"
                case phaseConfidence = "phase_confidence"
                case gapCoverage = "gap_coverage"
                case eatingProbability = "eating_probability"
                case chewEventProbability = "chew_event_probability"
            }
        }

        let inferenceStepSec: Double
        let probabilityTolerance: Double
        let decoder: Decoder
        let rows: [Row]
        let decodedEventTimestamps: [Double]

        enum CodingKeys: String, CodingKey {
            case inferenceStepSec = "inference_step_sec"
            case probabilityTolerance = "probability_tolerance"
            case decoder
            case rows
            case decodedEventTimestamps = "decoded_event_timestamps"
        }
    }

    func testPythonSwiftGoldenProbabilityAndDecoderParity() throws {
        let fixtureURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("Fixtures/temporal_replay_v2.json")
        let golden = try JSONDecoder().decode(
            Golden.self,
            from: Data(contentsOf: fixtureURL)
        )
        let decoder = ChewEventDecoder(
            eventThreshold: golden.decoder.eventThreshold,
            eatingThreshold: golden.decoder.eatingThreshold,
            minGapCoverage: golden.decoder.minGapCoverage,
            refractorySec: golden.decoder.refractorySec
        )

        for row in golden.rows {
            let output = TemporalInferenceOutput(
                timestamp: row.timestamp,
                eatingLogit: row.eatingLogit,
                chewEventLogit: row.chewEventLogit,
                phaseSin: row.phaseSin,
                phaseCos: row.phaseCos,
                phaseConfidence: row.phaseConfidence,
                gapCoverage: row.gapCoverage
            )
            XCTAssertEqual(
                output.eatingProbability,
                row.eatingProbability,
                accuracy: golden.probabilityTolerance
            )
            XCTAssertEqual(
                output.chewEventProbability,
                row.chewEventProbability,
                accuracy: golden.probabilityTolerance
            )
            decoder.push(output)
        }

        XCTAssertEqual(decoder.decodedEvents.count, golden.decodedEventTimestamps.count)
        for (actual, expected) in zip(
            decoder.decodedEvents.map(\.timestamp),
            golden.decodedEventTimestamps
        ) {
            XCTAssertEqual(actual, expected, accuracy: golden.inferenceStepSec)
        }
    }

    func testTemporalWindowMasksDroppedSampleGap() throws {
        let samples = stride(from: 0.0, through: 3.0, by: 0.02)
            .filter { !($0 > 1.0 && $0 < 1.3) }
            .map {
                MotionSample(
                    timestamp: $0,
                    ax: $0,
                    ay: $0 + 1,
                    az: $0 + 2,
                    gx: $0 + 3,
                    gy: $0 + 4,
                    gz: $0 + 5
                )
            }
        let window = try XCTUnwrap(
            TemporalWindowBuilder.build(samples: samples, endTimestamp: 3.0)
        )
        XCTAssertEqual(window.values.count, 7 * 150)
        XCTAssertLessThan(window.gapCoverage, 1)
        for index in window.timestamps.indices where
            window.timestamps[index] > 1.0 && window.timestamps[index] < 1.3 {
            XCTAssertEqual(window.values[6 * 150 + index], 0)
            for channel in 0..<6 {
                XCTAssertEqual(window.values[channel * 150 + index], 0)
            }
        }
    }

    func testLargeOriginsDoNotChangeGapValidityOrRepairClockResets() throws {
        for origin in [0.0, 3600.0, 100000.0, 1_000_000_000.0] {
            let samples = [origin, origin + 1].map {
                MotionSample(timestamp: $0, ax: 1, ay: 1, az: 1, gx: 1, gy: 1, gz: 1)
            }
            let window = try XCTUnwrap(TemporalWindowBuilder.build(samples: samples))
            for i in 0..<150 { XCTAssertEqual(window.values[6 * 150 + i], (i == 99 || i == 149) ? 1 : 0) }
            XCTAssertNil(TemporalWindowBuilder.build(samples: samples.reversed()))
        }
    }

    func testCadenceCueRequiresFiveIntervalsThreeViolationsAndCooldown() {
        let coach = CadenceCoach(
            minimumMedianIntervalSec: 0.5,
            cooldownSec: 20,
            minimumConfidence: 0.5
        )
        var cueTimes: [Double] = []
        for index in 0...80 {
            let timestamp = Double(index) * 0.3
            if coach.recordChew(timestamp: timestamp, confidence: 0.9) != nil {
                cueTimes.append(timestamp)
            }
        }
        XCTAssertEqual(cueTimes.first ?? -1, 2.1, accuracy: 1e-9)
        XCTAssertTrue(zip(cueTimes, cueTimes.dropFirst()).allSatisfy {
            $0.1 - $0.0 >= 20
        })
    }

    func testBaselineNeedsThreeVerifiedMealsAndFoodEntryGate() throws {
        var book = PersonalBaselineBook()
        book.addVerifiedMeal(
            texture: .hard,
            medianInterChewIntervalSec: 0.65,
            medianChewsPerVerifiedFoodEntry: 18
        )
        book.addVerifiedMeal(
            texture: .hard,
            medianInterChewIntervalSec: 0.55,
            medianChewsPerVerifiedFoodEntry: 20
        )
        XCTAssertNil(book.defaultGoals(for: .hard, foodEntryCapabilityPromoted: true))
        book.addVerifiedMeal(
            texture: .hard,
            medianInterChewIntervalSec: 0.60,
            medianChewsPerVerifiedFoodEntry: 22
        )

        let cadenceOnly = try XCTUnwrap(
            book.defaultGoals(for: .hard, foodEntryCapabilityPromoted: false)
        )
        XCTAssertEqual(cadenceOnly.minimumMedianInterChewIntervalSec, 0.60)
        XCTAssertNil(cadenceOnly.chewsPerFoodEntry)

        let full = try XCTUnwrap(
            book.defaultGoals(for: .hard, foodEntryCapabilityPromoted: true)
        )
        XCTAssertEqual(full.chewsPerFoodEntry, 20)

        var unknownBook = PersonalBaselineBook()
        for value in [0.5, 0.6, 0.7] {
            unknownBook.addVerifiedMeal(
                texture: .unknown,
                medianInterChewIntervalSec: value,
                medianChewsPerVerifiedFoodEntry: 20
            )
        }
        XCTAssertNil(
            unknownBook.defaultGoals(
                for: .unknown,
                foodEntryCapabilityPromoted: true
            )?.chewsPerFoodEntry
        )
    }

    func testBaselineStorePersistsAndLegacyCapabilityDisablesGoals() {
        let suite = "TemporalRuntimeTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let key = "baseline"
        let store = PersonalBaselineStore(defaults: defaults, storageKey: key)
        for value in [0.5, 0.6, 0.7] {
            store.addVerifiedMeal(texture: .mixed, medianInterChewIntervalSec: value)
        }
        let reloaded = PersonalBaselineStore(defaults: defaults, storageKey: key)
        let promoted = ModelCapabilities(temporalChewEventHead: true, foodEntryHead: false)
        XCTAssertEqual(
            reloaded.goals(for: .mixed, capabilities: promoted)?
                .minimumMedianInterChewIntervalSec,
            0.6
        )
        let legacy = ModelCapabilities(temporalChewEventHead: false, foodEntryHead: false)
        XCTAssertNil(reloaded.goals(for: .mixed, capabilities: legacy))
    }
}
