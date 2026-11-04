import Foundation
import AppKit

/// `<workspace>/experiments/eating/pipeline_status.json`, written by
/// `chewsense-motion pipeline-status`. Optional fields keep older files readable.
struct PipelineStatus: Decodable {
    struct Sync: Decodable {
        let clock: String?
        let syncCheck: String?
        let sensorCoverage: Double?
        let motionGaps: Int?
        let qualityChecks: String?
    }

    struct Labels: Decodable {
        let labelledS: Double?
        let chewingS: Double?
        let byTierS: [String: Double]?
        let assistedS: Double?
        let latestAssistedReview: String?
    }

    struct Proposals: Decodable {
        let coverage: Double?
        let reviewItems: Int?
        let labelConflicts: Int?
    }

    struct Recording: Decodable, Identifiable {
        let sessionId: String
        let recordedAt: String?
        let source: String?
        let activity: String?
        let durationS: Double?
        let video: Bool?
        let role: String?
        let sync: Sync?
        let labels: Labels?
        let windows: [String: Int]?
        let split: String?
        let usable: Bool
        let reason: String?
        let proposals: Proposals?
        let attention: String?
        var id: String { sessionId }
    }

    struct SplitCount: Decodable {
        let recordings: Int
        let withChewing: Int
        let labelledWindows: Int
        let chewingWindows: Int
    }

    struct DataSummary: Decodable {
        let usableRecordings: Int
        let rejectedRecordings: Int
        let participants: Int
        let labelledMinutes: Double
        let chewingMinutes: Double
        let verifiedClockChewingMinutes: Double
        let labelledMinutesByTier: [String: Double]
        let labelledWindows: Int
        let chewingWindows: Int
        let bySplit: [String: SplitCount]
        let awaitingAssistedReview: [String]
    }

    struct Scores: Decodable {
        let f1: Double?
        let precision: Double?
        let recall: Double?
        let falsePositiveRate: Double?
        let aucPr: Double?
        let f1Ci95: [Double?]?
        let recordings: Int?
        let chewingWindows: Int?
        let nonChewingWindows: Int?
        let boutsDetected: Int?
        let boutsReference: Int?
        let falseBouts: Int?
        let falseBoutsPerHour: Double?
        let onsetLatencyMedianS: Double?
    }

    struct Comparison: Decodable, Identifiable {
        let model: String
        let selected: Bool
        let devF1: Double?
        let devAucPr: Double?
        let testF1: Double?
        let latencyMs: Double?
        var id: String { model }
    }

    struct RecordingError: Decodable, Identifiable {
        let sessionId: String
        let split: String
        let recall: Double?
        let falsePositiveRate: Double?
        let chewingWindows: Int?
        let nonChewingWindows: Int?
        let falseBouts: Int?
        var id: String { "\(split)-\(sessionId)" }
    }

    struct ContextError: Decodable, Identifiable {
        let context: String
        let split: String
        let falsePositiveRate: Double?
        let recall: Double?
        let negatives: Int?
        let positives: Int?
        var id: String { "\(split)-\(context)" }
    }

    struct Timeline: Decodable {
        let split: String
        let tEnd: [Double]
        let probability: [Double?]
        let label: [Int]
        let decoded: [Int]
        let bouts: [[Double]]
        let humanIntervals: [[StringOrNumber]]
        let guidedContext: [[StringOrNumber]]
        let falsePositiveWindows: Int
        let falseNegativeWindows: Int
    }

    struct LiveModel: Decodable {
        let modelId: String?
        let status: String?
        let runId: String?
        let threshold: Double?
        let modeInLive: String?
        let role: String?
    }

    struct LiveModels: Decodable {
        let exportedDetector: LiveModel?
        let chewnet: LiveModel?
        let physicalDeviceValidation: String?
    }

    struct Parity: Decodable {
        let status: String?
        let recordings: Int?
        let recordingsPassed: Int?
        let scoredWindows: Int?
        let featureValuesBitIdentical: Double?
        let maxProbabilityAbsDiff: Double?
        let stateDisagreements: Int?
        let inputs: String?
    }

    struct Model: Decodable {
        let trained: Bool
        let runId: String?
        let finishedAt: String?
        let selectedModel: String?
        let threshold: Double?
        let trainedOnCurrentData: Bool?
        let recordingsNotInRun: [String]?
        let development: Scores?
        let test: Scores?
        let comparison: [Comparison]?
        let testConsultations: Int?
        let reportPath: String?
        let errorsByRecording: [RecordingError]?
        let errorsByContext: [ContextError]?
        let timelines: [String: Timeline]?
        let live: LiveModels?
        let parity: Parity?
    }

    struct Rate: Decodable {
        let rate: Double?
        let low: Double?
        let high: Double?
    }

    struct Effort: Decodable {
        let manualS: Double?
        let assistedS: Double?
        let status: String?
    }

    struct Assist: Decodable {
        let recordings: Int?
        let windowsScored: Int?
        let coverage: Rate?
        let autoChewingPrecision: Rate?
        let autoNotChewingPrecision: Rate?
        let chewingRecoveredAutomatically: Rate?
        let falsePositiveWindows: Int?
        let falseNegativeWindows: Int?
        let reviewWindows: Int?
        let videoUnavailableWindows: Int?
        let effortModel: Effort?
    }

    struct Run: Decodable, Identifiable {
        let runId: String?
        let finishedAt: String?
        let testEvaluated: Bool?
        let selectedModel: String?
        let datasetFingerprint: String?
        let recordings: Int?
        let note: String?
        let devF1: Double?
        let testF1: Double?
        var id: String { runId ?? UUID().uuidString }
    }

    struct Next: Decodable, Identifiable {
        let title: String?
        let reason: String?
        let command: String?
        var id: String { (title ?? "") + (reason ?? "") }
    }

    let generatedAt: String
    let recordings: [Recording]
    let data: DataSummary
    let model: Model
    let assistedLabeling: Assist?
    let history: [Run]
    let next: [Next]
    let commands: [String: String]
    let limitations: [String]
}

/// Mixed JSON arrays such as `[start, end, "chewing", "human_coarse"]`.
enum StringOrNumber: Decodable {
    case string(String), number(Double)

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Double.self) { self = .number(value) } else {
            self = .string(try container.decode(String.self))
        }
    }

    var number: Double? { if case .number(let value) = self { value } else { nil } }
    var string: String? { if case .string(let value) = self { value } else { nil } }
}

/// Loads the status file and runs the two safe pipeline commands: refreshing
/// the status and a development-only experiment that never consults the
/// locked test. Locked-test runs stay a deliberate command-line action.
@MainActor
final class TrainingStatusStore: ObservableObject {
    @Published private(set) var workspace: URL?
    @Published private(set) var status: PipelineStatus?
    @Published private(set) var loadError: String?
    @Published private(set) var running: String?
    @Published private(set) var log = ""
    @Published private(set) var lastRunFailed = false

    private var process: Process?

    /// The workspace is the first ancestor of the review folder containing `experiments/eating`.
    func adopt(reviewRoot: URL?) {
        var candidate = reviewRoot
        while let url = candidate, url.pathComponents.count > 1 {
            if FileManager.default.fileExists(atPath: url.appending(path: "experiments/eating").path) {
                if workspace != url { workspace = url; reload() }
                return
            }
            candidate = url.deletingLastPathComponent()
        }
    }

    func choose() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.message = "Choose the ChewSense workspace folder (contains experiments/eating)"
        if panel.runModal() == .OK, let url = panel.url {
            workspace = url
            reload()
        }
    }

    var statusURL: URL? { workspace?.appending(path: "experiments/eating/pipeline_status.json") }

    func reload() {
        guard let statusURL else { return }
        guard FileManager.default.fileExists(atPath: statusURL.path) else {
            status = nil
            loadError = "No status yet. Refresh to create it (chewsense-motion pipeline-status)."
            return
        }
        do {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            status = try decoder.decode(PipelineStatus.self, from: Data(contentsOf: statusURL))
            loadError = nil
        } catch {
            loadError = "Could not read \(statusURL.lastPathComponent): \(error.localizedDescription)"
        }
    }

    private var tool: URL? {
        guard let workspace else { return nil }
        let url = workspace.deletingLastPathComponent().appending(path: ".venv/bin/chewsense-motion")
        return FileManager.default.isExecutableFile(atPath: url.path) ? url : nil
    }

    func refresh() { run(["pipeline-status"], title: "Refreshing status") }

    func runDevelopmentCheck() {
        run(["eating-experiment", "--dev-only", "--note", "development check from Review"],
            title: "Development check (locked test untouched)", refreshAfter: true)
    }

    func cancel() { process?.terminate() }

    private func run(_ arguments: [String], title: String, refreshAfter: Bool = false) {
        guard running == nil else { return }
        guard let tool, let workspace else {
            log = "chewsense-motion was not found next to the workspace (.venv/bin). Run `uv sync --locked` in the repository."
            lastRunFailed = true
            return
        }
        let process = Process()
        process.executableURL = tool
        process.arguments = arguments
        process.currentDirectoryURL = workspace.deletingLastPathComponent()
        var environment = ProcessInfo.processInfo.environment
        environment["CHEWSENSE_WORKSPACE"] = workspace.path
        process.environment = environment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let text = String(decoding: handle.availableData, as: UTF8.self)
            guard !text.isEmpty else { return }
            Task { @MainActor in self?.log += text }
        }
        process.terminationHandler = { [weak self] finished in
            Task { @MainActor in
                guard let self else { return }
                pipe.fileHandleForReading.readabilityHandler = nil
                self.lastRunFailed = finished.terminationStatus != 0
                self.log += self.lastRunFailed ? "\n✗ \(title) failed (exit \(finished.terminationStatus))\n" : "\n✓ \(title) finished\n"
                self.running = nil
                self.process = nil
                if refreshAfter && !self.lastRunFailed { self.refresh() } else { self.reload() }
            }
        }
        log = "$ chewsense-motion \(arguments.joined(separator: " "))\n"
        lastRunFailed = false
        running = title
        do {
            try process.run()
            self.process = process
        } catch {
            running = nil
            lastRunFailed = true
            log += "Could not start: \(error.localizedDescription)\n"
        }
    }
}
