//
//  ChewingBoutSegmenter.swift
//  ChewSenseLive
//
//  Aggregates eating-state episodes into honestly named diagnostic bouts.
//  These intervals never stand in for food-entry events.
//

import Foundation

final class ChewingBoutSegmenter {
    let boutMergeGapSec: Double
    let minBoutDurationSec: Double

    private(set) var bouts: [ChewingBout] = []
    private var pendingStart: Double?

    init(
        boutMergeGapSec: Double = RuntimeConfig.boutMergeGapSec,
        minBoutDurationSec: Double = RuntimeConfig.minBoutDurationSec
    ) {
        self.boutMergeGapSec = boutMergeGapSec
        self.minBoutDurationSec = minBoutDurationSec
    }

    func reset() {
        bouts.removeAll(keepingCapacity: true)
        pendingStart = nil
    }

    func episodeStarted(at timestamp: Double) {
        if pendingStart == nil { pendingStart = timestamp }
    }

    @discardableResult
    func episodeEnded(
        at timestamp: Double,
        smoothedProb: Double,
        chewStrokes: [ChewStroke]
    ) -> ChewingBout? {
        guard let start = pendingStart else { return nil }
        pendingStart = nil

        if let last = bouts.last, start - last.endTime < boutMergeGapSec {
            let merged = mergeBout(
                last,
                newEnd: timestamp,
                confidence: smoothedProb,
                chewStrokes: chewStrokes
            )
            bouts[bouts.count - 1] = merged
            return merged
        }

        guard timestamp - start >= minBoutDurationSec else { return nil }
        let strokes = chewStrokes.filter {
            $0.timestamp >= start && $0.timestamp < timestamp
        }
        let bout = ChewingBout(
            startTime: start,
            endTime: timestamp,
            confidence: smoothedProb,
            chewCount: strokes.count,
            chewStrokes: strokes
        )
        bouts.append(bout)
        return bout
    }

    private func mergeBout(
        _ existing: ChewingBout,
        newEnd: Double,
        confidence: Double,
        chewStrokes: [ChewStroke]
    ) -> ChewingBout {
        let mergedEnd = max(existing.endTime, newEnd)
        let strokes = chewStrokes.filter {
            $0.timestamp >= existing.startTime && $0.timestamp < mergedEnd
        }
        return ChewingBout(
            id: existing.id,
            startTime: existing.startTime,
            endTime: mergedEnd,
            confidence: max(existing.confidence, confidence),
            chewCount: strokes.count,
            chewStrokes: strokes
        )
    }
}
