//
//  TemporalWindowBuilder.swift
//  ChewSenseLive
//
//  Deterministic 50 Hz resampling for the v2 temporal CoreML input.
//

import Foundation

struct TemporalModelWindow: Equatable {
    static let sampleCount = 150
    static let channelCount = 7

    /// Channel-major [ax, ay, az, gx, gy, gz, gap-validity].
    let values: [Double]
    let timestamps: [Double]

    var gapCoverage: Double {
        let start = 6 * Self.sampleCount
        guard values.count == Self.channelCount * Self.sampleCount else { return 0 }
        return values[start..<(start + Self.sampleCount)].reduce(0, +)
            / Double(Self.sampleCount)
    }
}

enum TemporalWindowBuilder {
    static func build(
        samples: [MotionSample],
        endTimestamp: Double? = nil,
        sampleRateHz: Double = 50,
        contextSec: Double = 3,
        maxGapSec: Double = 0.08
    ) -> TemporalModelWindow? {
        guard samples.count >= 2, sampleRateHz > 0, contextSec > 0 else { return nil }
        // A clock reset is invalid input, not something to sort into continuity.
        let sorted = samples
        guard maxGapSec > 0, sorted.allSatisfy({ $0.timestamp.isFinite && axisValues($0).allSatisfy(\.isFinite) }) else { return nil }
        for index in 1..<sorted.count {
            guard sorted[index - 1].timestamp < sorted[index].timestamp else { return nil }
        }

        let count = Int((sampleRateHz * contextSec).rounded())
        guard count == TemporalModelWindow.sampleCount else { return nil }
        let end = endTimestamp ?? sorted.last!.timestamp
        guard end.isFinite else { return nil }
        let timestamps = (0..<count).map {
            end - Double(count - 1 - $0) / sampleRateHz
        }
        var values = Array(repeating: 0.0, count: 7 * count)
        var right = 0

        for (gridIndex, timestamp) in timestamps.enumerated() {
            while right < sorted.count && sorted[right].timestamp < timestamp {
                right += 1
            }

            let exact = right < sorted.count
                && abs(sorted[right].timestamp - timestamp) <= 1e-9
            let exactLeft = right > 0 && abs(sorted[right - 1].timestamp - timestamp) <= 1e-9
            let validBracket = right > 0
                && right < sorted.count
                && sorted[right].timestamp - sorted[right - 1].timestamp <= maxGapSec

            let axes: [Double]?
            if exact {
                axes = axisValues(sorted[right])
            } else if exactLeft {
                axes = axisValues(sorted[right - 1])
            } else if validBracket {
                let left = sorted[right - 1]
                let rightSample = sorted[right]
                let fraction = (timestamp - left.timestamp)
                    / (rightSample.timestamp - left.timestamp)
                let lhs = axisValues(left)
                let rhs = axisValues(rightSample)
                axes = zip(lhs, rhs).map { $0 + fraction * ($1 - $0) }
            } else {
                axes = nil
            }

            if let axes {
                for channel in 0..<6 {
                    values[channel * count + gridIndex] = axes[channel]
                }
                values[6 * count + gridIndex] = 1
            }
        }
        return TemporalModelWindow(values: values, timestamps: timestamps)
    }

    private static func axisValues(_ sample: MotionSample) -> [Double] {
        [sample.ax, sample.ay, sample.az, sample.gx, sample.gy, sample.gz]
    }
}
