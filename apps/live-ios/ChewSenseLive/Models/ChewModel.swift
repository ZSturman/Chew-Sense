//
//  ChewModel.swift
//  ChewSenseLive
//
//  CoreML model wrapper for ChewNet inference.
//  Supports legacy binary eating detection and the v2 temporal chew model.
//

import Foundation
import CoreML

struct ChewPrediction {
    let eatingLogit: Double
    let eatingProbability: Double
    let chewEventLogit: Double?
    let chewEventProbability: Double?
    let phaseSin: Double?
    let phaseCos: Double?
    let phaseConfidence: Double?
    let gapCoverage: Double
}

/// Wrapper for the ChewNet CoreML model.
final class ChewModel {

    /// Shared singleton instance
    static let shared = ChewModel()
    
    /// The CoreML model
    private let model: ChewNet
    
    var isTemporalMode: Bool {
        NormalizationConstants.MODE == "temporal_tcn"
    }

    var capabilities: ModelCapabilities { .bundled }
    
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
                3. Run 'chewsense-motion deploy' to generate the model
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
        predictArray(values: features, shape: [1, NSNumber(value: features.count)])
    }

    /// Run the v2 raw temporal model. The validity mask is carried through
    /// normalization unchanged and its coverage is logged with every output.
    func predictTemporal(window: TemporalModelWindow) -> ChewPrediction {
        guard isTemporalMode else { return emptyPrediction() }
        let normalized = NormalizationConstants.normalizeTemporal(window.values)
        return predictArray(
            values: normalized,
            shape: [1, 7, NSNumber(value: TemporalModelWindow.sampleCount)],
            gapCoverage: window.gapCoverage
        )
    }

    private func sigmoid(_ x: Double) -> Double {
        return 1.0 / (1.0 + exp(-x))
    }

    private func predictArray(
        values: [Double],
        shape: [NSNumber],
        gapCoverage: Double = 1.0
    ) -> ChewPrediction {
        guard let inputArray = try? MLMultiArray(shape: shape, dataType: .float32) else {
            return emptyPrediction(gapCoverage: gapCoverage)
        }
        for (index, value) in values.enumerated() {
            inputArray[index] = NSNumber(value: Float(value))
        }

        do {
            let provider = try MLDictionaryFeatureProvider(
                dictionary: ["input": MLFeatureValue(multiArray: inputArray)]
            )
            let result = try model.model.prediction(from: provider)
            let output = result.featureValue(for: "output")?.multiArrayValue
                ?? result.featureValue(for: "logit")?.multiArrayValue
            guard let output, output.count >= 1 else {
                return emptyPrediction(gapCoverage: gapCoverage)
            }
            let eatingLogit = output[0].doubleValue
            if isTemporalMode, output.count >= 5 {
                let eventLogit = output[1].doubleValue
                return ChewPrediction(
                    eatingLogit: eatingLogit,
                    eatingProbability: sigmoid(eatingLogit),
                    chewEventLogit: eventLogit,
                    chewEventProbability: sigmoid(eventLogit),
                    phaseSin: output[2].doubleValue,
                    phaseCos: output[3].doubleValue,
                    phaseConfidence: output[4].doubleValue,
                    gapCoverage: gapCoverage
                )
            }
            return ChewPrediction(
                eatingLogit: eatingLogit,
                eatingProbability: sigmoid(eatingLogit),
                chewEventLogit: nil,
                chewEventProbability: nil,
                phaseSin: nil,
                phaseCos: nil,
                phaseConfidence: nil,
                gapCoverage: gapCoverage
            )
        } catch {
            print("Prediction failed: \(error)")
            return emptyPrediction(gapCoverage: gapCoverage)
        }
    }

    private func emptyPrediction(gapCoverage: Double = 0) -> ChewPrediction {
        ChewPrediction(
            eatingLogit: -.infinity,
            eatingProbability: 0,
            chewEventLogit: nil,
            chewEventProbability: nil,
            phaseSin: nil,
            phaseCos: nil,
            phaseConfidence: nil,
            gapCoverage: gapCoverage
        )
    }
    
}
