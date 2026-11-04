//
//  EatingFeatureExtractor.swift
//  ChewSenseLive
//
//  Resampling and the 80 interpretable window features of the exported eating
//  detector, mirroring `pipeline/temporal.resample_motion_window` and
//  `pipeline/eating/features.compute_features` operation by operation:
//  numpy.interp's slope formula, float32 storage, numpy's pairwise summation
//  for every reduction, |X|^2 via hypot. Only the DFT differs from numpy's
//  pocketfft (about 1e-14 relative); `chewsense-motion eating-parity` measures
//  the effect on recorded sessions.
//

import Foundation

/// One AirPods sample: seconds and six float32 axes (ax, ay, az, gx, gy, gz).
struct EatingMotionSample {
    let timestamp: Double
    let values: [Float]

    init(timestamp: Double, ax: Double, ay: Double, az: Double, gx: Double, gy: Double, gz: Double) {
        self.timestamp = timestamp
        self.values = [Float(ax), Float(ay), Float(az), Float(gx), Float(gy), Float(gz)]
    }

    init(timestamp: Double, values: [Float]) {
        precondition(values.count == 6, "six axes required")
        self.timestamp = timestamp
        self.values = values
    }
}

/// A resampled window: six float32 channels plus the validity channel.
struct EatingWindow {
    /// Channel-major [ax, ay, az, gx, gy, gz] values, `samples` per channel.
    let channels: [[Float]]
    let validity: [Float]

    /// float32(valid samples) / float32(samples), as numpy computed it.
    var validFraction: Double {
        let count = validity.reduce(Float(0), +)
        return Double(count / Float(validity.count))
    }
}

enum EatingFeatureExtractor {

    // MARK: Resampling

    /// Resample samples (strictly increasing timestamps) onto the window grid
    /// ending at `end`. Samples after the first one at or past `end` are
    /// irrelevant and may be absent.
    static func resample(
        _ samples: ArraySlice<EatingMotionSample>,
        end: Double,
        spec: EatingDetectorSpec.Input
    ) -> EatingWindow {
        let count = spec.windowSamples
        var channels = Array(repeating: Array(repeating: Float(0), count: count), count: 6)
        var validity = Array(repeating: Float(0), count: count)
        let base = samples.startIndex
        let n = samples.count
        guard n >= 2 else { return EatingWindow(channels: channels, validity: validity) }
        let rate = Double(spec.sampleRateHz)
        var right = 0
        for index in 0..<count {
            let grid = end - Double(count - 1 - index) / rate
            while right < n && samples[base + right].timestamp < grid { right += 1 }
            let leftIndex = min(max(right - 1, 0), n - 1)
            let rightIndex = min(max(right, 0), n - 1)
            let tLeft = samples[base + leftIndex].timestamp
            let tRight = samples[base + rightIndex].timestamp
            let inside = right > 0 && right < n
            var valid = inside && (tRight - tLeft) <= spec.maxGapS
            let exact = right < n && abs(tRight - grid) <= spec.exactMatchToleranceS
            let exactLeft = right > 0 && abs(tLeft - grid) <= spec.exactMatchToleranceS
            valid = valid || exact || exactLeft
            guard valid else { continue }
            validity[index] = 1
            for channel in 0..<6 {
                channels[channel][index] = Float(interpolate(samples, base: base, count: n, right: right, at: grid, channel: channel))
            }
        }
        return EatingWindow(channels: channels, validity: validity)
    }

    /// numpy.interp for one grid point; `right` is the first index with t >= x.
    private static func interpolate(
        _ samples: ArraySlice<EatingMotionSample>, base: Int, count n: Int, right: Int, at x: Double, channel: Int
    ) -> Double {
        if right >= n { return Double(samples[base + n - 1].values[channel]) }
        let r = samples[base + right]
        if r.timestamp == x || right == 0 { return Double(r.values[channel]) }
        let l = samples[base + right - 1]
        let y0 = Double(l.values[channel])
        let y1 = Double(r.values[channel])
        let slope = (y1 - y0) / (r.timestamp - l.timestamp)
        return slope * (x - l.timestamp) + y0
    }

    // MARK: Features

    /// numpy's pairwise summation (DOUBLE_pairwise_sum), starting from 0.
    static func pairwiseSum(_ a: [Double], _ start: Int = 0, _ count: Int? = nil) -> Double {
        let n = count ?? (a.count - start)
        if n < 8 {
            var result = 0.0
            for i in 0..<n { result += a[start + i] }
            return result
        }
        if n <= 128 {
            var r = (0..<8).map { a[start + $0] }
            var i = 8
            while i < n - (n % 8) {
                for j in 0..<8 { r[j] += a[start + i + j] }
                i += 8
            }
            var result = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
            while i < n {
                result += a[start + i]
                i += 1
            }
            return result
        }
        var half = n / 2
        half -= half % 8
        return pairwiseSum(a, start, half) + pairwiseSum(a, start + half, n - half)
    }

    /// The eight analysed signals: gx, gy, gz, |gyro|, ax, ay, az, |accel|.
    static func signals(of window: EatingWindow) -> [[Double]] {
        let samples = window.validity.count
        let channels = window.channels.map { $0.map(Double.init) }
        func magnitude(_ a: [Double], _ b: [Double], _ c: [Double]) -> [Double] {
            (0..<samples).map { ((a[$0] * a[$0] + b[$0] * b[$0]) + c[$0] * c[$0]).squareRoot() }
        }
        return [
            channels[3], channels[4], channels[5], magnitude(channels[3], channels[4], channels[5]),
            channels[0], channels[1], channels[2], magnitude(channels[0], channels[1], channels[2]),
        ]
    }

    /// Return the 80 features (float32, as the Python pipeline stores them).
    static func features(of window: EatingWindow, spec: EatingDetectorSpec) -> [Float] {
        let f = spec.features
        let samples = spec.input.windowSamples
        let bins = f.frequenciesHz.count
        let analysed = f.analysedBins[0]...f.analysedBins[1]
        let analysedCount = Double(analysed.count)
        var result: [Float] = []
        result.reserveCapacity(spec.model.featureCount)
        let (cosTable, sinTable) = dftTables(samples)

        for signal in signals(of: window) {
            let mean = pairwiseSum(signal) / Double(samples)
            let centered = signal.map { $0 - mean }
            let centeredMean = pairwiseSum(centered) / Double(samples)
            let deviations = centered.map { ($0 - centeredMean) * ($0 - centeredMean) }
            let rms = (pairwiseSum(deviations) / Double(samples)).squareRoot()

            let tapered = (0..<samples).map { centered[$0] * f.taper[$0] }
            var spectrum = Array(repeating: 0.0, count: bins)
            for k in 0..<bins {
                var re = 0.0
                var im = 0.0
                for n in 0..<samples {
                    let index = (k * n) % samples
                    re += tapered[n] * cosTable[index]
                    im -= tapered[n] * sinTable[index]
                }
                let magnitude = hypot(re, im)
                spectrum[k] = magnitude * magnitude
            }
            let analysedPower = Array(spectrum[analysed])
            let total = pairwiseSum(analysedPower) + f.powerEpsilon
            let bandFractions = f.bands.map { band in
                pairwiseSum(Array(spectrum[band.bins[0]...band.bins[1]])) / total
            }

            var dominant = f.dominantBins[0]
            for k in f.dominantBins[0]...f.dominantBins[1] where spectrum[k] > spectrum[dominant] { dominant = k }
            let chewPeak = spectrum[f.chewBins[0]...f.chewBins[1]].max() ?? 0
            let sorted = analysedPower.sorted()
            let middle = sorted.count / 2
            let median = (sorted.count % 2 == 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2)
                + f.powerEpsilon
            let chewPeakRatio = log10(chewPeak / median + f.powerEpsilon)

            let terms = analysedPower.map { value -> Double in
                let p = value / total
                return -(p * log(p + f.powerEpsilon))
            }
            let entropy = pairwiseSum(terms) / log(analysedCount)

            let variance = pairwiseSum(centered.map { $0 * $0 }) + f.powerEpsilon
            var autocorr = 0.0
            for lag in f.autocorrLags[0]...f.autocorrLags[1] {
                let products = (0..<(samples - lag)).map { centered[$0 + lag] * centered[$0] }
                autocorr = max(autocorr, pairwiseSum(products) / variance)
            }

            result.append(Float(log(rms + f.rmsLogEpsilon)))
            result.append(contentsOf: bandFractions.map { Float($0) })
            result.append(Float(f.frequenciesHz[dominant]))
            result.append(Float(chewPeakRatio))
            result.append(Float(entropy))
            result.append(Float(autocorr))
        }
        return result
    }

    private static var tableCache: [Int: ([Double], [Double])] = [:]
    private static let tableLock = NSLock()

    private static func dftTables(_ n: Int) -> ([Double], [Double]) {
        tableLock.lock()
        defer { tableLock.unlock() }
        if let cached = tableCache[n] { return cached }
        let angles = (0..<n).map { 2.0 * Double.pi * Double($0) / Double(n) }
        let tables = (angles.map(cos), angles.map(sin))
        tableCache[n] = tables
        return tables
    }
}
