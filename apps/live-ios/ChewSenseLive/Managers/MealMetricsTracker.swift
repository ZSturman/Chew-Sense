//
//  MealMetricsTracker.swift
//  ChewSenseLive
//
//  Honest cadence-only live metrics. Chewing bouts are retained as diagnostic
//  telemetry, but never presented or serialized as bites.
//

import Combine
import Foundation

@MainActor
final class MealMetricsTracker: ObservableObject {
    @Published private(set) var chewCount = 0
    @Published private(set) var medianInterChewIntervalSec: Double?
    @Published private(set) var cadencePerMinute: Double?
    @Published private(set) var confidenceCoverage = 0.0
    @Published private(set) var uncertainTimeSec = 0.0
    @Published private(set) var latestCue: CadenceCue?
    @Published private(set) var texture: MealTexture = .unknown
    @Published private(set) var chewEvents: [ChewStroke] = []

    private(set) var diagnosticBouts: [ChewingBout] = []
    private var intervals: [Double] = []
    private var confidentInferenceTimeSec = 0.0
    private var totalInferenceTimeSec = 0.0
    private var coach = CadenceCoach(minimumMedianIntervalSec: nil)

    func configure(texture: MealTexture, goals: PersonalizedCoachingGoals?) {
        self.texture = texture
        coach = CadenceCoach(
            minimumMedianIntervalSec: goals?.minimumMedianInterChewIntervalSec
        )
    }

    func reset() {
        chewCount = 0
        medianInterChewIntervalSec = nil
        cadencePerMinute = nil
        confidenceCoverage = 0
        uncertainTimeSec = 0
        latestCue = nil
        chewEvents.removeAll(keepingCapacity: true)
        diagnosticBouts.removeAll(keepingCapacity: true)
        intervals.removeAll(keepingCapacity: true)
        confidentInferenceTimeSec = 0
        totalInferenceTimeSec = 0
        coach.reset()
    }

    /// Record a learned event-head chew. Legacy heuristic events may be
    /// exported for diagnostics but must not call this coaching path.
    @discardableResult
    func recordChew(_ event: ChewStroke) -> CadenceCue? {
        guard event.source == .model,
              event.confidence >= RuntimeConfig.confidentEventThreshold else {
            return nil
        }
        if let previous = chewEvents.last, event.timestamp > previous.timestamp {
            intervals.append(event.timestamp - previous.timestamp)
        }
        chewEvents.append(event)
        chewCount = chewEvents.count
        if !intervals.isEmpty {
            let median = Self.percentile(intervals, 0.5)
            medianInterChewIntervalSec = median
            cadencePerMinute = median > 0 ? 60 / median : nil
        }
        let cue = coach.recordChew(
            timestamp: event.timestamp,
            confidence: event.confidence
        )
        if let cue { latestCue = cue }
        return cue
    }

    func recordInference(stepDurationSec: Double, confident: Bool) {
        let duration = max(0, stepDurationSec)
        totalInferenceTimeSec += duration
        if confident {
            confidentInferenceTimeSec += duration
        } else {
            uncertainTimeSec += duration
        }
        confidenceCoverage = totalInferenceTimeSec > 0
            ? confidentInferenceTimeSec / totalInferenceTimeSec
            : 0
    }

    func recordDiagnosticBout(_ bout: ChewingBout) {
        if let index = diagnosticBouts.firstIndex(where: { $0.id == bout.id }) {
            diagnosticBouts[index] = bout
        } else {
            diagnosticBouts.append(bout)
        }
    }

    func clearCue() {
        latestCue = nil
    }

    func snapshotJSON(capabilities: ModelCapabilities = .bundled) -> [String: Any] {
        var payload: [String: Any] = [
            "schema_version": "2.0.0",
            "texture": texture.rawValue,
            "detected_chews": chewCount,
            "cadence_distribution_sec": cadenceDistributionJSON(),
            "confidence_coverage": confidenceCoverage,
            "time_excluded_uncertain_sec": uncertainTimeSec,
            "chewing_bout_count_diagnostic": diagnosticBouts.count,
            "chew_event_model_promoted": capabilities.temporalChewEventHead,
            "food_entry_coaching_available": capabilities.foodEntryCoachingEnabled,
        ]
        if let medianInterChewIntervalSec {
            payload["median_inter_chew_interval_sec"] = medianInterChewIntervalSec
        }
        if let cadencePerMinute {
            payload["cadence_per_minute"] = cadencePerMinute
        }
        // This block remains unreachable until an independently promoted
        // food-entry head provides real groupings. No bout-derived values are
        // substituted here.
        if capabilities.foodEntryCoachingEnabled {
            payload["food_entry_grouping_status"] = "capability_promoted"
        }
        return payload
    }

    private func cadenceDistributionJSON() -> [String: Double] {
        guard !intervals.isEmpty else { return [:] }
        return [
            "p10": Self.percentile(intervals, 0.10),
            "p25": Self.percentile(intervals, 0.25),
            "p50": Self.percentile(intervals, 0.50),
            "p75": Self.percentile(intervals, 0.75),
            "p90": Self.percentile(intervals, 0.90),
        ]
    }

    private static func percentile(_ values: [Double], _ quantile: Double) -> Double {
        let sorted = values.sorted()
        let position = Double(sorted.count - 1) * min(1, max(0, quantile))
        let lower = Int(floor(position))
        let upper = Int(ceil(position))
        if lower == upper { return sorted[lower] }
        let fraction = position - Double(lower)
        return sorted[lower] + fraction * (sorted[upper] - sorted[lower])
    }
}
