//
//  ModelInfoView.swift
//  ChewSenseDemo
//
//  Read-only sheet that shows which CoreML model is bundled. Reads
//  `chewnet_norm.json` (copied next to ChewNet.mlpackage by
//  scripts/update_model.sh) so it stays in sync with the model without
//  any code changes when you swap models.
//

import SwiftUI
import CryptoKit

struct ModelInfo: Decodable {
    let model_id: String?
    let mode: String?
    let num_outputs: Int?
    let dataset_hash: String?
    let input_dim: Int?
    let hidden_dim: Int?
    let runtime_config: RuntimeConfigJSON?

    struct RuntimeConfigJSON: Decodable {
        let alpha: Double?
        let high_threshold: Double?
        let low_threshold: Double?
        let min_start_windows: Int?
        let min_end_windows: Int?
    }

    static func loadBundled() -> ModelInfo? {
        guard let url = Bundle.main.url(forResource: "chewnet_norm", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(ModelInfo.self, from: data)
    }

    /// SHA-256 of the inner `model.mlmodel` from the bundled `.mlpackage`,
    /// when present. Xcode usually flattens the package to a `.mlmodelc`
    /// directory, in which case this returns nil; the script-emitted hash
    /// is still authoritative for verification.
    static func bundledModelSHA() -> String? {
        let pkg = Bundle.main.url(forResource: "ChewNet", withExtension: "mlpackage")
        guard let pkg else { return nil }
        let mlmodel = pkg.appendingPathComponent("Data/com.apple.CoreML/model.mlmodel")
        guard let data = try? Data(contentsOf: mlmodel) else { return nil }
        let digest = SHA256.hash(data: data)
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}

struct ModelInfoView: View {

    @Environment(\.dismiss) private var dismiss
    private let info = ModelInfo.loadBundled()
    private let modelSHA = ModelInfo.bundledModelSHA()

    var body: some View {
        NavigationStack {
            List {
                Section("Model") {
                    row("Model ID", info?.model_id ?? "unknown")
                    row("Mode", info?.mode ?? "unknown")
                    row("Outputs", info?.num_outputs.map(String.init) ?? "—")
                    row("Input dim", info?.input_dim.map(String.init) ?? "—")
                    row("Hidden dim", info?.hidden_dim.map(String.init) ?? "—")
                }
                Section("Dataset") {
                    row("Hash", info?.dataset_hash ?? "—", monospaced: true)
                }
                if let sha = modelSHA {
                    Section("CoreML package") {
                        row("model.mlmodel SHA-256", shorten(sha), monospaced: true)
                    }
                }
                Section("Detector thresholds") {
                    let rc = info?.runtime_config
                    row("EMA alpha", rc?.alpha.map { String(format: "%.2f", $0) } ?? "—")
                    row("High threshold", rc?.high_threshold.map { String(format: "%.2f", $0) } ?? "—")
                    row("Low threshold", rc?.low_threshold.map { String(format: "%.2f", $0) } ?? "—")
                    row("Min start windows", rc?.min_start_windows.map(String.init) ?? "—")
                    row("Min end windows", rc?.min_end_windows.map(String.init) ?? "—")
                }
                Section("Updating the model") {
                    Text("From the repo root run:")
                        .font(.subheadline)
                    Text("./06-DemoApp/scripts/update_model.sh")
                        .font(.system(.footnote, design: .monospaced))
                        .padding(8)
                        .background(Color.gray.opacity(0.12))
                        .cornerRadius(6)
                    Text("Then rebuild the app in Xcode (Cmd+R). The values above will refresh automatically.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Model Info")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    @ViewBuilder
    private func row(_ key: String, _ value: String, monospaced: Bool = false) -> some View {
        HStack(alignment: .top) {
            Text(key)
                .foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .font(monospaced ? .system(.footnote, design: .monospaced) : .footnote)
                .multilineTextAlignment(.trailing)
                .lineLimit(2)
                .truncationMode(.middle)
        }
    }

    private func shorten(_ s: String) -> String {
        guard s.count > 16 else { return s }
        return String(s.prefix(8)) + "…" + String(s.suffix(8))
    }
}

#Preview { ModelInfoView() }
