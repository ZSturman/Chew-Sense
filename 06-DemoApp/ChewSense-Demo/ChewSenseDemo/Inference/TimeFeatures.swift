//
//  TimeFeatures.swift
//  ChewSenseDebuggingModel
//
//  Time-domain feature extraction for chewing detection.
//  These are the 7 time-domain features that match the Python pipeline.
//

import Foundation
import Accelerate

/// Time-domain feature extraction for chewing detection.
struct TimeFeatures {
    
    // MARK: - Basic Statistics
    
    /// Compute mean of an array
    static func mean(_ x: [Double]) -> Double {
        guard !x.isEmpty else { return 0.0 }
        var result = 0.0
        vDSP_meanvD(x, 1, &result, vDSP_Length(x.count))
        return result
    }
    
    /// Compute variance of an array (population variance)
    static func variance(_ x: [Double]) -> Double {
        guard x.count > 1 else { return 0.0 }
        let m = mean(x)
        var sumSq = 0.0
        for val in x {
            sumSq += (val - m) * (val - m)
        }
        return sumSq / Double(x.count)
    }
    
    /// Compute root mean square
    static func rms(_ x: [Double]) -> Double {
        guard !x.isEmpty else { return 0.0 }
        var sumSq = 0.0
        vDSP_svesqD(x, 1, &sumSq, vDSP_Length(x.count))
        return sqrt(sumSq / Double(x.count))
    }
    
    // MARK: - Derived Features
    
    /// Compute acceleration magnitude from x, y, z components
    static func magnitude(_ x: [Double], _ y: [Double], _ z: [Double]) -> [Double] {
        guard x.count == y.count && y.count == z.count else { return [] }
        
        var result = [Double](repeating: 0.0, count: x.count)
        for i in 0..<x.count {
            result[i] = sqrt(x[i] * x[i] + y[i] * y[i] + z[i] * z[i])
        }
        return result
    }
    
    /// Compute jerk (first derivative) of a signal
    static func jerk(_ x: [Double]) -> [Double] {
        guard x.count > 1 else { return x }
        
        var result = [Double](repeating: 0.0, count: x.count)
        result[0] = 0.0  // Prepend with zero like np.diff with prepend
        for i in 1..<x.count {
            result[i] = x[i] - x[i - 1]
        }
        return result
    }
    
    /// Compute zero-crossing rate
    /// Counts how often the signal crosses zero, normalized by length
    static func zeroCrossingRate(_ x: [Double]) -> Double {
        guard x.count > 1 else { return 0.0 }
        
        var crossings = 0
        for i in 1..<x.count {
            // Check sign change (using signbit like Python)
            let prev = x[i - 1]
            let curr = x[i]
            if (prev < 0 && curr >= 0) || (prev >= 0 && curr < 0) {
                crossings += 1
            }
        }
        
        return Double(crossings) / Double(x.count - 1)
    }
    
    // MARK: - Feature Extraction
    
    /// Extract all 7 time-domain features from sensor data.
    /// Order matches Python pipeline: mean_acc, var_acc, rms_acc, mean_gyro, rms_gyro, rms_jerk, zcr_acc
    ///
    /// - Parameters:
    ///   - ax, ay, az: Accelerometer data
    ///   - gx, gy, gz: Gyroscope data
    /// - Returns: Array of 7 time-domain features
    static func extractTimeFeatures(
        ax: [Double], ay: [Double], az: [Double],
        gx: [Double], gy: [Double], gz: [Double]
    ) -> [Double] {
        // Compute magnitudes
        let accelMag = magnitude(ax, ay, az)
        let gyroMag = magnitude(gx, gy, gz)
        
        // Compute jerk of acceleration magnitude
        let jerkVals = jerk(accelMag)
        
        // Extract features
        let meanAcc = mean(accelMag)
        let varAcc = variance(accelMag)
        let rmsAcc = rms(accelMag)
        let meanGyro = mean(gyroMag)
        let rmsGyro = rms(gyroMag)
        let rmsJerk = rms(jerkVals)
        let zcrAcc = zeroCrossingRate(accelMag)
        
        return [meanAcc, varAcc, rmsAcc, meanGyro, rmsGyro, rmsJerk, zcrAcc]
    }
}
