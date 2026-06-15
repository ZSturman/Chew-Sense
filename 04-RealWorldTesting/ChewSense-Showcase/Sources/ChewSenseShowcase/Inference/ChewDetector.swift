//
//  ChewDetector.swift
//  ChewSenseDebuggingModel
//
//  State machine for chewing episode detection.
//  Uses hysteresis with configurable thresholds to avoid rapid state changes.
//

import Foundation

/// Possible chewing states
enum ChewState: Equatable {
    case idle
    case chewing
}

/// Configuration for the chewing detector state machine.
/// These values can be tuned at runtime without retraining the model.
struct ChewDetectorConfig {
    /// EMA smoothing factor (0 = no smoothing, 1 = no memory)
    let alpha: Double
    
    /// Probability threshold to START a chewing episode
    let highThreshold: Double
    
    /// Probability threshold to END a chewing episode
    let lowThreshold: Double
    
    /// Consecutive windows above highThreshold to confirm start
    let minStartWindows: Int
    
    /// Consecutive windows below lowThreshold to confirm end
    let minEndWindows: Int

    /// Shake-guard: gyro RMS (rad/s) above which the window is treated as a
    /// head shake / quick rotation rather than chewing. 0 disables the guard.
    let shakeGyroRmsThreshold: Double

    /// Shake-guard: minimum (chew_band_power / low_band_power) required for the
    /// window to be considered chewing-shaped. 0 disables the guard.
    let shakeChewToLowRatioMin: Double

    init(
        alpha: Double,
        highThreshold: Double,
        lowThreshold: Double,
        minStartWindows: Int,
        minEndWindows: Int,
        shakeGyroRmsThreshold: Double = 1.5,
        shakeChewToLowRatioMin: Double = 0.6
    ) {
        self.alpha = alpha
        self.highThreshold = highThreshold
        self.lowThreshold = lowThreshold
        self.minStartWindows = minStartWindows
        self.minEndWindows = minEndWindows
        self.shakeGyroRmsThreshold = shakeGyroRmsThreshold
        self.shakeChewToLowRatioMin = shakeChewToLowRatioMin
    }

    /// Default configuration matching Python pipeline defaults
    static let `default` = ChewDetectorConfig(
        alpha: 0.4,
        highThreshold: 0.6,
        lowThreshold: 0.4,
        minStartWindows: 3,
        minEndWindows: 2
    )

    /// Create config from generated RuntimeConfig (if available)
    static func fromGenerated() -> ChewDetectorConfig {
        return ChewDetectorConfig(
            alpha: RuntimeConfig.alpha,
            highThreshold: RuntimeConfig.highThreshold,
            lowThreshold: RuntimeConfig.lowThreshold,
            minStartWindows: RuntimeConfig.minStartWindows,
            minEndWindows: RuntimeConfig.minEndWindows
        )
    }
}

/// Result of a detector update
struct ChewDetectorResult {
    let state: ChewState
    let startedEpisode: Bool
    let endedEpisode: Bool
    let smoothedProb: Double
}

/// State machine for detecting chewing episodes from model probabilities.
final class ChewDetector {
    private var config: ChewDetectorConfig
    private(set) var state: ChewState = .idle
    
    private var smoothedProb: Double = 0.0
    private var aboveHighCount: Int = 0
    private var belowLowCount: Int = 0
    
    init(config: ChewDetectorConfig = .default) {
        self.config = config
    }
    
    /// Update detector with new probability from model.
    /// Returns the current state and whether an episode started/ended.
    func update(probability p: Double) -> ChewDetectorResult {
        // 1. EMA smoothing
        smoothedProb = config.alpha * p + (1.0 - config.alpha) * smoothedProb
        var started = false
        var ended = false
        
        switch state {
        case .idle:
            if smoothedProb >= config.highThreshold {
                aboveHighCount += 1
                belowLowCount = 0
                if aboveHighCount >= config.minStartWindows {
                    state = .chewing
                    started = true
                    aboveHighCount = 0
                }
            } else {
                aboveHighCount = 0
                if smoothedProb <= config.lowThreshold {
                    belowLowCount += 1
                } else {
                    belowLowCount = 0
                }
            }
            
        case .chewing:
            if smoothedProb <= config.lowThreshold {
                belowLowCount += 1
                aboveHighCount = 0
                if belowLowCount >= config.minEndWindows {
                    state = .idle
                    ended = true
                    belowLowCount = 0
                }
            } else {
                belowLowCount = 0
                if smoothedProb >= config.highThreshold {
                    aboveHighCount += 1
                } else {
                    aboveHighCount = 0
                }
            }
        }
        
        return ChewDetectorResult(
            state: state,
            startedEpisode: started,
            endedEpisode: ended,
            smoothedProb: smoothedProb
        )
    }
    
    /// Get current smoothed probability
    func currentSmoothedProb() -> Double {
        return smoothedProb
    }

    /// Update with a probability plus auxiliary signals from the feature
    /// extractor. When the window looks like a head shake or quick rotation
    /// (high gyro RMS combined with a chew-band / low-band power ratio that
    /// is *below* the chewing-shape threshold), the model probability is
    /// suppressed to 0 before being passed to the hysteresis state machine.
    /// This is a pure runtime filter — model weights and feature vector are
    /// unchanged, so the existing CoreML asset is fully compatible.
    ///
    /// - Parameters:
    ///   - p: Raw model probability in [0, 1].
    ///   - gyroRms: Root-mean-square gyroscope magnitude over the window
    ///     (rad/s). Pass 0 if unavailable to skip the gyro guard.
    ///   - chewBandPower: Bandpower in the 1-3 Hz chewing band (feature 8).
    ///   - lowBandPower: Bandpower in the 0.8-1.5 Hz low-frequency band
    ///     (feature 9). Used as the denominator for the shape ratio.
    func update(
        probability p: Double,
        gyroRms: Double,
        chewBandPower: Double,
        lowBandPower: Double
    ) -> ChewDetectorResult {
        var adjusted = p
        let gyroSuspect = config.shakeGyroRmsThreshold > 0
            && gyroRms >= config.shakeGyroRmsThreshold
        let denom = max(lowBandPower, 1e-6)
        let ratio = chewBandPower / denom
        let shapeSuspect = config.shakeChewToLowRatioMin > 0
            && ratio < config.shakeChewToLowRatioMin
        if gyroSuspect && shapeSuspect {
            adjusted = 0.0
        }
        return update(probability: adjusted)
    }

    /// Reset the detector to initial state
    func reset() {
        state = .idle
        smoothedProb = 0.0
        aboveHighCount = 0
        belowLowCount = 0
    }
    
    /// Update configuration (for runtime tuning)
    func updateConfig(_ newConfig: ChewDetectorConfig) {
        self.config = newConfig
    }
}
