import Combine
import CoreML
import Foundation

enum ChewModelState: Equatable {
    case loading
    case ready
    case failed(String)

    var isReady: Bool {
        if case .ready = self { return true }
        return false
    }

    var message: String {
        switch self {
        case .loading:
            return "Loading ChewNet"
        case .ready:
            return "ChewNet ready"
        case .failed(let message):
            return message
        }
    }
}

/// Generic CoreML wrapper used by SwiftPM, which does not generate model classes.
@MainActor
final class ChewModel: ObservableObject {
    static let shared = ChewModel()

    @Published private(set) var state: ChewModelState = .loading

    private var model: MLModel?
    private var inputName = "input"
    private var outputName = "logit"

    init(bundle: Bundle = .module) {
        guard let packageURL = bundle.url(
            forResource: "ChewNet",
            withExtension: "mlpackage"
        ) else {
            state = .failed("ChewNet.mlpackage is missing from the app bundle.")
            return
        }

        do {
            let compiledURL = try MLModel.compileModel(at: packageURL)
            let loaded = try MLModel(
                contentsOf: compiledURL,
                configuration: MLModelConfiguration()
            )
            model = loaded
            inputName = loaded.modelDescription.inputDescriptionsByName.keys.first ?? "input"
            outputName = Self.preferredOutputName(in: loaded.modelDescription)
            state = .ready
        } catch {
            state = .failed("ChewNet could not load: \(error.localizedDescription)")
        }
    }

    func predictChewProbability(features: [Double]) throws -> Double {
        guard let model else {
            throw predictionError(code: 1, message: state.message)
        }
        guard features.count == NormalizationConstants.FEATURE_MEAN.count else {
            throw predictionError(code: 2, message: "Expected 12 model features.")
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
            throw predictionError(
                code: 3,
                message: "ChewNet returned no \(outputName) output."
            )
        }

        let logit = output[0].doubleValue
        return 1.0 / (1.0 + exp(-logit))
    }

    private static func preferredOutputName(in description: MLModelDescription) -> String {
        let names = Array(description.outputDescriptionsByName.keys)
        return names.contains("logit") ? "logit" : (names.first ?? "logit")
    }

    private func predictionError(code: Int, message: String) -> NSError {
        NSError(
            domain: "ChewModel",
            code: code,
            userInfo: [NSLocalizedDescriptionKey: message]
        )
    }
}
