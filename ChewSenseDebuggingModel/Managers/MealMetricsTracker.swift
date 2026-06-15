//
//  MealMetrics.swift
//  ChewSenseDebuggingModel
//
//  Live aggregator for motion-only meal metrics.
//
//  Subscribes to a stream of committed `BiteEvent`s and exposes
//  rolling/aggregate metrics suitable for a real-time UI:
//
//    * biteCount            — total committed bites this session
//    * chewCount            — total chew strokes across all bites
//    * avgChewsPerBite      — mean of `chewCount` per bite
//    * biteRatePerMinute    — bites in the last `biteRateWindowSec`
//                             scaled to per-minute
//    * lastInterBiteInterval — gap between previous and most-recent bite
//    * rushedBiteCount      — bites whose `chewCount` is below
//                             `RuntimeConfig.rushedChewsPerBite`
//
//  This type is `ObservableObject` so SwiftUI views can bind to it
//  directly. All mutations happen on the main actor.

import Combine
import Foundation

@MainActor
final class MealMetricsTracker: ObservableObject {
    @Published private(set) var biteCount: Int = 0
    @Published private(set) var chewCount: Int = 0
    @Published private(set) var avgChewsPerBite: Double = 0
    @Published private(set) var biteRatePerMinute: Double = 0
    @Published private(set) var lastInterBiteIntervalSec: Double = 0
    @Published private(set) var rushedBiteCount: Int = 0
    @Published private(set) var bites: [BiteEvent] = []

    private let biteRateWindowSec: Double
    private let rushedChewsPerBite: Int

    init(
        biteRateWindowSec: Double = RuntimeConfig.biteRateWindowSec,
        rushedChewsPerBite: Int = RuntimeConfig.rushedChewsPerBite
    ) {
        self.biteRateWindowSec = biteRateWindowSec
        self.rushedChewsPerBite = rushedChewsPerBite
    }

    func reset() {
        bites.removeAll(keepingCapacity: true)
        biteCount = 0
        chewCount = 0
        avgChewsPerBite = 0
        biteRatePerMinute = 0
        lastInterBiteIntervalSec = 0
        rushedBiteCount = 0
    }

    /// Record a newly committed (or merged) bite. If the bite already
    /// exists by `id`, it is updated in place rather than appended.
    func record(_ bite: BiteEvent, now: Double) {
        if let idx = bites.firstIndex(where: { $0.id == bite.id }) {
            bites[idx] = bite
        } else {
            if let prev = bites.last {
                lastInterBiteIntervalSec = max(0, bite.startTime - prev.startTime)
            }
            bites.append(bite)
        }
        recompute(now: now)
    }

    private func recompute(now: Double) {
        biteCount = bites.count
        let chews = bites.reduce(0) { $0 + $1.chewCount }
        chewCount = chews
        avgChewsPerBite = bites.isEmpty ? 0 : Double(chews) / Double(bites.count)
        rushedBiteCount = bites.filter { $0.chewCount < rushedChewsPerBite }.count

        let cutoff = now - biteRateWindowSec
        let inWindow = bites.filter { $0.startTime >= cutoff }
        let span = max(biteRateWindowSec, 1.0)
        biteRatePerMinute = Double(inWindow.count) * 60.0 / span
    }

    /// Snapshot of the metrics suitable for serializing to
    /// `meal_metrics.json` in the field-test bundle.
    func snapshotJSON() -> [String: Any] {
        let totalDuration: Double
        if let first = bites.first, let last = bites.last {
            totalDuration = max(0, last.endTime - first.startTime)
        } else {
            totalDuration = 0
        }
        let totalChewing = bites.reduce(0.0) { $0 + $1.duration }

        return [
            "bite_count": biteCount,
            "chew_count": chewCount,
            "avg_chews_per_bite": avgChewsPerBite,
            "bite_rate_per_minute": biteRatePerMinute,
            "last_inter_bite_interval_sec": lastInterBiteIntervalSec,
            "rushed_bite_count": rushedBiteCount,
            "total_duration": totalDuration,
            "total_chewing_time": totalChewing,
            "eating_efficiency": totalDuration > 0 ? totalChewing / totalDuration : 0,
        ]
    }
}
