//
//  EatingDetectorSpec.swift
//  ChewSenseLive
//
//  Versioned, exported motion-only eating detector (`chewsense.eating-detector`
//  v1, written by `chewsense-motion eating-export`). Every constant the
//  Python reference uses — taper, FFT bins, lags, trees, threshold and decoder —
//  is read from the file, so nothing is re-derived on device.
//
//  Foundation-only: the same source compiles into the Mac parity tool.
//

import Foundation

struct EatingDetectorSpec: Decodable {
    struct Input: Decodable {
        let sampleRateHz: Int
        let windowS: Double
        let windowSamples: Int
        let maxGapS: Double
        let exactMatchToleranceS: Double
        let minValidFraction: Double
    }

    struct Band: Decodable {
        let name: String
        let bins: [Int]
    }

    struct Features: Decodable {
        let names: [String]
        let signals: [String]
        let taper: [Double]
        let frequenciesHz: [Double]
        let analysedBins: [Int]
        let bands: [Band]
        let dominantBins: [Int]
        let chewBins: [Int]
        let autocorrLags: [Int]
        let rmsLogEpsilon: Double
        let powerEpsilon: Double
    }

    struct Tree: Decodable {
        let feature: [Int]
        let threshold: [Double]
        let left: [Int]
        let right: [Int]
        let missingLeft: [Bool]
        let value: [Double]
    }

    struct Model: Decodable {
        let kind: String
        let featureCount: Int
        let baselineLogit: Double
        let trees: [Tree]
    }

    struct Decoder: Decodable {
        let alpha: Double
        let highThreshold: Double
        let lowThreshold: Double
        let minStartWindows: Int
        let minEndWindows: Int
    }

    struct Decision: Decodable {
        let threshold: Double
        let strideS: Double
        let invalidWindowProbability: Double
        let decoder: Decoder
    }

    struct Provenance: Decodable {
        let runId: String
        let model: String
        let datasetFingerprint: String
    }

    let format: String
    let formatVersion: Int
    let modelId: String
    let status: String
    let provenance: Provenance
    let input: Input
    let features: Features
    let model: Model
    let decision: Decision

    enum SpecError: LocalizedError {
        case invalid(String)
        case missingResource(String)

        var errorDescription: String? {
            switch self {
            case .invalid(let reason): return "Invalid eating-detector specification: \(reason)"
            case .missingResource(let name): return "Eating-detector specification \(name) is not bundled"
            }
        }
    }

    static let bundledResourceName = "eating-detector"

    /// The app's bundled specification, parsed once.
    static let bundled: Result<EatingDetectorSpec, Error> = Result { try loadBundled() }

    static func load(from url: URL) throws -> EatingDetectorSpec {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let spec = try decoder.decode(EatingDetectorSpec.self, from: Data(contentsOf: url))
        try spec.validate()
        return spec
    }

    static func loadBundled(_ bundle: Bundle = .main) throws -> EatingDetectorSpec {
        guard let url = bundle.url(forResource: bundledResourceName, withExtension: "json") else {
            throw SpecError.missingResource("\(bundledResourceName).json")
        }
        return try load(from: url)
    }

    /// Rejects any file whose shape differs from what the runtime implements.
    func validate() throws {
        func require(_ condition: Bool, _ reason: String) throws {
            if !condition { throw SpecError.invalid(reason) }
        }
        try require(format == "chewsense.eating-detector" && formatVersion == 1, "unsupported format \(format) v\(formatVersion)")
        try require(model.kind == "hist_gradient_boosting_binary", "unsupported model \(model.kind)")
        try require(input.windowSamples == Int((Double(input.sampleRateHz) * input.windowS).rounded()), "window size")
        try require(features.taper.count == input.windowSamples, "taper length")
        try require(features.frequenciesHz.count == input.windowSamples / 2 + 1, "frequency bins")
        try require(features.signals.count == 8, "signal count")
        try require(features.names.count == model.featureCount && model.featureCount == features.signals.count * 10, "feature count")
        let bins = features.frequenciesHz.count
        for range in [features.analysedBins, features.dominantBins, features.chewBins] + features.bands.map(\.bins) {
            try require(range.count == 2 && range[0] >= 0 && range[0] <= range[1] && range[1] < bins, "bin range")
        }
        try require(features.bands.count == 5, "band count")
        try require(features.autocorrLags.count == 2 && features.autocorrLags[0] >= 1
                    && features.autocorrLags[1] < input.windowSamples, "autocorrelation lags")
        try require(decision.strideS > 0 && decision.decoder.minStartWindows > 0 && decision.decoder.minEndWindows > 0, "decoder")
        for tree in model.trees {
            let n = tree.feature.count
            try require(n > 0 && [tree.threshold.count, tree.left.count, tree.right.count, tree.missingLeft.count, tree.value.count]
                .allSatisfy { $0 == n }, "tree arrays")
            for node in 0..<n where tree.feature[node] >= 0 {
                try require(tree.feature[node] < model.featureCount, "tree feature index")
                try require((0..<n).contains(tree.left[node]) && (0..<n).contains(tree.right[node]), "tree child index")
            }
        }
    }
}
