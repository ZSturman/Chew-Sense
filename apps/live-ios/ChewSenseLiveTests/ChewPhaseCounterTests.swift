//
//  ChewPhaseCounterTests.swift
//  ChewSenseLiveTests
//
//  Tests the streaming chew-cycle counter that consumes the two-head
//  ChewNet's `chew_phase_sin` output.
//

import XCTest
@testable import ChewSenseLive

final class ChewPhaseCounterTests: XCTestCase {

    /// A pure 2 Hz sine wave fed at 50 Hz for 2 seconds should produce
    /// 4 chew cycles and an instantaneous rate of ~2 Hz.
    func testCountsSineCyclesAtKnownRate() {
        let counter = ChewPhaseCounter(minChewPeriodSec: 0.10, rateWindow: 5)
        let fs = 50.0
        let f = 2.0
        let duration = 2.0
        let n = Int(fs * duration)

        for i in 0..<n {
            let t = Double(i) / fs
            let s = sin(2 * .pi * f * t)
            counter.push(timestamp: t, sin: s)
        }

        XCTAssertEqual(counter.chewCount, 4)
        XCTAssertEqual(counter.chewRateHz, 2.0, accuracy: 0.05)
    }

    /// Crossings spaced tighter than `minChewPeriodSec` must be ignored.
    func testMinPeriodGate() {
        let counter = ChewPhaseCounter(minChewPeriodSec: 0.30)

        // Two rapid crossings separated by 0.05 s; only first counts.
        counter.push(timestamp: 0.00, sin: -0.5)
        counter.push(timestamp: 0.01, sin: 0.5)   // crossing #1
        counter.push(timestamp: 0.04, sin: -0.5)
        counter.push(timestamp: 0.06, sin: 0.5)   // rejected (Δt < 0.30)
        counter.push(timestamp: 0.50, sin: -0.5)
        counter.push(timestamp: 0.55, sin: 0.5)   // crossing #2

        XCTAssertEqual(counter.chewCount, 2)
    }

    func testResetRestoresInitialState() {
        let counter = ChewPhaseCounter()
        counter.push(timestamp: 0.0, sin: -1)
        counter.push(timestamp: 0.5, sin: 1)
        counter.reset()
        XCTAssertEqual(counter.chewCount, 0)
        XCTAssertEqual(counter.chewRateHz, 0)
    }
}
