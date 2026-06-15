import Foundation
import Combine

/// Outcome of a single inference window.
struct InferenceTick: Identifiable {
    let id = UUID()
    let timestamp: TimeInterval
    let probability: Double          // raw sigmoid output
    let smoothed: Double             // EMA-smoothed
    let state: ChewState
    let startedEpisode: Bool
    let endedEpisode: Bool
    let accelMagnitude: Double       // for waveform display
}

/// Drives 0.75 s sliding-window inference on a stream of MotionSamples.
/// Same pipeline used for replay and live so both code paths are identical.
@MainActor
final class InferenceEngine: ObservableObject {

    // MARK: Published state for views
    @Published private(set) var probability: Double = 0.0
    @Published private(set) var smoothed: Double = 0.0
    @Published private(set) var state: ChewState = .idle
    @Published private(set) var bites: [BiteEvent] = []
    @Published private(set) var waveform: [Double] = []   // last N accel-mag samples
    @Published private(set) var errorMessage: String?

    // MARK: Configuration
    let windowSeconds: Double = 0.75
    let strideSeconds: Double = 0.25                      // how often to run inference
    let waveformCapacity: Int = 600                       // ~6 s at 100 Hz

    // MARK: Internals
    private let model = ChewModel.shared
    private let detector = ChewDetector(config: .fromGenerated())
    private var ringBuffer: [MotionSample] = []
    private var lastInferenceTime: TimeInterval = 0
    private var sessionStartTime: TimeInterval?
    private var currentBite: BiteAccumulator?

    // History accumulator for the meal report
    @Published private(set) var lastSummary: MealSummary?

    var modelState: ChewModelState { model.state }

    init() {
        if case .failed(let message) = model.state {
            errorMessage = message
        }
    }

    func reset() {
        detector.reset()
        ringBuffer.removeAll(keepingCapacity: true)
        waveform.removeAll(keepingCapacity: true)
        bites.removeAll()
        probability = 0
        smoothed = 0
        state = .idle
        lastInferenceTime = 0
        sessionStartTime = nil
        currentBite = nil
        lastSummary = nil
        errorMessage = model.state.isReady ? nil : model.state.message
    }

    /// Feed a single sample. Triggers inference whenever a new stride boundary is crossed.
    @discardableResult
    func push(_ s: MotionSample) -> InferenceTick? {
        if sessionStartTime == nil { sessionStartTime = s.timestamp; lastInferenceTime = s.timestamp - strideSeconds }
        ringBuffer.append(s)
        // Keep buffer small: a bit more than one window at 100 Hz = ~75 samples.
        let maxBuffer = 256
        if ringBuffer.count > maxBuffer { ringBuffer.removeFirst(ringBuffer.count - maxBuffer) }

        // Update waveform display (accel magnitude)
        let mag = sqrt(s.ax * s.ax + s.ay * s.ay + s.az * s.az)
        waveform.append(mag)
        if waveform.count > waveformCapacity { waveform.removeFirst(waveform.count - waveformCapacity) }

        // Inference cadence
        guard s.timestamp - lastInferenceTime >= strideSeconds else { return nil }
        lastInferenceTime = s.timestamp

        // Slice the most-recent windowSeconds of samples
        let cutoff = s.timestamp - windowSeconds
        let window = ringBuffer.filter { $0.timestamp >= cutoff }
        guard window.count >= 30 else { return nil }   // need a minimum

        let fs = FeatureExtractor.estimateSamplingRate(from: window)
        let raw = FeatureExtractor.extractFeatures(from: window, samplingRate: fs)
        let normalized = NormalizationConstants.normalize(raw)
        let prob: Double
        do {
            prob = try model.predictChewProbability(features: normalized)
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }

        // Use shake-guard variant
        let gyroRms = TimeFeatures.rms(TimeFeatures.magnitude(window.map(\.gx), window.map(\.gy), window.map(\.gz)))
        let chewBP = raw[7]
        let lowBP = raw[8]
        let result = detector.update(probability: prob, gyroRms: gyroRms, chewBandPower: chewBP, lowBandPower: lowBP)

        probability = prob
        smoothed = result.smoothedProb
        state = result.state

        // Bite accumulation
        if result.startedEpisode {
            currentBite = BiteAccumulator(startTime: s.timestamp - (sessionStartTime ?? s.timestamp))
        }
        currentBite?.observe(magnitude: mag, timestamp: s.timestamp)
        if result.endedEpisode, let acc = currentBite {
            let endRel = s.timestamp - (sessionStartTime ?? s.timestamp)
            let bite = acc.finalize(endTime: endRel)
            bites.append(bite)
            currentBite = nil
        }

        return InferenceTick(
            timestamp: s.timestamp,
            probability: prob,
            smoothed: result.smoothedProb,
            state: result.state,
            startedEpisode: result.startedEpisode,
            endedEpisode: result.endedEpisode,
            accelMagnitude: mag
        )
    }

    /// Close out any in-flight bite and produce a `MealSummary`.
    func finalizeSummary(label: String) -> MealSummary {
        if let acc = currentBite, let start = sessionStartTime {
            // Force-close the bite at the most recent sample timestamp.
            let endRel = (ringBuffer.last?.timestamp ?? start) - start
            bites.append(acc.finalize(endTime: endRel))
            currentBite = nil
        }
        let total = (ringBuffer.last?.timestamp ?? 0) - (sessionStartTime ?? 0)
        let summary = MealSummary(label: label, totalSeconds: max(total, 0), bites: bites)
        lastSummary = summary
        return summary
    }
}

/// Builds a single `BiteEvent` while a chewing episode is active.
private final class BiteAccumulator {
    let startTime: TimeInterval
    private var lastPeakTime: TimeInterval = -.infinity
    private var lastMag: Double = 0
    private var rising: Bool = false
    private(set) var chewCount: Int = 0
    private(set) var sampleTimestamps: [TimeInterval] = []
    private let minPeriod: Double = NormalizationConstants_minChewPeriodSecOrDefault

    init(startTime: TimeInterval) {
        self.startTime = startTime
    }

    /// Heuristic chew counter — counts magnitude peaks separated by >= minChewPeriodSec.
    /// Used until the two-head phase model ships (see ROADMAP Phase 3).
    func observe(magnitude m: Double, timestamp t: TimeInterval) {
        sampleTimestamps.append(t)
        if rising && m < lastMag {
            // Just crossed a peak.
            if t - lastPeakTime >= minPeriod {
                chewCount += 1
                lastPeakTime = t
            }
            rising = false
        } else if m > lastMag {
            rising = true
        }
        lastMag = m
    }

    func finalize(endTime: TimeInterval) -> BiteEvent {
        BiteEvent(
            startSeconds: startTime,
            endSeconds: endTime,
            chewCount: chewCount
        )
    }
}

/// Avoid hard-linking against the renamed RuntimeConfig stroke constants —
/// fall back to a reasonable default if they aren't present.
private var NormalizationConstants_minChewPeriodSecOrDefault: Double {
    return RuntimeConfig.minChewPeriodSec
}
