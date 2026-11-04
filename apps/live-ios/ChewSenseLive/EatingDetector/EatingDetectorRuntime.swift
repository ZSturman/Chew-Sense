//
//  EatingDetectorRuntime.swift
//  ChewSenseLive
//
//  Streaming motion-only eating detector: buffers AirPods samples, evaluates a
//  3 s window every `stride_s` seconds, runs the exported gradient-boosted
//  trees and feeds the probability to the same EMA + hysteresis state machine
//  as `ChewDetector`. Matches `pipeline/eating/runtime.run_stream`.
//

import Foundation

/// Gradient-boosted trees from the specification.
struct EatingTreeEnsemble {
    let spec: EatingDetectorSpec.Model

    init(_ spec: EatingDetectorSpec.Model) {
        self.spec = spec
    }

    /// baseline + leaf values summed in tree order; `feature <= threshold` goes left.
    func logit(_ features: [Float]) -> Double {
        var total = spec.baselineLogit
        for tree in spec.trees {
            var node = 0
            while tree.feature[node] >= 0 {
                let value = Double(features[tree.feature[node]])
                let goLeft = value.isNaN ? tree.missingLeft[node] : value <= tree.threshold[node]
                node = goLeft ? tree.left[node] : tree.right[node]
            }
            total += tree.value[node]
        }
        return total
    }

    func probability(_ features: [Float]) -> Double {
        1.0 / (1.0 + exp(-logit(features)))
    }
}

/// One evaluated window.
struct EatingDetectorOutput {
    let windowEnd: Double
    let validFraction: Double
    /// Model probability; nil when too little motion was measured.
    let probability: Double?
    /// Probability passed to the decoder (invalid windows contribute the spec's value, 0).
    let decoderInput: Double
    let smoothed: Double
    let chewing: Bool
    let startedBout: Bool
    let endedBout: Bool
    let features: [Float]?
}

/// Streaming runtime. Not thread-safe; call from one queue.
final class EatingDetectorRuntime {
    let spec: EatingDetectorSpec
    private let ensemble: EatingTreeEnsemble
    private let detector: ChewDetector
    private var buffer: [EatingMotionSample] = []
    private var firstEnd: Double?
    private var evaluated = 0
    private var lastTimestamp: Double?
    private(set) var droppedSamples = 0
    /// Keep features in each output (parity checks); off in Live.
    var recordsFeatures = false

    init(spec: EatingDetectorSpec, firstWindowEnd: Double? = nil) {
        self.spec = spec
        self.ensemble = EatingTreeEnsemble(spec.model)
        let decoder = spec.decision.decoder
        self.detector = ChewDetector(config: ChewDetectorConfig(
            alpha: decoder.alpha,
            highThreshold: decoder.highThreshold,
            lowThreshold: decoder.lowThreshold,
            minStartWindows: decoder.minStartWindows,
            minEndWindows: decoder.minEndWindows,
            shakeGyroRmsThreshold: 0,
            shakeChewToLowRatioMin: 0
        ))
        self.firstEnd = firstWindowEnd
    }

    var modelID: String { spec.modelId }

    func reset() {
        buffer.removeAll()
        firstEnd = nil
        evaluated = 0
        lastTimestamp = nil
        droppedSamples = 0
        detector.reset()
    }

    /// The end time of the n-th window: first + n * stride (no accumulated drift).
    private func end(at index: Int) -> Double? {
        firstEnd.map { $0 + spec.decision.strideS * Double(index) }
    }

    /// Add one sample; returns every window that became evaluable.
    @discardableResult
    func push(_ sample: EatingMotionSample) -> [EatingDetectorOutput] {
        guard sample.timestamp.isFinite, sample.values.allSatisfy({ $0.isFinite }) else {
            droppedSamples += 1
            return []
        }
        if let lastTimestamp, sample.timestamp <= lastTimestamp {
            // Repeated or backward rows are dropped, never reordered.
            droppedSamples += 1
            return []
        }
        lastTimestamp = sample.timestamp
        buffer.append(sample)
        if firstEnd == nil { firstEnd = sample.timestamp + spec.input.windowS }

        var outputs: [EatingDetectorOutput] = []
        while let windowEnd = end(at: evaluated), sample.timestamp >= windowEnd {
            outputs.append(evaluate(windowEnd: windowEnd))
            evaluated += 1
        }
        prune()
        return outputs
    }

    private func evaluate(windowEnd: Double) -> EatingDetectorOutput {
        // Samples up to the first one at or after the window end.
        var stop = buffer.count
        while stop > 1 && buffer[stop - 2].timestamp >= windowEnd { stop -= 1 }
        let window = EatingFeatureExtractor.resample(buffer[0..<stop], end: windowEnd, spec: spec.input)
        let validFraction = window.validFraction
        var probability: Double?
        var features: [Float]?
        if validFraction >= spec.input.minValidFraction {
            let values = EatingFeatureExtractor.features(of: window, spec: spec)
            probability = ensemble.probability(values)
            features = values
        }
        let input = probability ?? spec.decision.invalidWindowProbability
        let result = detector.update(probability: input)
        return EatingDetectorOutput(
            windowEnd: windowEnd,
            validFraction: validFraction,
            probability: probability,
            decoderInput: input,
            smoothed: result.smoothedProb,
            chewing: result.state == .chewing,
            startedBout: result.startedEpisode,
            endedBout: result.endedEpisode,
            features: recordsFeatures ? features : nil
        )
    }

    /// Keep one sample before the next window's first grid point (plus margin).
    private func prune() {
        guard let next = end(at: evaluated) else { return }
        let keepFrom = next - spec.input.windowS - 0.5
        var drop = 0
        while drop + 1 < buffer.count && buffer[drop + 1].timestamp < keepFrom { drop += 1 }
        if drop > 256 || (drop > 0 && buffer.count > 2048) {
            buffer.removeFirst(drop)
        }
    }
}
