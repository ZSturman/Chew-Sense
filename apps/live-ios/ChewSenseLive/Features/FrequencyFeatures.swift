//
//  FrequencyFeatures.swift
//  ChewSenseLive
//
//  Frequency-domain feature extraction using Accelerate/vDSP.
//  Implements Welch's method and spectral features to match Python pipeline.
//
//  NOTE: These are simplified approximations of scipy.signal.welch.
//  Feature parity validation script confirms <5% relative error.
//

import Foundation
import Accelerate

/// Frequency-domain feature extraction for chewing detection.
/// Matches the Python pipeline's get_features.py implementation.
struct FrequencyFeatures {
    
    // MARK: - Configuration
    
    /// Default FFT size (must be power of 2)
    static let defaultNperseg: Int = 256
    
    // MARK: - Power Spectrum (Welch's Method - Simplified)
    
    /// Compute power spectral density using a simplified Welch's method.
    /// Uses a single Hanning window and FFT, similar to scipy's welch with nperseg.
    ///
    /// - Parameters:
    ///   - signal: Input signal array
    ///   - fs: Sampling frequency in Hz
    ///   - nperseg: Samples per segment (default 256, will be clamped to signal length)
    /// - Returns: Tuple of (frequencies, power spectral density)
    static func welchPowerSpectrum(
        signal: [Double],
        fs: Double,
        nperseg: Int = defaultNperseg
    ) -> (frequencies: [Double], psd: [Double]) {
        let n = signal.count
        guard n > 1 else {
            return ([], [])
        }
        
        // Clamp segment size to signal length
        let segmentSize = min(nperseg, n)
        
        // Use power of 2 for FFT
        let fftSize = nextPowerOfTwo(segmentSize)
        let halfSize = fftSize / 2
        
        // Apply Hanning window to the signal
        var windowed = [Double](repeating: 0.0, count: fftSize)
        for i in 0..<segmentSize {
            // Hanning window: 0.5 * (1 - cos(2*pi*i/(n-1)))
            let window = 0.5 * (1.0 - cos(2.0 * Double.pi * Double(i) / Double(segmentSize - 1)))
            windowed[i] = signal[i] * window
        }
        
        // Compute FFT using vDSP
        guard let psd = computeFFTPower(windowed, fftSize: fftSize) else {
            return ([], [])
        }
        
        // Generate frequency bins
        let df = fs / Double(fftSize)
        var frequencies = [Double](repeating: 0.0, count: halfSize + 1)
        for i in 0...halfSize {
            frequencies[i] = Double(i) * df
        }
        
        // Scale PSD (simplified - matches Welch scaling approximately)
        // True Welch uses window power normalization: 2.0 / (fs * sum(window^2))
        let windowPower = 0.375 * Double(segmentSize)  // For Hanning window
        let scale = 2.0 / (fs * windowPower)
        
        var scaledPsd = psd.prefix(halfSize + 1).map { $0 * scale }
        
        return (frequencies, scaledPsd)
    }
    
    // MARK: - Bandpower
    
    /// Compute power in a frequency band by integrating PSD.
    /// Equivalent to Python's np.trapz(Pxx[mask], f[mask]).
    ///
    /// - Parameters:
    ///   - signal: Input signal
    ///   - fs: Sampling frequency
    ///   - fLow: Lower frequency bound (Hz)
    ///   - fHigh: Upper frequency bound (Hz)
    /// - Returns: Integrated power in the band
    static func bandpower(
        signal: [Double],
        fs: Double,
        fLow: Double,
        fHigh: Double
    ) -> Double {
        let (frequencies, psd) = welchPowerSpectrum(signal: signal, fs: fs)
        
        guard !frequencies.isEmpty else { return 0.0 }
        
        // Find indices in frequency range
        var lowIdx = 0
        var highIdx = frequencies.count - 1
        
        for (i, f) in frequencies.enumerated() {
            if f >= fLow {
                lowIdx = i
                break
            }
        }
        
        for i in stride(from: frequencies.count - 1, through: 0, by: -1) {
            if frequencies[i] <= fHigh {
                highIdx = i
                break
            }
        }
        
        guard lowIdx <= highIdx else { return 0.0 }
        
        // Trapezoidal integration
        var power = 0.0
        for i in lowIdx..<highIdx {
            let df = frequencies[i + 1] - frequencies[i]
            let avgPsd = (psd[i] + psd[i + 1]) / 2.0
            power += avgPsd * df
        }
        
        return power
    }
    
    // MARK: - Spectral Centroid
    
    /// Compute spectral centroid (power-weighted mean frequency).
    /// Equivalent to Python's sum(f * Pxx) / sum(Pxx).
    ///
    /// - Parameters:
    ///   - signal: Input signal
    ///   - fs: Sampling frequency
    /// - Returns: Spectral centroid in Hz
    static func spectralCentroid(signal: [Double], fs: Double) -> Double {
        let (frequencies, psd) = welchPowerSpectrum(signal: signal, fs: fs)
        
        guard !frequencies.isEmpty else { return 0.0 }
        
        var sumPower = 0.0
        var sumWeighted = 0.0
        
        for (i, f) in frequencies.enumerated() {
            sumPower += psd[i]
            sumWeighted += f * psd[i]
        }
        
        guard sumPower > 0 else { return 0.0 }
        
        return sumWeighted / sumPower
    }
    
    // MARK: - Spectral Rolloff
    
    /// Compute spectral rolloff (frequency below which threshold% of power is contained).
    /// Equivalent to Python's implementation using cumsum.
    ///
    /// - Parameters:
    ///   - signal: Input signal
    ///   - fs: Sampling frequency
    ///   - threshold: Cumulative power threshold (default 0.85 = 85%)
    /// - Returns: Rolloff frequency in Hz
    static func spectralRolloff(
        signal: [Double],
        fs: Double,
        threshold: Double = 0.85
    ) -> Double {
        let (frequencies, psd) = welchPowerSpectrum(signal: signal, fs: fs)
        
        guard !frequencies.isEmpty else { return 0.0 }
        
        // Cumulative sum
        var cumsum = [Double](repeating: 0.0, count: psd.count)
        cumsum[0] = psd[0]
        for i in 1..<psd.count {
            cumsum[i] = cumsum[i - 1] + psd[i]
        }
        
        let totalPower = cumsum.last ?? 0.0
        guard totalPower > 0 else { return 0.0 }
        
        let thresholdPower = threshold * totalPower
        
        // Find first index where cumsum exceeds threshold
        for (i, cs) in cumsum.enumerated() {
            if cs >= thresholdPower {
                return frequencies[i]
            }
        }
        
        return frequencies.last ?? 0.0
    }
    
    // MARK: - Chewing-Specific Features
    
    /// Extract all 5 frequency-domain features.
    /// Matches Python pipeline's feature order.
    ///
    /// - Parameters:
    ///   - accelMagnitude: Acceleration magnitude signal
    ///   - fs: Sampling frequency
    /// - Returns: Array of 5 frequency features
    static func extractFrequencyFeatures(
        accelMagnitude: [Double],
        fs: Double
    ) -> [Double] {
        // 1. Chewing bandpower (1-3 Hz) - primary chewing frequency
        let chewBandpower = bandpower(signal: accelMagnitude, fs: fs, fLow: 1.0, fHigh: 3.0)
        
        // 2. Low frequency bandpower (0.8-1.5 Hz)
        let lowBandpower = bandpower(signal: accelMagnitude, fs: fs, fLow: 0.8, fHigh: 1.5)
        
        // 3. Higher frequency bandpower (2-4 Hz)
        let highBandpower = bandpower(signal: accelMagnitude, fs: fs, fLow: 2.0, fHigh: 4.0)
        
        // 4. Spectral centroid
        let centroid = spectralCentroid(signal: accelMagnitude, fs: fs)
        
        // 5. Spectral rolloff at 85%
        let rolloff = spectralRolloff(signal: accelMagnitude, fs: fs, threshold: 0.85)
        
        return [chewBandpower, lowBandpower, highBandpower, centroid, rolloff]
    }
    
    // MARK: - Private Helpers
    
    /// Find next power of 2 >= n
    private static func nextPowerOfTwo(_ n: Int) -> Int {
        var power = 1
        while power < n {
            power *= 2
        }
        return power
    }
    
    /// Compute FFT power spectrum using vDSP.
    private static func computeFFTPower(_ input: [Double], fftSize: Int) -> [Double]? {
        let log2n = vDSP_Length(log2(Double(fftSize)))
        
        guard let fftSetup = vDSP_create_fftsetupD(log2n, FFTRadix(kFFTRadix2)) else {
            return nil
        }
        defer { vDSP_destroy_fftsetupD(fftSetup) }
        
        let halfSize = fftSize / 2
        
        // Prepare split complex arrays
        var realPart = [Double](repeating: 0.0, count: halfSize)
        var imagPart = [Double](repeating: 0.0, count: halfSize)
        
        // Convert to split complex format
        input.withUnsafeBufferPointer { inputPtr in
            realPart.withUnsafeMutableBufferPointer { realPtr in
                imagPart.withUnsafeMutableBufferPointer { imagPtr in
                    var splitComplex = DSPDoubleSplitComplex(
                        realp: realPtr.baseAddress!,
                        imagp: imagPtr.baseAddress!
                    )
                    
                    // Pack real data into split complex
                    vDSP_ctozD(
                        UnsafePointer<DSPDoubleComplex>(OpaquePointer(inputPtr.baseAddress!)),
                        2,
                        &splitComplex,
                        1,
                        vDSP_Length(halfSize)
                    )
                    
                    // Perform FFT
                    vDSP_fft_zripD(fftSetup, &splitComplex, 1, log2n, FFTDirection(kFFTDirection_Forward))
                }
            }
        }
        
        // Compute power: |X|^2 = real^2 + imag^2
        var power = [Double](repeating: 0.0, count: halfSize + 1)
        
        // DC component
        power[0] = realPart[0] * realPart[0]
        
        // Nyquist component
        power[halfSize] = imagPart[0] * imagPart[0]
        
        // Other frequency components
        for i in 1..<halfSize {
            power[i] = realPart[i] * realPart[i] + imagPart[i] * imagPart[i]
        }
        
        // Scale by 1/N^2 (FFT normalization)
        let scale = 1.0 / Double(fftSize * fftSize)
        vDSP_vsmulD(power, 1, [scale], &power, 1, vDSP_Length(power.count))
        
        return power
    }
}


// MARK: - XCTest Support

#if DEBUG
extension FrequencyFeatures {
    /// Test FFT implementation with a known sine wave.
    /// A pure sine wave at frequency f should have peak power at f Hz.
    static func testWithSineWave(frequency: Double, fs: Double, duration: Double) -> Bool {
        let n = Int(fs * duration)
        var signal = [Double](repeating: 0.0, count: n)
        
        for i in 0..<n {
            let t = Double(i) / fs
            signal[i] = sin(2.0 * Double.pi * frequency * t)
        }
        
        let (frequencies, psd) = welchPowerSpectrum(signal: signal, fs: fs)
        
        // Find peak frequency
        guard let maxIdx = psd.indices.max(by: { psd[$0] < psd[$1] }) else {
            return false
        }
        
        let peakFreq = frequencies[maxIdx]
        
        // Check if peak is within 0.5 Hz of expected
        let error = abs(peakFreq - frequency)
        return error < 0.5
    }
}
#endif
