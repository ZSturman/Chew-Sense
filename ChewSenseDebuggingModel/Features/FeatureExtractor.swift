//
//  FeatureExtractor.swift
//  ChewSenseDebuggingModel
//
//  Unified feature extraction combining time and frequency domain features.
//  Produces the 12-element feature vector expected by ChewNet.
//

import Foundation

/// Complete feature extraction for ChewNet model.
/// Combines time-domain (7) and frequency-domain (5) features.
struct FeatureExtractor {
    
    /// Feature names matching Python pipeline order.
    static let featureNames: [String] = [
        "mean_accel_mag",
        "var_accel_mag",
        "rms_accel_mag",
        "mean_gyro_mag",
        "rms_gyro_mag",
        "rms_jerk",
        "zcr_accel_mag",
        "chew_bandpower_1_3hz",
        "bandpower_0_8_1_5hz",
        "bandpower_2_4hz",
        "spectral_centroid",
        "spectral_rolloff_0_85"
    ]
    
    /// Extract all 12 features from a window of sensor samples.
    ///
    /// - Parameters:
    ///   - samples: Array of MotionSample data
    ///   - samplingRate: Estimated sampling rate in Hz (default 100)
    /// - Returns: 12-element feature vector
    static func extractFeatures(
        from samples: [MotionSample],
        samplingRate: Double = 100.0
    ) -> [Double] {
        guard !samples.isEmpty else {
            return Array(repeating: 0.0, count: 12)
        }
        
        // Extract raw signal arrays
        let ax = samples.map { $0.ax }
        let ay = samples.map { $0.ay }
        let az = samples.map { $0.az }
        let gx = samples.map { $0.gx }
        let gy = samples.map { $0.gy }
        let gz = samples.map { $0.gz }
        
        // Time-domain features (7)
        let timeFeatures = TimeFeatures.extractTimeFeatures(
            ax: ax, ay: ay, az: az,
            gx: gx, gy: gy, gz: gz
        )
        
        // Frequency-domain features (5) - need acceleration magnitude
        let accelMag = TimeFeatures.magnitude(ax, ay, az)
        let freqFeatures = FrequencyFeatures.extractFrequencyFeatures(
            accelMagnitude: accelMag,
            fs: samplingRate
        )
        
        // Combine: 7 time + 5 frequency = 12 features
        return timeFeatures + freqFeatures
    }
    
    /// Estimate sampling rate from timestamps
    static func estimateSamplingRate(from samples: [MotionSample]) -> Double {
        guard samples.count > 1 else { return 100.0 }
        
        var dts: [Double] = []
        for i in 1..<samples.count {
            let dt = samples[i].timestamp - samples[i-1].timestamp
            if dt > 0 && dt < 0.1 {  // Reasonable range
                dts.append(dt)
            }
        }
        
        guard !dts.isEmpty else { return 100.0 }
        
        // Use median for robustness
        let sorted = dts.sorted()
        let median = sorted[sorted.count / 2]
        
        return 1.0 / median
    }
}
