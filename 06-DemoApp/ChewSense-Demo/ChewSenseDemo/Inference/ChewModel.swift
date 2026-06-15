import CoreML
import Foundation

@MainActor
final class ChewModel: ChewProbabilityPredicting {
    static let shared = ChewModel()

    private var model: MLModel?
    private var inputName = "input"
    private var outputName = "logit"
    private(set) var loadError: String?

    init(bundle: Bundle = .main) {
        guard let url = bundle.url(forResource: "ChewNet", withExtension: "mlmodelc") else {
            loadError = "ChewNet.mlmodelc is missing from the app bundle."
            return
        }

        do {
            let configuration = MLModelConfiguration()
#if targetEnvironment(simulator)
            configuration.computeUnits = .cpuOnly
#endif
            let loaded = try MLModel(
                contentsOf: url,
                configuration: configuration
            )
            model = loaded
            inputName = loaded.modelDescription.inputDescriptionsByName.keys.first ?? "input"
            outputName = Self.preferredOutputName(in: loaded.modelDescription)
        } catch {
            loadError = "ChewNet could not load: \(error.localizedDescription)"
        }
    }

    func predictChewProbability(features: [Double]) throws -> Double {
        guard let model else {
            throw modelError(code: 1, message: loadError ?? "ChewNet is unavailable.")
        }
        guard features.count == NormalizationConstants.FEATURE_MEAN.count else {
            throw modelError(code: 2, message: "Expected 12 model features.")
        }

        let array = try MLMultiArray(
            shape: [1, NSNumber(value: features.count)],
            dataType: .float32
        )
        for (index, value) in features.enumerated() {
            array[index] = NSNumber(value: Float(value))
        }

        let provider = try MLDictionaryFeatureProvider(dictionary: [inputName: array])
        let result = try model.prediction(from: provider)
        guard
            let output = result.featureValue(for: outputName)?.multiArrayValue,
            output.count > 0
        else {
            throw modelError(code: 3, message: "ChewNet returned no \(outputName) output.")
        }

        let logit = output[0].doubleValue
        return 1.0 / (1.0 + exp(-logit))
    }

    private static func preferredOutputName(in description: MLModelDescription) -> String {
        let names = Array(description.outputDescriptionsByName.keys)
        return names.contains("logit") ? "logit" : (names.first ?? "logit")
    }

    private func modelError(code: Int, message: String) -> NSError {
        NSError(
            domain: "ChewModel",
            code: code,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}
