//
//  ConfigurationView.swift
//  ChewSenseLive
//
//  Read-only "Active Model" inspector + scenario notes + activity preset
//  picker for field-test sessions.
//
//  Detection parameters (alpha, thresholds, confirmation windows) are
//  owned by the deployed CoreML model and are surfaced here read-only.
//  Tune them via retrain → compare → deploy, never via the recording UI.
//

import SwiftUI
import Combine

/// Observable session-metadata configuration. Exposes only fields that
/// describe *what is being recorded*, not how detection runs. Detection
/// parameters are sourced from the auto-generated `RuntimeConfig` (see
/// `Generated/NormalizationConstants.swift`) and are exposed read-only.
final class DetectionConfiguration: ObservableObject {

    // MARK: - User-supplied session metadata (persisted between runs)

    @Published var testScenario: String {
        didSet { UserDefaults.standard.set(testScenario, forKey: "config.testScenario") }
    }

    @Published var activityPreset: ActivityPreset {
        didSet {
            UserDefaults.standard.set(activityPreset.rawValue, forKey: "config.activityPreset")
            // Initialize the gold-lock toggle to the preset's default when it
            // changes, but only on transitions — never blindly overwrite a
            // toggle the user just touched (didSet on goldLockCandidate
            // doesn't re-fire because we compare).
            let preferred = activityPreset.defaultGoldCandidate
            if goldLockCandidate != preferred {
                goldLockCandidate = preferred
            }
        }
    }

    @Published var customActivity: String {
        didSet { UserDefaults.standard.set(customActivity, forKey: "config.customActivity") }
    }

    @Published var goldLockCandidate: Bool {
        didSet { UserDefaults.standard.set(goldLockCandidate, forKey: "config.goldLockCandidate") }
    }

    @Published var mealTexture: MealTexture {
        didSet { UserDefaults.standard.set(mealTexture.rawValue, forKey: "config.mealTexture") }
    }

    @Published var eatingDetectorMode: EatingDetectorMode {
        didSet { UserDefaults.standard.set(eatingDetectorMode.rawValue, forKey: EatingDetectorMode.defaultsKey) }
    }

    // MARK: - Init

    init() {
        let defaults = UserDefaults.standard
        self.testScenario = defaults.string(forKey: "config.testScenario") ?? ""
        let storedPreset = defaults.string(forKey: "config.activityPreset")
            .flatMap(ActivityPreset.init(rawValue:))
        self.activityPreset = storedPreset ?? .custom
        self.customActivity = defaults.string(forKey: "config.customActivity") ?? ""
        self.goldLockCandidate = defaults.bool(forKey: "config.goldLockCandidate")
        self.mealTexture = defaults.string(forKey: "config.mealTexture")
            .flatMap(MealTexture.init(rawValue:)) ?? .unknown
        self.eatingDetectorMode = EatingDetectorMode.stored
    }

    // MARK: - Derived values

    /// True when the current preset selection is enough to start a session.
    /// Custom requires a non-empty custom string; everything else is fine.
    var isPresetReadyToRecord: Bool {
        switch activityPreset {
        case .custom:
            return !customActivity.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        default:
            return true
        }
    }

    /// Display label written into session metadata. For custom presets,
    /// uses the user-supplied free-form description; otherwise the preset's
    /// canonical display name.
    var activityLabel: String {
        switch activityPreset {
        case .custom:
            return customActivity.trimmingCharacters(in: .whitespacesAndNewlines)
        default:
            return activityPreset.displayName
        }
    }

    /// Slug written into session metadata. For custom, normalizes the
    /// user-supplied string into a slug-shaped value.
    var activitySlug: String {
        switch activityPreset {
        case .custom:
            let trimmed = customActivity.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty { return "custom" }
            let lowered = trimmed.lowercased()
            let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789_")
            var slug = String(lowered.unicodeScalars.map { allowed.contains($0) ? Character($0) : "_" })
            while slug.contains("__") { slug = slug.replacingOccurrences(of: "__", with: "_") }
            return slug.trimmingCharacters(in: CharacterSet(charactersIn: "_"))
        default:
            return activityPreset.slug
        }
    }

    // MARK: - Detector / runtime config from the bundled model

    /// Returns the detector config baked into the deployed CoreML model.
    /// The Inspector surfaces these read-only and the recording flow uses
    /// them verbatim — there is no runtime override path.
    func detectorConfigFromBundledModel() -> ChewDetectorConfig {
        return ChewDetectorConfig.fromGenerated()
    }

    /// JSON written into the session bundle's `config.json`. Mirrors the
    /// shape that previous app versions wrote so the Python pipeline keeps
    /// reading both old and new bundles unchanged.
    func toSessionConfigJSON() -> String {
        let detector = detectorConfigFromBundledModel()
        let dict: [String: Any] = [
            "alpha": detector.alpha,
            "high_threshold": detector.highThreshold,
            "low_threshold": detector.lowThreshold,
            "min_start_windows": detector.minStartWindows,
            "min_end_windows": detector.minEndWindows,
            "test_scenario": testScenario,
            "activity": activitySlug,
            "activity_label": activityLabel,
            "expected_label_class": activityPreset.expectedLabelClass.rawValue,
            "gold_lock_candidate": goldLockCandidate,
            "meal_texture": mealTexture.rawValue,
            "params_source": "bundled_model"
        ]
        if let data = try? JSONSerialization.data(withJSONObject: dict, options: .prettyPrinted),
           let string = String(data: data, encoding: .utf8) {
            return string
        }
        return "{}"
    }

    func applyRecommendation(_ recommendation: CollectionRecommendation) {
        if let preset = recommendation.activityPreset {
            activityPreset = preset
        } else {
            activityPreset = .custom
            customActivity = recommendation.activityLabel
        }
        testScenario = recommendation.scenarioText
    }
}

// MARK: - Inspector Configuration Views

private struct ConfigurationDescriptor {
    let title: String
    let description: String
}

private enum ConfigurationDescriptions {
    static let scenario = ConfigurationDescriptor(
        title: "Scenario",
        description: "Optional free-form note (food type, environment, etc.)."
    )
    static let alpha = ConfigurationDescriptor(
        title: "Smoothing Alpha",
        description: "EMA smoothing factor used during inference."
    )
    static let highThreshold = ConfigurationDescriptor(
        title: "Start Threshold",
        description: "Smoothed probability required before the detector enters Chewing."
    )
    static let lowThreshold = ConfigurationDescriptor(
        title: "End Threshold",
        description: "Smoothed probability required before the detector returns to Not Chewing."
    )
    static let startWindows = ConfigurationDescriptor(
        title: "Start Windows",
        description: "Consecutive windows above the start threshold to confirm Chewing."
    )
    static let endWindows = ConfigurationDescriptor(
        title: "End Windows",
        description: "Consecutive windows below the end threshold to confirm Not Chewing."
    )
}

struct DetectionConfigurationInspector: View {
    @ObservedObject var config: DetectionConfiguration
    /// Retained for API compatibility with `MainContentView`. Editing has
    /// been removed for detection params; only metadata fields below are
    /// affected, and they remain editable until recording starts.
    let isEditable: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Field-Test Session")
                    .font(.headline)

                ActivityPresetSection(config: config, isEditable: isEditable)

                VStack(alignment: .leading, spacing: 6) {
                    Text(ConfigurationDescriptions.scenario.title)
                        .font(.caption.weight(.semibold))
                    TextField("apple chew, walking idle, talking", text: $config.testScenario)
                        .textFieldStyle(.roundedBorder)
                        .disabled(!isEditable)
                    Text(ConfigurationDescriptions.scenario.description)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Divider()

                ActiveModelSection()

                Divider()

                BundledModelSection()

                Divider()

                ExportedEatingDetectorSection(config: config, isEditable: isEditable)
            }
            .padding()
        }
        .frame(minWidth: 280, idealWidth: 340)
    }
}

/// Activity preset picker + benchmark candidate toggle. Drives the
/// dataset-coverage workflow described in
/// `docs/REAL_WORLD_TEST_PROTOCOL.md`.
private struct ActivityPresetSection: View {
    @ObservedObject var config: DetectionConfiguration
    let isEditable: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Activity Preset")
                .font(.subheadline.weight(.semibold))
            Text("Tag every recording so the retraining pipeline can group, filter, and balance dataset coverage.")
                .font(.caption)
                .foregroundStyle(.secondary)

            Picker("Preset", selection: $config.activityPreset) {
                ForEach(ActivityPreset.allCases) { preset in
                    Label(preset.displayName, systemImage: preset.systemImage)
                        .tag(preset)
                }
            }
            .pickerStyle(.menu)
            .disabled(!isEditable)

            Text(config.activityPreset.helpText)
                .font(.caption)
                .foregroundStyle(.secondary)

            if config.activityPreset == .custom {
                TextField("Describe the activity", text: $config.customActivity)
                    .textFieldStyle(.roundedBorder)
                    .disabled(!isEditable)
            }

            Picker("Meal texture", selection: $config.mealTexture) {
                ForEach(MealTexture.allCases) { texture in
                    Text(texture.title).tag(texture)
                }
            }
            .disabled(!isEditable)

            Text("Unknown keeps cadence metrics available but disables any future chew-count goal.")
                .font(.caption)
                .foregroundStyle(.secondary)

            HStack(spacing: 8) {
                expectedLabelBadge(for: config.activityPreset.expectedLabelClass)
                Spacer()
            }

            Toggle(isOn: $config.goldLockCandidate) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Mark as benchmark candidate")
                        .font(.caption.weight(.semibold))
                    Text("Flags this session for promotion to the locked validation set on import.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .toggleStyle(.switch)
            .disabled(!isEditable)

            if !config.isPresetReadyToRecord {
                Label("Add a description before recording", systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundColor(.orange)
            }
        }
    }

    @ViewBuilder
    private func expectedLabelBadge(for klass: ExpectedLabelClass) -> some View {
        let (text, color) = labelForClass(klass)
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.18))
            .foregroundColor(color)
            .clipShape(Capsule())
    }

    private func labelForClass(_ klass: ExpectedLabelClass) -> (String, Color) {
        switch klass {
        case .positive: return ("Expected: chewing positives", .orange)
        case .negative: return ("Expected: useful negatives", .blue)
        case .mixed:    return ("Expected: mixed / mouth movement", .purple)
        case .unknown:  return ("Expected: unspecified", .gray)
        }
    }
}

/// Read-only display of the detection parameters baked into the deployed
/// model. Tuning happens in the Python pipeline, not here.
struct ActiveModelSection: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Active Detection Parameters")
                .font(.headline)
            Text("Owned by the deployed model. Edit them by retraining and re-deploying — never in the app.")
                .font(.caption)
                .foregroundStyle(.secondary)

            InspectorValueRow(
                title: ConfigurationDescriptions.alpha.title,
                value: String(format: "%.2f", RuntimeConfig.alpha),
                description: ConfigurationDescriptions.alpha.description
            )
            InspectorValueRow(
                title: ConfigurationDescriptions.highThreshold.title,
                value: String(format: "%.2f", RuntimeConfig.highThreshold),
                description: ConfigurationDescriptions.highThreshold.description
            )
            InspectorValueRow(
                title: ConfigurationDescriptions.lowThreshold.title,
                value: String(format: "%.2f", RuntimeConfig.lowThreshold),
                description: ConfigurationDescriptions.lowThreshold.description
            )
            InspectorValueRow(
                title: ConfigurationDescriptions.startWindows.title,
                value: "\(RuntimeConfig.minStartWindows)",
                description: ConfigurationDescriptions.startWindows.description
            )
            InspectorValueRow(
                title: ConfigurationDescriptions.endWindows.title,
                value: "\(RuntimeConfig.minEndWindows)",
                description: ConfigurationDescriptions.endWindows.description
            )
        }
    }
}

/// Displays identity of the model that is currently bundled into the app.
/// Reads directly from the auto-generated NormalizationConstants so any
/// re-run of `chewsense-motion deploy` is immediately visible here.
struct BundledModelSection: View {
    private var shortSHA: String {
        let sha = NormalizationConstants.MODEL_PACKAGE_SHA256
        guard !sha.isEmpty else { return "unknown" }
        return String(sha.prefix(12)) + "…"
    }

    private var datasetStatus: String {
        let status = NormalizationConstants.DATASET_STATUS
        return status.isEmpty ? "unknown" : status
    }

    private var generatedAt: String {
        let value = NormalizationConstants.GENERATED_AT
        return value.isEmpty ? "unknown" : value
    }

    private var datasetHash: String {
        let value = NormalizationConstants.DATASET_HASH
        return value.isEmpty ? "unknown" : value
    }

    private var wandbRunID: String {
        let value = NormalizationConstants.WANDB_RUN_ID
        return value.isEmpty ? "unknown" : value
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Bundled Model")
                .font(.headline)
            Text("Identity of the model currently shipping in this build. Re-run the pipeline `deploy` command and rebuild the app to update these values.")
                .font(.caption)
                .foregroundStyle(.secondary)

            InspectorValueRow(
                title: "Model ID",
                value: NormalizationConstants.MODEL_ID,
                description: "Local registry identifier for the bundled checkpoint."
            )
            InspectorValueRow(
                title: "Package SHA-256",
                value: shortSHA,
                description: "First 12 chars of the SHA-256 of ChewNet.mlpackage. Changes whenever a new model is deployed."
            )
            InspectorValueRow(
                title: "Generated At",
                value: generatedAt,
                description: "Timestamp the pipeline wrote NormalizationConstants.swift."
            )
            InspectorValueRow(
                title: "Dataset Hash",
                value: datasetHash,
                description: "Hash of the dataset manifest used to train the bundled model."
            )
            InspectorValueRow(
                title: "Dataset Status",
                value: datasetStatus,
                description: "Lineage notes for the bundled model's training dataset."
            )
            InspectorValueRow(
                title: "W&B Run",
                value: wandbRunID,
                description: "Weights & Biases run id, when training logged to W&B."
            )
        }
    }
}

/// The exported motion-only eating detector and the flag that decides
/// whether it drives Live or only runs in shadow.
struct ExportedEatingDetectorSection: View {
    @ObservedObject var config: DetectionConfiguration
    let isEditable: Bool
    private var spec: Result<EatingDetectorSpec, Error> { EatingDetectorSpec.bundled }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Motion-Only Eating Detector")
                .font(.headline)
            switch spec {
            case .success(let spec):
                Picker("Detector", selection: $config.eatingDetectorMode) {
                    ForEach(EatingDetectorMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .disabled(!isEditable)
                Text(config.eatingDetectorMode.explanation)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                InspectorValueRow(
                    title: "Model ID",
                    value: spec.modelId,
                    description: "Exported by `chewsense-motion eating-export` from experiment \(spec.provenance.runId)."
                )
                InspectorValueRow(
                    title: "Status",
                    value: spec.status,
                    description: "Not promoted. Python and Swift outputs match on recorded sessions; device validation is outstanding."
                )
                InspectorValueRow(
                    title: "Decision",
                    value: String(format: "p ≥ %.2f, every %.1f s", spec.decision.threshold, spec.decision.strideS),
                    description: "Threshold chosen on development data; EMA \(spec.decision.decoder.alpha), start after \(spec.decision.decoder.minStartWindows) windows, end after \(spec.decision.decoder.minEndWindows)."
                )
            case .failure(let error):
                Text("Unavailable: \(error.localizedDescription)")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
    }
}

private struct InspectorValueRow: View {
    let title: String
    let value: String
    let description: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Text(value)
                    .font(.caption.monospacedDigit())
                    .multilineTextAlignment(.trailing)
                    .lineLimit(3)
                    .minimumScaleFactor(0.75)
            }
            Text(description)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(8)
        .background(Color.gray.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}
