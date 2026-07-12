//
//  ChewStrokeCounter.swift
//  ChewSenseLive
//
//  Streaming peak detector for individual chew strokes.
//
//  Operates on the 1–3 Hz acceleration bandpower scalar (feature index 7
//  in `FeatureExtractor.featureNames` — `chew_bandpower_1_3hz`). Strokes
//  are emitted at local maxima that exceed a quantile-based threshold
//  AND are spaced at least `RuntimeConfig.minChewPeriodSec` apart.
//
//  This is intentionally light-weight: a small ring buffer of recent
//  bandpower samples is kept (default 4 s @ 10 Hz feature-step rate ≈
//  40 samples) and the threshold is `max(meanThresh, qtl(0.6))`.
//
//  Outputs are runtime-only `ChewStroke`s with `.heuristic` source —
//  the BiteReviewView lets the user upgrade them to `.confirmed` later.

import Foundation

final class ChewStrokeCounter {
    /// Minimum spacing between strokes (seconds).
    let minChewPeriodSec: Double

    /// How many recent (timestamp, value) bandpower samples to retain.
    private let bufferCapacity: Int

    /// Quantile used to set the dynamic peak threshold (0..1).
    private let thresholdQuantile: Double

    private var buffer: [(t: Double, v: Double)] = []
    private(set) var strokes: [ChewStroke] = []

    private var prevValue: Double = 0
    private var prevPrevValue: Double = 0
    private var lastEmitTime: Double = -.infinity

    init(
        minChewPeriodSec: Double = RuntimeConfig.minChewPeriodSec,
        bufferCapacity: Int = 60,
        thresholdQuantile: Double = 0.6
    ) {
        self.minChewPeriodSec = minChewPeriodSec
        self.bufferCapacity = bufferCapacity
        self.thresholdQuantile = thresholdQuantile
    }

    func reset() {
        buffer.removeAll(keepingCapacity: true)
        strokes.removeAll(keepingCapacity: true)
        prevValue = 0
        prevPrevValue = 0
        lastEmitTime = -.infinity
    }

    /// Push the next bandpower sample. `timestamp` is monotonically
    /// increasing seconds (the window-end time is fine).
    /// Returns the newly emitted stroke, if any.
    @discardableResult
    func push(timestamp: Double, bandpower: Double) -> ChewStroke? {
        // Maintain ring buffer.
        buffer.append((timestamp, bandpower))
        if buffer.count > bufferCapacity {
            buffer.removeFirst(buffer.count - bufferCapacity)
        }

        // Need at least 3 samples to detect a local maximum at the
        // mid-point (prevPrev, prev, curr) where prev > prevPrev and
        // prev > curr.
        defer {
            prevPrevValue = prevValue
            prevValue = bandpower
        }
        guard buffer.count >= 3 else { return nil }

        let mid = prevValue
        let isLocalMax = mid > prevPrevValue && mid > bandpower
        guard isLocalMax else { return nil }

        // Quantile threshold from the recent window.
        let values = buffer.map { $0.v }.sorted()
        let qIdx = max(0, min(values.count - 1, Int(Double(values.count - 1) * thresholdQuantile)))
        let qThresh = values[qIdx]
        let mean = values.reduce(0, +) / Double(values.count)
        let threshold = max(qThresh, mean)
        guard mid >= threshold else { return nil }

        // Locate the timestamp of the mid sample.
        let midTs = buffer[buffer.count - 2].t
        guard midTs - lastEmitTime >= minChewPeriodSec else { return nil }
        lastEmitTime = midTs

        // Confidence ~ prominence above mean, clamped to [0,1].
        let prominence = mid - mean
        let scale = max(qThresh - mean, 1e-6)
        let conf = max(0.0, min(1.0, prominence / (2.0 * scale)))
        let stroke = ChewStroke(timestamp: midTs, confidence: conf, source: .heuristic)
        strokes.append(stroke)
        return stroke
    }

    /// Strokes whose timestamp falls within `[start, end)`.
    func strokes(in start: Double, _ end: Double) -> [ChewStroke] {
        strokes.filter { $0.timestamp >= start && $0.timestamp < end }
    }
}
