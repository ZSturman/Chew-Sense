//
//  ChewEventDecoder.swift
//  ChewSenseLive
//
//  Streaming decoder for the v2 temporal event head. This intentionally
//  mirrors pipeline/temporal.py; phase is never used as a chew counter.
//

import Foundation

struct TemporalInferenceOutput: Equatable {
    let timestamp: Double
    let eatingLogit: Double
    let chewEventLogit: Double
    let phaseSin: Double
    let phaseCos: Double
    let phaseConfidence: Double
    let gapCoverage: Double

    var eatingProbability: Double { Self.sigmoid(eatingLogit) }
    var chewEventProbability: Double { Self.sigmoid(chewEventLogit) }

    private static func sigmoid(_ value: Double) -> Double {
        if value >= 0 {
            let z = exp(-value)
            return 1 / (1 + z)
        }
        let z = exp(value)
        return z / (1 + z)
    }
}

final class ChewEventDecoder {
    let eventThreshold: Double
    let eatingThreshold: Double
    let minGapCoverage: Double
    let refractorySec: Double

    private(set) var rawOutputs: [TemporalInferenceOutput] = []
    private(set) var decodedEvents: [ChewStroke] = []
    private var previousPrevious: TemporalInferenceOutput?
    private var previous: TemporalInferenceOutput?
    private var lastEventTimestamp = -Double.infinity

    init(
        eventThreshold: Double = RuntimeConfig.chewEventThreshold,
        eatingThreshold: Double = 0.5,
        minGapCoverage: Double = RuntimeConfig.minGapCoverage,
        refractorySec: Double = RuntimeConfig.eventRefractorySec
    ) {
        self.eventThreshold = eventThreshold
        self.eatingThreshold = eatingThreshold
        self.minGapCoverage = minGapCoverage
        self.refractorySec = refractorySec
    }

    func reset() {
        rawOutputs.removeAll(keepingCapacity: true)
        decodedEvents.removeAll(keepingCapacity: true)
        previousPrevious = nil
        previous = nil
        lastEventTimestamp = -Double.infinity
    }

    /// A one-step lookahead confirms a local maximum. The returned event's
    /// timestamp is the peak time, not the later confirmation time.
    @discardableResult
    func push(_ output: TemporalInferenceOutput) -> ChewStroke? {
        if let last = rawOutputs.last, output.timestamp <= last.timestamp {
            return nil
        }
        rawOutputs.append(output)

        var emitted: ChewStroke?
        if let before = previousPrevious, let peak = previous {
            let probability = peak.chewEventProbability
            let isLocalPeak = probability >= before.chewEventProbability
                && probability > output.chewEventProbability
            let eligible = isLocalPeak
                && probability >= eventThreshold
                && peak.eatingProbability >= eatingThreshold
                && peak.gapCoverage >= minGapCoverage
                && peak.timestamp - lastEventTimestamp >= refractorySec
            if eligible {
                let event = ChewStroke(
                    timestamp: peak.timestamp,
                    confidence: probability,
                    source: .model
                )
                decodedEvents.append(event)
                lastEventTimestamp = peak.timestamp
                emitted = event
            }
        }
        previousPrevious = previous
        previous = output
        return emitted
    }
}
