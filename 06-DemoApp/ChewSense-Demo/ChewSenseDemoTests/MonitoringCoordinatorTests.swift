import XCTest
@testable import ChewSenseDemo

@MainActor
final class MonitoringCoordinatorTests: XCTestCase {
    func testBundledModelAcceptsTwelveFeatures() throws {
        let model = ChewModel()
        XCTAssertNil(model.loadError)

        let probability = try model.predictChewProbability(
            features: Array(repeating: 0, count: 12)
        )
        XCTAssertTrue(probability.isFinite)
        XCTAssertTrue((0...1).contains(probability))
    }

    func testMonitoringBeginsOnlyAfterFirstSample() {
        let motion = TestMotionSource(isAvailable: true)
        let coordinator = makeCoordinator(motion: motion)

        coordinator.start()
        XCTAssertEqual(coordinator.connectionState, .startingMotion)
        XCTAssertFalse(coordinator.isMonitoring)

        motion.send(sample(at: 0))
        XCTAssertEqual(coordinator.connectionState, .streaming)
        XCTAssertTrue(coordinator.isMonitoring)
        coordinator.stop()
    }

    func testUnavailableAirPodsWaitsWithoutClaimingMonitoring() async {
        let motion = TestMotionSource(isAvailable: false)
        let coordinator = makeCoordinator(motion: motion)

        coordinator.start()
        try? await Task.sleep(nanoseconds: 80_000_000)

        XCTAssertEqual(coordinator.connectionState, .waitingForAirPods)
        XCTAssertFalse(coordinator.isMonitoring)
        coordinator.stop()
    }

    func testMissingFirstSampleFailsClearly() async {
        let motion = TestMotionSource(isAvailable: true)
        let coordinator = makeCoordinator(motion: motion)

        coordinator.start()
        try? await Task.sleep(nanoseconds: 100_000_000)

        guard case .failed(let message) = coordinator.connectionState else {
            return XCTFail("Expected a failed connection state")
        }
        XCTAssertTrue(message.contains("no motion data"))
        XCTAssertFalse(coordinator.isMonitoring)
    }

    func testRejectedMotionStartRetriesWithoutRecursiveLoop() async {
        let motion = TestMotionSource(isAvailable: true, startsSuccessfully: false)
        let coordinator = makeCoordinator(motion: motion)

        coordinator.start()
        try? await Task.sleep(nanoseconds: 45_000_000)

        XCTAssertEqual(coordinator.connectionState, .waitingForAirPods)
        XCTAssertGreaterThanOrEqual(motion.startCount, 2)
        XCTAssertFalse(coordinator.isMonitoring)
        coordinator.stop()
    }

    func testStaleSamplesTriggerMotionRestart() async {
        let motion = TestMotionSource(isAvailable: true)
        let coordinator = makeCoordinator(motion: motion)

        coordinator.start()
        motion.send(sample(at: 0))
        XCTAssertEqual(coordinator.connectionState, .streaming)

        try? await Task.sleep(nanoseconds: 130_000_000)

        XCTAssertGreaterThanOrEqual(motion.startCount, 2)
        XCTAssertFalse(coordinator.isMonitoring)
        coordinator.stop()
    }

    func testHighModelProbabilityTransitionsToChewing() {
        let motion = TestMotionSource(isAvailable: true)
        let notifier = TestNotifier()
        let coordinator = makeCoordinator(
            motion: motion,
            model: TestModel(probability: 0.99),
            notifier: notifier
        )

        coordinator.start()
        for index in 0..<100 {
            motion.send(sample(at: Double(index) * 0.02))
        }

        XCTAssertEqual(coordinator.connectionState, .streaming)
        XCTAssertEqual(coordinator.eatingState, .chewing)
        XCTAssertGreaterThan(coordinator.sampleRateHz, 40)
        XCTAssertGreaterThan(notifier.startedCount, 0)
        coordinator.stop()
    }

    private func makeCoordinator(
        motion: TestMotionSource,
        model: TestModel? = nil,
        notifier: TestNotifier? = nil
    ) -> MonitoringCoordinator {
        MonitoringCoordinator(
            motion: motion,
            model: model ?? TestModel(probability: 0.05),
            notifier: notifier ?? TestNotifier(),
            timing: MonitoringTiming(
                availabilityPollSeconds: 0.01,
                firstSampleTimeoutSeconds: 0.05,
                staleSampleTimeoutSeconds: 0.08
            )
        )
    }

    private func sample(at timestamp: TimeInterval) -> MotionSample {
        let phase = timestamp * 2 * Double.pi * 2
        return MotionSample(
            timestamp: timestamp,
            ax: 0.04 * sin(phase),
            ay: 0.02 * cos(phase),
            az: 0.01 * sin(phase * 0.5),
            gx: 0.01 * sin(phase),
            gy: 0.01 * cos(phase),
            gz: 0.01 * sin(phase)
        )
    }
}

@MainActor
private final class TestMotionSource: MotionStreaming {
    var isAvailable: Bool
    var startCount = 0
    private let startsSuccessfully: Bool
    private var onSample: ((MotionSample) -> Void)?
    private var onError: ((Error) -> Void)?

    init(isAvailable: Bool, startsSuccessfully: Bool = true) {
        self.isAvailable = isAvailable
        self.startsSuccessfully = startsSuccessfully
    }

    func refreshAvailability() -> Bool {
        isAvailable
    }

    func start(
        onSample: @escaping (MotionSample) -> Void,
        onError: @escaping (Error) -> Void
    ) -> Bool {
        guard isAvailable else { return false }
        startCount += 1
        guard startsSuccessfully else { return false }
        self.onSample = onSample
        self.onError = onError
        return true
    }

    func stop() {}

    func send(_ sample: MotionSample) {
        onSample?(sample)
    }
}

@MainActor
private final class TestModel: ChewProbabilityPredicting {
    let probability: Double
    let loadError: String? = nil

    init(probability: Double) {
        self.probability = probability
    }

    func predictChewProbability(features: [Double]) throws -> Double {
        probability
    }
}

@MainActor
private final class TestNotifier: ChewEpisodeNotifying {
    var startedCount = 0
    var endedCount = 0

    func handleEpisodeStarted() {
        startedCount += 1
    }

    func handleEpisodeEnded() {
        endedCount += 1
    }
}
