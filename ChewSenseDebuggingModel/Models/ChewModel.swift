//
//  ChewModel.swift
//  ChewSenseDebuggingModel
//
//  CoreML model wrapper for ChewNet inference.
//  Supports both binary chewing detection and mouth shape estimation.
//

import Foundation
import CoreML

/// Mouth shape state estimated from AirPod motion data.
/// Values are normalized 0.0–1.0 relative to face dimensions.
struct MouthShapeState {
    let mouthOpenness: Double
    let mouthWidth: Double
    let jawDisplacement: Double
    let lipCompression: Double
    
    static let neutral = MouthShapeState(
        mouthOpenness: 0.0,
        mouthWidth: 0.5,
        jawDisplacement: 0.0,
        lipCompression: 0.0
    )
}

struct ChewPrediction {
    let eatingProbability: Double
    let phaseSin: Double?
    let phaseCos: Double?
}

/// Wrapper for the ChewNet CoreML model.
final class ChewModel {
    
    /// Shared singleton instance
    static let shared = ChewModel()
    
    /// The CoreML model
    private let model: ChewNet
    
    /// Whether this model outputs mouth shape (multi-output) or binary
    var isMouthShapeMode: Bool {
        return NormalizationConstants.MODE == "mouth_shape"
    }

    var isTwoHeadMode: Bool {
        return NormalizationConstants.MODE == "two_head"
    }
    
    private init() {
        let config = MLModelConfiguration()
        
        do {
            model = try ChewNet(configuration: config)
        } catch {
            fatalError("""
                Failed to load ChewNet model: \(error)
                
                Make sure:
                1. ChewNet.mlpackage is added to the Xcode project
                2. The model is included in the target's "Copy Bundle Resources"
                3. Run 'python main.py deploy' from 02-DataPipeline to generate the model
                """)
        }
    }
    
    /// Predict chewing probability from normalized features (binary mode).
    ///
    /// - Parameter features: Normalized feature vector
    /// - Returns: Probability of chewing (0.0 to 1.0)
    func predictChewProbability(features: [Double]) -> Double {
        predict(features: features).eatingProbability
    }

    /// Run one model invocation and expose the optional reviewed chew-phase
    /// output used to derive individual motion chews.
    func predict(features: [Double]) -> ChewPrediction {
        guard let inputArray = try? MLMultiArray(shape: [1, NSNumber(value: features.count)], dataType: .float32) else {
            print("Failed to create MLMultiArray")
            return ChewPrediction(eatingProbability: 0, phaseSin: nil, phaseCos: nil)
        }
        
        for (i, v) in features.enumerated() {
            inputArray[i] = NSNumber(value: Float(v))
        }
        
        do {
            let input = ChewNetInput(input: inputArray)
            let output = try model.prediction(input: input)
            let logitArray = output.logit
            let logit = logitArray[0].doubleValue
            let phaseSin = isTwoHeadMode && logitArray.count >= 3
                ? logitArray[1].doubleValue
                : nil
            let phaseCos = isTwoHeadMode && logitArray.count >= 3
                ? logitArray[2].doubleValue
                : nil
            return ChewPrediction(
                eatingProbability: sigmoid(logit),
                phaseSin: phaseSin,
                phaseCos: phaseCos
            )
        } catch {
            print("Prediction failed: \(error)")
            return ChewPrediction(eatingProbability: 0, phaseSin: nil, phaseCos: nil)
        }
    }
    
    /// Predict mouth shape from normalized features (multi-output mode).
    ///
    /// - Parameter features: Normalized feature vector
    /// - Returns: MouthShapeState with estimated parameters
    func predictMouthShape(features: [Double]) -> MouthShapeState {
        // The checked-in ChewNet model is binary. Future mouth-shape models
        // should add a generated output accessor here once that model is active.
        return .neutral
    }
    
    private func sigmoid(_ x: Double) -> Double {
        return 1.0 / (1.0 + exp(-x))
    }
    
}
