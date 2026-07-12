//
//  FeatureExtractionTests.swift
//  ChewSenseLiveTests
//
//  Unit tests for Swift feature extraction, including FFT validation.
//

import XCTest
@testable import ChewSenseLive

final class FeatureExtractionTests: XCTestCase {
    
    // MARK: - FFT Validation with Known Signal
    
    /// Test that FFT correctly identifies peak frequency in a sine wave.
    /// A pure 2 Hz sine wave should have peak power at 2 Hz.
    func testFFTPeakFrequency() {
        let frequency = 2.0  // Hz
        let fs = 100.0       // Sampling rate
        let duration = 1.0   // seconds
        let n = Int(fs * duration)
        
        // Generate sine wave
        var signal = [Double](repeating: 0.0, count: n)
        for i in 0..<n {
            let t = Double(i) / fs
            signal[i] = sin(2.0 * Double.pi * frequency * t)
        }
        
        // Compute power spectrum
        let (frequencies, psd) = FrequencyFeatures.welchPowerSpectrum(signal: signal, fs: fs)
        
        // Find peak
        guard let maxIdx = psd.indices.max(by: { psd[$0] < psd[$1] }) else {
            XCTFail("Empty PSD")
            return
        }
        
        let peakFreq = frequencies[maxIdx]
        
        // Peak should be within 0.5 Hz of expected
        XCTAssertEqual(peakFreq, frequency, accuracy: 0.5, "Peak frequency should match input sine wave")
    }
    
    /// Test that bandpower captures power in the correct frequency range.
    func testBandpowerRange() {
        let fs = 100.0
        let n = 200
        
        // Generate 2 Hz sine wave
        var signal = [Double](repeating: 0.0, count: n)
        for i in 0..<n {
            let t = Double(i) / fs
            signal[i] = sin(2.0 * Double.pi * 2.0 * t)  // 2 Hz
        }
        
        // Power in 1-3 Hz should be significant
        let power1_3 = FrequencyFeatures.bandpower(signal: signal, fs: fs, fLow: 1.0, fHigh: 3.0)
        
        // Power in 5-10 Hz should be minimal
        let power5_10 = FrequencyFeatures.bandpower(signal: signal, fs: fs, fLow: 5.0, fHigh: 10.0)
        
        XCTAssertGreaterThan(power1_3, power5_10 * 10, "Bandpower in 1-3 Hz should dominate for 2 Hz signal")
    }
    
    /// Test spectral centroid calculation.
    func testSpectralCentroid() {
        let fs = 100.0
        let n = 200
        
        // Generate 5 Hz sine wave
        var signal = [Double](repeating: 0.0, count: n)
        for i in 0..<n {
            let t = Double(i) / fs
            signal[i] = sin(2.0 * Double.pi * 5.0 * t)  // 5 Hz
        }
        
        let centroid = FrequencyFeatures.spectralCentroid(signal: signal, fs: fs)
        
        // Centroid should be close to 5 Hz for pure tone
        XCTAssertEqual(centroid, 5.0, accuracy: 1.0, "Spectral centroid should be near signal frequency")
    }
    
    /// Test spectral rolloff calculation.
    func testSpectralRolloff() {
        let fs = 100.0
        let n = 200
        
        // Generate 3 Hz sine wave
        var signal = [Double](repeating: 0.0, count: n)
        for i in 0..<n {
            let t = Double(i) / fs
            signal[i] = sin(2.0 * Double.pi * 3.0 * t)  // 3 Hz
        }
        
        let rolloff = FrequencyFeatures.spectralRolloff(signal: signal, fs: fs, threshold: 0.85)
        
        // Rolloff should be at or above the signal frequency
        XCTAssertGreaterThanOrEqual(rolloff, 3.0, "Spectral rolloff should be at or above signal frequency")
        XCTAssertLessThan(rolloff, 20.0, "Spectral rolloff should be reasonable")
    }
    
    // MARK: - Time Domain Features
    
    func testMean() {
        let signal = [1.0, 2.0, 3.0, 4.0, 5.0]
        let mean = TimeFeatures.mean(signal)
        XCTAssertEqual(mean, 3.0, accuracy: 0.0001)
    }
    
    func testVariance() {
        let signal = [1.0, 2.0, 3.0, 4.0, 5.0]
        let variance = TimeFeatures.variance(signal)
        XCTAssertEqual(variance, 2.0, accuracy: 0.0001)  // Population variance
    }
    
    func testRMS() {
        let signal = [3.0, 4.0]  // sqrt((9 + 16) / 2) = sqrt(12.5) ≈ 3.536
        let rms = TimeFeatures.rms(signal)
        XCTAssertEqual(rms, sqrt(12.5), accuracy: 0.0001)
    }
    
    func testZeroCrossingRate() {
        let signal = [-1.0, 1.0, -1.0, 1.0, -1.0]  // 4 crossings / 4 intervals = 1.0
        let zcr = TimeFeatures.zeroCrossingRate(signal)
        XCTAssertEqual(zcr, 1.0, accuracy: 0.0001)
    }
    
    func testMagnitude() {
        let x = [3.0]
        let y = [4.0]
        let z = [0.0]
        let mag = TimeFeatures.magnitude(x, y, z)
        XCTAssertEqual(mag[0], 5.0, accuracy: 0.0001)  // 3-4-5 triangle
    }
    
    // MARK: - Feature Vector Integration
    
    func testFeatureVectorLength() {
        // Generate dummy samples
        var samples: [MotionSample] = []
        for i in 0..<75 {  // 0.75 seconds at 100 Hz
            samples.append(MotionSample(
                timestamp: Double(i) / 100.0,
                ax: Double.random(in: -1...1),
                ay: Double.random(in: -1...1),
                az: Double.random(in: -1...1),
                gx: Double.random(in: -1...1),
                gy: Double.random(in: -1...1),
                gz: Double.random(in: -1...1)
            ))
        }
        
        let features = FeatureExtractor.extractFeatures(from: samples, samplingRate: 100.0)
        
        XCTAssertEqual(features.count, 12, "Feature vector should have 12 elements")
    }
    
    // MARK: - Debug Test for FFT Implementation
    
    /// Validates the FFT implementation produces correct values.
    func testFFTImplementationCorrectness() {
        let result = FrequencyFeatures.testWithSineWave(frequency: 2.0, fs: 100.0, duration: 1.0)
        XCTAssertTrue(result, "FFT should correctly identify 2 Hz sine wave")
        
        let result5Hz = FrequencyFeatures.testWithSineWave(frequency: 5.0, fs: 100.0, duration: 1.0)
        XCTAssertTrue(result5Hz, "FFT should correctly identify 5 Hz sine wave")
    }
}
