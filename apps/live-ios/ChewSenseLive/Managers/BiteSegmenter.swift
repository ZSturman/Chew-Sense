//
//  BiteSegmenter.swift
//  ChewSenseLive
//
//  Aggregates `ChewDetector` episode start/end events into bites,
//  applying merge & duration rules taken from `RuntimeConfig`:
//
//    * Two episodes whose start-to-prev-end gap is below
//      `minGapSec` are merged into one bite.
//    * Episodes shorter than `minBiteDurationSec` are dropped as noise.
//
//  The segmenter holds onto an `inProgress` candidate while the
//  detector reports `.chewing`; on episode end it either commits the
//  bout (and links the supplied phase-derived chew cycles) or absorbs it
//  into a previously emitted bout
//  if the gap is short enough.

import Foundation

final class BiteSegmenter {
    let minGapSec: Double
    let minBiteDurationSec: Double

    private(set) var bites: [BiteEvent] = []
    private var pendingStart: Double?

    init(
        minGapSec: Double = RuntimeConfig.minGapSec,
        minBiteDurationSec: Double = RuntimeConfig.minBiteDurationSec
    ) {
        self.minGapSec = minGapSec
        self.minBiteDurationSec = minBiteDurationSec
    }

    func reset() {
        bites.removeAll(keepingCapacity: true)
        pendingStart = nil
    }

    /// Call when the detector reports `startedEpisode == true`.
    func episodeStarted(at timestamp: Double) {
        // If a previous bite ended very recently, we'll try to merge in
        // `episodeEnded` — for now, just remember the candidate start.
        if pendingStart == nil {
            pendingStart = timestamp
        }
    }

    /// Call when the detector reports `endedEpisode == true`.
    /// Returns the bite that was just committed, if any (post-merge,
    /// post-min-duration check).
    @discardableResult
    func episodeEnded(
        at timestamp: Double,
        smoothedProb: Double,
        chewStrokes: [ChewStroke]
    ) -> BiteEvent? {
        guard let start = pendingStart else { return nil }
        pendingStart = nil

        // Merge with previous bite if the gap is too small.
        if let last = bites.last, start - last.endTime < minGapSec {
            let merged = mergeBite(last, newStart: start, newEnd: timestamp,
                                   confidence: smoothedProb,
                                   chewStrokes: chewStrokes)
            bites[bites.count - 1] = merged
            return merged
        }

        let duration = timestamp - start
        guard duration >= minBiteDurationSec else { return nil }

        let strokes = chewStrokes.filter { $0.timestamp >= start && $0.timestamp < timestamp }
        let bite = BiteEvent(
            startTime: start,
            endTime: timestamp,
            confidence: smoothedProb,
            chewCount: strokes.count,
            chewStrokes: strokes
        )
        bites.append(bite)
        return bite
    }

    private func mergeBite(
        _ existing: BiteEvent,
        newStart: Double,
        newEnd: Double,
        confidence: Double,
        chewStrokes: [ChewStroke]
    ) -> BiteEvent {
        let mergedEnd = max(existing.endTime, newEnd)
        let strokes = chewStrokes.filter {
            $0.timestamp >= existing.startTime && $0.timestamp < mergedEnd
        }
        return BiteEvent(
            id: existing.id,
            startTime: existing.startTime,
            endTime: mergedEnd,
            confidence: max(existing.confidence, confidence),
            chewCount: strokes.count,
            chewStrokes: strokes
        )
    }
}
