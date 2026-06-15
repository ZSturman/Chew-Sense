//
//  ChewPhaseCounter.swift
//  ChewSenseDebuggingModel
//
//  Streaming chew-cycle counter driven by the two-head ChewNet's
//  `chew_phase_sin` output. One full chew cycle corresponds to one
//  period of sin (zero → +peak → zero → -peak → zero). We emit one
//  cycle on every negative-to-positive zero crossing of the smoothed
//  sin signal, gated by `minChewPeriodSec` so noise doesn't double-
//  count.
//
//  Instantaneous chew rate (Hz) is the reciprocal of the median
//  interval between recent crossings.
//
//  This counter is independent of `ChewStrokeCounter` (which operates
//  on the 1–3 Hz bandpower scalar) and is only meaningful when
//  `NormalizationConstants.MODE == "two_head"`.

import Foundation

final class ChewPhaseCounter {
    /// Minimum spacing between two emitted chew cycles, in seconds.
    let minChewPeriodSec: Double

    /// Number of recent intervals retained for rate estimation.
    let rateWindow: Int

    private(set) var chewCount: Int = 0
    private(set) var chewRateHz: Double = 0
    private(set) var lastEmittedTimestamp: Double?

    private var prevSin: Double = 0
    private var prevTimestamp: Double = -.infinity
    private var lastCrossingTime: Double = -.infinity
    private var recentIntervals: [Double] = []

    init(minChewPeriodSec: Double = 0.20, rateWindow: Int = 5) {
        self.minChewPeriodSec = minChewPeriodSec
        self.rateWindow = max(1, rateWindow)
    }

    func reset() {
        chewCount = 0
        chewRateHz = 0
        prevSin = 0
        prevTimestamp = -.infinity
        lastCrossingTime = -.infinity
        recentIntervals.removeAll(keepingCapacity: true)
        lastEmittedTimestamp = nil
    }

    /// Break phase continuity while preserving the session-level count.
    func pause() {
        prevSin = 0
        prevTimestamp = -.infinity
    }

    /// Push the next `chew_phase_sin` sample produced by the model.
    /// Returns `true` if a new chew cycle was counted on this sample.
    @discardableResult
    func push(timestamp: Double, sin sinValue: Double) -> Bool {
        defer {
            prevSin = sinValue
            prevTimestamp = timestamp
        }

        guard prevTimestamp.isFinite else { return false }

        // Negative-to-positive zero crossing of sin marks the start of a
        // new chew cycle (mouth begins opening).
        let crossed = prevSin <= 0 && sinValue > 0
        if !crossed { return false }

        // Linear interpolation gives a sub-sample crossing time so the
        // emitted chew-rate isn't quantized by the model step.
        let denom = sinValue - prevSin
        let frac = denom > 0 ? (-prevSin) / denom : 0
        let crossingTime = prevTimestamp + frac * (timestamp - prevTimestamp)

        if lastCrossingTime.isFinite {
            let dt = crossingTime - lastCrossingTime
            if dt < minChewPeriodSec { return false }
            recentIntervals.append(dt)
            if recentIntervals.count > rateWindow {
                recentIntervals.removeFirst(recentIntervals.count - rateWindow)
            }
            chewRateHz = 1.0 / median(recentIntervals)
        }

        lastCrossingTime = crossingTime
        lastEmittedTimestamp = crossingTime
        chewCount += 1
        return true
    }

    private func median(_ xs: [Double]) -> Double {
        guard !xs.isEmpty else { return .infinity }
        let sorted = xs.sorted()
        let n = sorted.count
        return n % 2 == 1 ? sorted[n / 2] : 0.5 * (sorted[n / 2 - 1] + sorted[n / 2])
    }
}
