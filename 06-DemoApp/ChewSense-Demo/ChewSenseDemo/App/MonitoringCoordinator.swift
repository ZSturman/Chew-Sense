import Combine
import Foundation

@MainActor
final class MonitoringCoordinator: ObservableObject {
    @Published private(set) var connectionState: MotionConnectionState = .idle
    @Published private(set) var isAirPodsAvailable: Bool = false
    @Published private(set) var smoothedProbability: Double = 0
    @Published private(set) var mouthOpenness: Double = 0
    @Published private(set) var eatingState: ChewState = .idle
    @Published private(set) var lastEpisodeStartedAt: Date?
    @Published private(set) var lastEpisodeEndedAt: Date?
    @Published private(set) var sampleRateHz: Double = 0
    @Published private(set) var sampleAgeSeconds: TimeInterval?

    private let motion: MotionStreaming
    private let model: ChewProbabilityPredicting
    private let notifier: ChewEpisodeNotifying
    private let detector: ChewDetector
    private let mouth = MouthOpennessEstimator()
    private let timing: MonitoringTiming

    private var samples: [MotionSample] = []
    private let maxBuffer = 256
    private let windowSeconds: Double = 0.75
    private let strideSeconds: Double = 0.25
    private var lastInferenceAt: TimeInterval = 0
    private var lastMotionTimestamp: TimeInterval?
    private var lastSampleReceivedAt: Date?
    private var sessionRequested = false

    private var availabilityTask: Task<Void, Never>?
    private var firstSampleTask: Task<Void, Never>?
    private var staleSampleTask: Task<Void, Never>?

    var isMonitoring: Bool {
        connectionState == .streaming
    }

    var hasActiveSession: Bool {
        sessionRequested
    }

    var connectionStatus: String {
        connectionState.statusText
    }

    var modelAvailable: Bool {
        model.loadError == nil
    }

    var sampleFreshnessText: String {
        guard let sampleAgeSeconds else { return "No samples yet" }
        if sampleAgeSeconds < 0.5 { return "Live" }
        return String(format: "Last sample %.1f s ago", sampleAgeSeconds)
    }

    convenience init() {
        self.init(
            motion: LiveMotionSource(),
            model: ChewModel.shared,
            notifier: ChewEpisodeNotifier(),
            timing: .production,
            detectorConfig: .fromGenerated()
        )
    }

    init(
        motion: MotionStreaming,
        model: ChewProbabilityPredicting,
        notifier: ChewEpisodeNotifying,
        timing: MonitoringTiming,
        detectorConfig: ChewDetectorConfig = .fromGenerated()
    ) {
        self.motion = motion
        self.model = model
        self.notifier = notifier
        self.timing = timing
        detector = ChewDetector(config: detectorConfig)
        isAirPodsAvailable = motion.refreshAvailability()
    }

    func start() {
        guard !sessionRequested else { return }
        guard model.loadError == nil else {
            connectionState = .failed(model.loadError ?? "ChewNet is unavailable.")
            return
        }

        reset(clearMetrics: true)
        sessionRequested = true
        waitForAirPods()
    }

    func stop() {
        sessionRequested = false
        cancelConnectionTasks()
        motion.stop()
        sampleAgeSeconds = nil
        connectionState = .idle
    }

    func toggle() {
        if sessionRequested {
            stop()
        } else {
            start()
        }
    }

    func reset(clearMetrics: Bool) {
        samples.removeAll(keepingCapacity: true)
        lastInferenceAt = 0
        lastMotionTimestamp = nil
        lastSampleReceivedAt = nil
        sampleRateHz = 0
        sampleAgeSeconds = nil
        detector.reset()
        mouth.reset()
        if clearMetrics {
            smoothedProbability = 0
            mouthOpenness = 0
            eatingState = .idle
            lastEpisodeStartedAt = nil
            lastEpisodeEndedAt = nil
        }
    }

    private func waitForAirPods(attemptImmediately: Bool = true) {
        guard sessionRequested else { return }
        availabilityTask?.cancel()
        firstSampleTask?.cancel()
        staleSampleTask?.cancel()
        motion.stop()
        connectionState = .waitingForAirPods

        if attemptImmediately, refreshAvailabilityAndStartIfPossible() {
            return
        }

        availabilityTask = Task { [weak self] in
            guard let self else { return }
            if !attemptImmediately {
                await self.sleep(seconds: self.timing.availabilityPollSeconds)
            }
            while !Task.isCancelled, self.sessionRequested {
                if self.refreshAvailabilityAndStartIfPossible() {
                    return
                }
                await self.sleep(seconds: self.timing.availabilityPollSeconds)
            }
        }
    }

    @discardableResult
    private func refreshAvailabilityAndStartIfPossible() -> Bool {
        let available = motion.refreshAvailability()
        isAirPodsAvailable = available
        guard available, sessionRequested else { return false }
        beginMotionStream()
        return true
    }

    private func beginMotionStream() {
        guard sessionRequested else { return }
        availabilityTask?.cancel()
        firstSampleTask?.cancel()
        staleSampleTask?.cancel()
        connectionState = .startingMotion

        let started = motion.start(
            onSample: { [weak self] sample in
                self?.receive(sample: sample)
            },
            onError: { [weak self] error in
                self?.fail("AirPods motion stopped: \(error.localizedDescription)")
            }
        )

        guard started else {
            waitForAirPods(attemptImmediately: false)
            return
        }

        firstSampleTask = Task { [weak self] in
            guard let self else { return }
            await self.sleep(seconds: self.timing.firstSampleTimeoutSeconds)
            guard
                !Task.isCancelled,
                self.sessionRequested,
                self.connectionState == .startingMotion
            else {
                return
            }
            self.fail("AirPods connected, but no motion data arrived. Reconnect them in iOS controls and retry.")
        }
    }

    private func receive(sample: MotionSample) {
        guard sessionRequested else { return }

        let now = Date()
        if connectionState != .streaming {
            firstSampleTask?.cancel()
            connectionState = .streaming
            startStaleSampleMonitor()
        }

        if let previous = lastMotionTimestamp {
            let delta = sample.timestamp - previous
            if delta > 0, delta < 1 {
                let instantaneousRate = 1.0 / delta
                sampleRateHz = sampleRateHz == 0
                    ? instantaneousRate
                    : (0.85 * sampleRateHz + 0.15 * instantaneousRate)
            }
        }
        lastMotionTimestamp = sample.timestamp
        lastSampleReceivedAt = now
        sampleAgeSeconds = 0
        handleInference(sample: sample)
    }

    private func startStaleSampleMonitor() {
        staleSampleTask?.cancel()
        staleSampleTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled, self.sessionRequested {
                await self.sleep(seconds: min(0.25, self.timing.staleSampleTimeoutSeconds / 2))
                guard let receivedAt = self.lastSampleReceivedAt else { continue }
                let age = Date().timeIntervalSince(receivedAt)
                self.sampleAgeSeconds = age
                if age >= self.timing.staleSampleTimeoutSeconds {
                    self.motion.stop()
                    self.waitForAirPods()
                    return
                }
            }
        }
    }

    private func handleInference(sample: MotionSample) {
        let opened = mouth.update(sample: sample)
        mouthOpenness = 0.4 * mouthOpenness + 0.6 * opened

        samples.append(sample)
        if samples.count > maxBuffer {
            samples.removeFirst(samples.count - maxBuffer)
        }

        if lastInferenceAt == 0 {
            lastInferenceAt = sample.timestamp - strideSeconds
        }
        guard sample.timestamp - lastInferenceAt >= strideSeconds else { return }
        lastInferenceAt = sample.timestamp

        let cutoff = sample.timestamp - windowSeconds
        let window = samples.filter { $0.timestamp >= cutoff }
        guard window.count >= 30 else { return }

        let samplingRate = FeatureExtractor.estimateSamplingRate(from: window)
        let raw = FeatureExtractor.extractFeatures(
            from: window,
            samplingRate: samplingRate
        )
        let normalized = NormalizationConstants.normalize(raw)

        let probability: Double
        do {
            probability = try model.predictChewProbability(features: normalized)
        } catch {
            fail("ChewNet inference failed: \(error.localizedDescription)")
            return
        }

        let gyroRms = TimeFeatures.rms(
            TimeFeatures.magnitude(
                window.map(\.gx),
                window.map(\.gy),
                window.map(\.gz)
            )
        )
        let result = detector.update(
            probability: probability,
            gyroRms: gyroRms,
            chewBandPower: raw[7],
            lowBandPower: raw[8]
        )

        smoothedProbability = result.smoothedProb
        eatingState = result.state

        if result.startedEpisode {
            lastEpisodeStartedAt = Date()
            notifier.handleEpisodeStarted()
        }
        if result.endedEpisode {
            lastEpisodeEndedAt = Date()
            notifier.handleEpisodeEnded()
        }
    }

    private func fail(_ message: String) {
        sessionRequested = false
        cancelConnectionTasks()
        motion.stop()
        connectionState = .failed(message)
    }

    private func cancelConnectionTasks() {
        availabilityTask?.cancel()
        firstSampleTask?.cancel()
        staleSampleTask?.cancel()
        availabilityTask = nil
        firstSampleTask = nil
        staleSampleTask = nil
    }

    private func sleep(seconds: TimeInterval) async {
        guard seconds > 0 else {
            await Task.yield()
            return
        }
        try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }
}
