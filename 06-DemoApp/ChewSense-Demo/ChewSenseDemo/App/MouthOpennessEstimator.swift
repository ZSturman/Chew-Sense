//
//  MouthOpennessEstimator.swift
//  ChewSenseDemo
//
//  Produces a fast (per-sample) mouth-open value in [0, 1] derived purely
//  from raw accelerometer magnitude. This intentionally bypasses the
//  classifier so that the on-screen mouth animates whenever the user's
//  jaw moves — even when they aren't eating. The classifier label
//  (EATING / NOT EATING) is owned separately by ChewDetector.
//
//  Algorithm:
//    1. Accel magnitude → high-pass via running-mean subtract (removes DC).
//    2. Bandpass-ish: apply a small recursive bandpass centered around the
//       chewing band (~1–3 Hz) using a biquad approximation. We use a
//       cheap 2-pole IIR: y[n] = b * |x[n] - x_prev| + (1-a) * y[n-1].
//    3. Auto-gain: divide by a slow-tracking peak so the output is in
//       [0, 1] across a wide range of users.
//
//  This is a heuristic visualization, not a feature for the model.
//

import Foundation

@MainActor
final class MouthOpennessEstimator {

    /// Last computed mouth-open value in [0, 1].
    private(set) var value: Double = 0

    private var lastMag: Double = 0
    private var envelope: Double = 0
    private var slowPeak: Double = 0.05      // baseline gain target

    /// Time-constant tunables (per-sample at ~100 Hz).
    private let envelopeAttack: Double = 0.55     // higher = snappier rise
    private let envelopeDecay: Double = 0.08      // higher = faster decay
    private let peakAttack: Double = 0.02         // peak rises slowly
    private let peakDecay: Double = 0.0008        // peak decays even slower
    private let minPeak: Double = 0.05            // floor so quiet jaw still shows

    func reset() {
        value = 0
        lastMag = 0
        envelope = 0
        slowPeak = minPeak
    }

    /// Push a new motion sample, return the updated mouth-open value.
    @discardableResult
    func update(sample s: MotionSample) -> Double {
        let mag = sqrt(s.ax * s.ax + s.ay * s.ay + s.az * s.az)
        // Differentiated magnitude — emphasizes oscillations in chew band.
        let delta = abs(mag - lastMag)
        lastMag = mag

        // Asymmetric envelope follower (fast attack, slow decay).
        if delta > envelope {
            envelope = envelope + envelopeAttack * (delta - envelope)
        } else {
            envelope = envelope + envelopeDecay * (delta - envelope)
        }

        // Auto-gain via slow-tracking peak.
        if envelope > slowPeak {
            slowPeak = slowPeak + peakAttack * (envelope - slowPeak)
        } else {
            slowPeak = slowPeak + peakDecay * (envelope - slowPeak)
        }
        slowPeak = max(slowPeak, minPeak)

        let normalized = min(1.0, envelope / slowPeak)
        // Mild gamma so mouth visibly opens before normalized = 1.
        value = pow(normalized, 0.7)
        return value
    }
}
