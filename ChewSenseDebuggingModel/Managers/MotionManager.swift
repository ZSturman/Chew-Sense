//
//  MotionManager.swift
//  ChewSenseDebuggingModel
//
//  Manages AirPods motion data collection, feature extraction, model inference,
//  and field-test session bundle logging.
//

import Foundation
import CoreMotion
import Combine

#if os(iOS)
import UIKit
import AVFoundation
#endif

/// Quaternion orientation captured from AirPods attitude — passed to the
/// 3D head visualization without leaking CMCore types into the SwiftUI layer.
struct HeadAttitude {
    let w: Double
    let x: Double
    let y: Double
    let z: Double

    static let identity = HeadAttitude(w: 1, x: 0, y: 0, z: 0)
}

/// A single motion sample from AirPods
struct MotionSample {
    let timestamp: TimeInterval
    let ax: Double
    let ay: Double
    let az: Double
    let gx: Double
    let gy: Double
    let gz: Double
}

/// User feedback types captured during a field test.
enum FeedbackEventType: String, CaseIterable {
    case falsePositive = "false_positive"
    case falseNegative = "false_negative"
    case correct = "correct"

    var title: String {
        switch self {
        case .falsePositive: return "False Positive"
        case .falseNegative: return "False Negative"
        case .correct: return "Correct"
        }
    }

    func correctedState(predictedState: String) -> String {
        switch self {
        case .falsePositive:
            return "idle"
        case .falseNegative:
            return "chewing"
        case .correct:
            return predictedState
        }
    }
}

private struct LastPredictionWindow {
    let timestamp: String
    let windowStart: TimeInterval
    let windowEnd: TimeInterval
    let probability: Double
    let smoothed: Double
    let state: String
    let featuresJSON: String
}

/// Manages motion data collection and chewing detection
final class MotionManager: ObservableObject {

    // MARK: - Configuration

    private let windowDuration: TimeInterval = 0.75
    private let stepDuration: TimeInterval = 0.10

    // MARK: - Dependencies

    private let headphoneManager = CMHeadphoneMotionManager()
    private let detector: ChewDetector
    private let strokeCounter = ChewStrokeCounter()
    private let phaseCounter = ChewPhaseCounter()
    private var phaseStrokes: [ChewStroke] = []
    private let biteSegmenter = BiteSegmenter()
    let mealMetrics = MealMetricsTracker()
    private let model = ChewModel.shared
    private let motionQueue: OperationQueue = {
        let queue = OperationQueue()
        queue.name = "com.chewsense.debugging-model.motion"
        queue.maxConcurrentOperationCount = 1
        queue.qualityOfService = .userInitiated
        return queue
    }()

    // MARK: - State

    private var samples: [MotionSample] = []
    private var lastFeatureTime: TimeInterval = 0
    private var lastWindow: LastPredictionWindow?
    private var sessionBaseTimestamp: TimeInterval?
    private var recordedPredictions: [LastPredictionWindow] = []
    /// Counter used to throttle waveform/attitude UI updates to ~20 Hz (every 5th
    /// sample at ~100 Hz).
    private var vizSampleCounter: Int = 0

    // MARK: - Published Properties

    @Published var isRunning: Bool = false
    @Published var statusText: String = "Idle"
    @Published var latestProb: Double = 0.0
    @Published var latestSmoothed: Double = 0.0
    @Published var isChewing: Bool = false
    @Published var logPreview: [String] = []
    @Published var mouthShape: MouthShapeState = .neutral
    @Published var currentSessionURL: URL?
    @Published var latestCompletedSessionURL: URL?
    @Published var isFinalizingSession: Bool = false
    @Published var feedbackCount: Int = 0
    @Published var livePredictionSpans: [PredictionSpan] = []
    @Published var liveTimelineDuration: Double = 0
    @Published var recordingStartedAt: Date?
    /// Rolling 200-sample buffer (~2 s at 100 Hz) for the live waveform view.
    @Published var recentSamples: [MotionSample] = []
    /// Latest AirPod quaternion for the 3D head orientation view.
    @Published var latestAttitude: HeadAttitude = .identity
    #if os(iOS)
    @Published var captureSession: AVCaptureSession?
    #endif

    // MARK: - Session Logging

    private var logLines: [String] = []
    private var sessionID: String?
    private var motionFileHandle: FileHandle?
    private var predictionsFileHandle: FileHandle?
    private var feedbackFileHandle: FileHandle?
    private var videoRecorder: DebugVideoRecorder?

    // MARK: - Init

    init(detectorConfig: ChewDetectorConfig = .default) {
        detector = ChewDetector(config: detectorConfig)
    }

    // MARK: - Control

    #if os(iOS)
    /// Configures the AVCaptureSession (asking for camera permission if
    /// needed) and publishes it so the composer view can show a live preview
    /// before the user starts recording.
    func prepareCameraPreview() {
        guard captureSession == nil else { return }

        let activate: () -> Void = { [weak self] in
            guard let self else { return }
            let recorder = self.videoRecorder ?? DebugVideoRecorder()
            self.videoRecorder = recorder
            self.captureSession = recorder.exposedSession
            // startPreview() calls configureIfNeededLocked() internally on sessionQueue;
            // calling configureSessionOnly() here (sessionQueue.sync from main) would
            // cause a priority inversion via AVFoundation's internal default-QoS queues.
            recorder.startPreview()
        }

        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            activate()
        case .notDetermined:
            statusText = "Waiting for camera permission"
            AVCaptureDevice.requestAccess(for: .video) { granted in
                DispatchQueue.main.async {
                    if granted {
                        activate()
                    } else {
                        self.statusText = "Camera permission is required to record review video."
                    }
                }
            }
        case .denied, .restricted:
            statusText = "Camera permission is required to record review video."
        @unknown default:
            statusText = "Camera permission could not be verified."
        }
    }

    /// Tear down preview when the composer view disappears and we're not
    /// actively recording. Keeping the recorder configured between sessions
    /// avoids the connection-reconfigure-while-running pattern that was
    /// truncating recordings on Mac Catalyst.
    func stopCameraPreview() {
        guard !isRunning else { return }
        captureSession = nil
        let recorder = videoRecorder
        videoRecorder = nil
        recorder?.releaseSession()
    }

    private func activateAudioSession() {
        let audio = AVAudioSession.sharedInstance()
        do {
            try audio.setCategory(.playback, options: [.mixWithOthers])
            try audio.setActive(true)
        } catch {
            statusText = "Audio route setup failed: \(error.localizedDescription)"
        }
    }

    #endif

    /// Session-level metadata captured at recording start. Detection
    /// parameters are NOT exposed here — they always come from the bundled
    /// model via `ChewDetectorConfig.fromGenerated()`.
    struct SessionMetadataInput {
        var testScenario: String = ""
        var sessionNotes: String = ""
        var configJSON: String? = nil
        /// Activity slug (e.g. "head_shake", "eating_soft"). Required.
        var activitySlug: String = "custom"
        /// Display label for the activity (free-form when slug == custom).
        var activityLabel: String = ""
        /// One of "positive", "negative", "mixed", "unknown".
        var expectedLabelClass: String = "unknown"
        /// True if the user wants this session promoted to the locked
        /// validation set on import.
        var goldLockCandidate: Bool = false
    }

    func start(metadata: SessionMetadataInput) {
        guard !isRunning, !isFinalizingSession else { return }

        #if os(iOS)
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            startSession(metadata: metadata)
        case .notDetermined:
            statusText = "Waiting for camera permission"
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                DispatchQueue.main.async {
                    guard let self else { return }
                    if granted {
                        self.startSession(metadata: metadata)
                    } else {
                        self.statusText = "Camera permission is required to record review video."
                    }
                }
            }
        case .denied, .restricted:
            statusText = "Camera permission is required to record review video."
        @unknown default:
            statusText = "Camera permission could not be verified."
        }
        #else
        startSession(metadata: metadata)
        #endif
    }

    private func startSession(metadata: SessionMetadataInput) {
        guard !isRunning, !isFinalizingSession else { return }

        #if os(iOS)
        activateAudioSession()
        #endif

        guard headphoneManager.isDeviceMotionAvailable else {
            DispatchQueue.main.async {
                self.statusText = "Headphone motion not available. Ensure AirPods Pro/Max/4 are connected."
            }
            return
        }

        // Detection parameters always come from the bundled model — never
        // from the UI. This is the single source of truth for runtime
        // tuning; to change it, retrain and re-deploy.
        let activeDetectorConfig = ChewDetectorConfig.fromGenerated()
        detector.updateConfig(activeDetectorConfig)
        detector.reset()
        resetRuntimeState()

        do {
            try createSessionBundle(
                metadata: metadata,
                detectorConfig: activeDetectorConfig
            )
        } catch {
            closeSessionFiles()
            #if os(iOS)
            captureSession = nil
            let recorder = videoRecorder
            videoRecorder = nil
            recorder?.releaseSession()
            #endif
            currentSessionURL = nil
            DispatchQueue.main.async {
                self.statusText = "Could not start session: \(error.localizedDescription)"
            }
            return
        }

        headphoneManager.startDeviceMotionUpdates(to: motionQueue) { [weak self] motion, error in
            guard let self = self, let motion = motion, error == nil else { return }
            self.handleMotion(motion)
        }

        DispatchQueue.main.async {
            self.isRunning = true
            self.statusText = "Running"
        }
    }

    func stop() {
        guard isRunning else { return }

        isFinalizingSession = true
        statusText = "Finishing video..."

        headphoneManager.stopDeviceMotionUpdates()

        let finishedSessionURL = currentSessionURL
        let finish: () -> Void = { [weak self] in
            guard let self else { return }
            self.isRunning = false
            self.isFinalizingSession = false
            self.recordingStartedAt = nil
            self.statusText = "Stopped"
            self.latestCompletedSessionURL = finishedSessionURL
            #if os(iOS)
            // Keep the recorder configured but stop the running preview so the
            // next New flow can resume quickly without reconfiguring inputs.
            self.captureSession = nil
            self.videoRecorder?.stopPreview()
            #endif
        }

        // Finalize labels (write `label` column into motion.csv and rewrite
        // predictions.csv from the in-memory record). Runs on motionQueue so
        // it doesn't block the main thread; we only expose the completed
        // session URL once the artifacts are ready for review playback.
        let finalizeAndFinish: () -> Void = { [weak self] in
            guard let self else { return }
            self.motionQueue.addOperation { [weak self] in
                guard let self else { return }
                if let url = finishedSessionURL {
                    DispatchQueue.main.async {
                        self.statusText = "Finalizing labels..."
                    }
                    self.finalizeSessionArtifacts(at: url)
                }
                DispatchQueue.main.async {
                    finish()
                }
            }
        }

        let recorder = videoRecorder
        motionQueue.addOperation { [weak self] in
            self?.closeSessionFiles()
            DispatchQueue.main.async {
                if let recorder {
                    recorder.stop { result in
                        switch result {
                        case .failure(let error):
                            self?.statusText = "Recording finalize failed: \(error.localizedDescription)"
                            print("[MotionManager] video stop failed: \(error)")
                        case .success(let url):
                            let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
                            let bytes = (attrs?[.size] as? NSNumber)?.int64Value ?? 0
                            let mb = Double(bytes) / 1_048_576.0
                            self?.statusText = String(format: "Saved video: %.1f MB", mb)
                        }
                        finalizeAndFinish()
                    }
                } else {
                    finalizeAndFinish()
                }
            }
        }
    }

    // MARK: - Session Artifact Finalization

    /// Re-writes `predictions.csv` from the in-memory record (defends
    /// against partial flushes) and adds a per-row `label` column to
    /// `motion.csv` so that playback/review can align labels with motion
    /// timestamps without a manual labeling pass.
    private func finalizeSessionArtifacts(at sessionURL: URL) {
        // Snapshot to avoid races with any in-flight motion handling.
        let predictions = self.recordedPredictions
        rewritePredictionsCSV(at: sessionURL, predictions: predictions)
        labelMotionCSV(at: sessionURL, predictions: predictions)
        writeBiteAndChewArtifacts(at: sessionURL)
    }

    /// Persist `bite_events.csv`, `chew_events.csv`, and `meal_metrics.json`
    /// alongside the rest of the field-test bundle. Schemas match the
    /// canonical Python writers in
    /// `05-FutureStages/02-BiteSegmentation/bite_detector.py`.
    private func writeBiteAndChewArtifacts(at sessionURL: URL) {
        let bites = self.biteSegmenter.bites
        let strokes = self.strokeCounter.strokes

        // bite_events.csv
        var biteCSV = "start_time,end_time,duration,confidence,chew_count\n"
        for b in bites {
            biteCSV += String(
                format: "%.6f,%.6f,%.6f,%.6f,%d\n",
                b.startTime, b.endTime, b.duration, b.confidence, b.chewCount
            )
        }
        try? biteCSV.data(using: .utf8)?.write(
            to: sessionURL.appendingPathComponent("bite_events.csv"),
            options: .atomic
        )

        // chew_events.csv (with bite_index attribution where possible)
        var chewCSV = "timestamp,bite_index,confidence,source\n"
        for s in strokes {
            let biteIdx = bites.firstIndex(where: { s.timestamp >= $0.startTime && s.timestamp < $0.endTime }) ?? -1
            chewCSV += String(
                format: "%.6f,%d,%.6f,%@\n",
                s.timestamp, biteIdx, s.confidence, s.source.rawValue as CVarArg
            )
        }
        try? chewCSV.data(using: .utf8)?.write(
            to: sessionURL.appendingPathComponent("chew_events.csv"),
            options: .atomic
        )

        // meal_metrics.json — snapshot via main-actor tracker. We're on the
        // motionQueue here; bounce to the main actor synchronously via
        // DispatchQueue if needed, but the tracker's snapshot is just a
        // read of plain values, so a property capture is safe.
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            let payload = self.mealMetrics.snapshotJSON()
            if let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys]) {
                try? data.write(
                    to: sessionURL.appendingPathComponent("meal_metrics.json"),
                    options: .atomic
                )
            }
        }
    }

    private func rewritePredictionsCSV(at sessionURL: URL, predictions: [LastPredictionWindow]) {
        let url = sessionURL.appendingPathComponent("predictions.csv")
        var csv = "timestamp,window_start,window_end,probability,smoothed,state,features_json\n"
        for window in predictions {
            let fields = [
                window.timestamp,
                String(format: "%.6f", window.windowStart),
                String(format: "%.6f", window.windowEnd),
                String(format: "%.4f", window.probability),
                String(format: "%.4f", window.smoothed),
                window.state,
                window.featuresJSON
            ].map(csvEscape)
            csv += fields.joined(separator: ",") + "\n"
        }

        let tmpURL = url.deletingLastPathComponent().appendingPathComponent(url.lastPathComponent + ".tmp")
        do {
            try csv.data(using: .utf8)?.write(to: tmpURL, options: .atomic)
            if FileManager.default.fileExists(atPath: url.path) {
                _ = try FileManager.default.replaceItemAt(url, withItemAt: tmpURL)
            } else {
                try FileManager.default.moveItem(at: tmpURL, to: url)
            }
        } catch {
            // Non-fatal: the line-by-line file written during recording may
            // still be present. Remove the temp file if it survived.
            try? FileManager.default.removeItem(at: tmpURL)
        }
    }

    private func labelMotionCSV(at sessionURL: URL, predictions: [LastPredictionWindow]) {
        let url = sessionURL.appendingPathComponent("motion.csv")
        guard let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else {
            return
        }

        var lines = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
            .split(separator: "\n", omittingEmptySubsequences: false)
            .map(String.init)
        guard !lines.isEmpty else { return }

        // Header: ensure a `label` column exists.
        var header = lines[0]
            .split(separator: ",", omittingEmptySubsequences: false)
            .map(String.init)
        guard let timestampIndex = header.firstIndex(where: { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "timestamp" }) else {
            return
        }

        let labelIndex: Int
        if let existing = header.firstIndex(where: { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "label" }) {
            labelIndex = existing
        } else {
            header.append("label")
            labelIndex = header.count - 1
        }
        lines[0] = header.joined(separator: ",")

        // Sorted predictions for fast lookup.
        let sorted = predictions.sorted { $0.windowStart < $1.windowStart }
        var cursor = 0

        for rowIndex in 1..<lines.count {
            let raw = lines[rowIndex]
            let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty { continue }

            var columns = raw.split(separator: ",", omittingEmptySubsequences: false).map(String.init)
            while columns.count <= labelIndex {
                columns.append("")
            }

            guard timestampIndex < columns.count,
                  let timestamp = Double(columns[timestampIndex].trimmingCharacters(in: .whitespacesAndNewlines)) else {
                lines[rowIndex] = columns.joined(separator: ",")
                continue
            }

            let isChewing = predictedChewing(at: timestamp, sortedPredictions: sorted, cursor: &cursor)
            columns[labelIndex] = isChewing ? "true" : "false"
            lines[rowIndex] = columns.joined(separator: ",")
        }

        let newText = lines.joined(separator: "\n")
        let tmpURL = url.deletingLastPathComponent().appendingPathComponent(url.lastPathComponent + ".tmp")
        do {
            try newText.data(using: .utf8)?.write(to: tmpURL, options: .atomic)
            _ = try FileManager.default.replaceItemAt(url, withItemAt: tmpURL)
        } catch {
            try? FileManager.default.removeItem(at: tmpURL)
        }
    }

    /// Returns true if `timestamp` falls inside a window with state == "chewing".
    /// Advances `cursor` monotonically — assumes timestamps are non-decreasing,
    /// which is the case for motion.csv rows written sequentially.
    private func predictedChewing(
        at timestamp: TimeInterval,
        sortedPredictions: [LastPredictionWindow],
        cursor: inout Int
    ) -> Bool {
        guard !sortedPredictions.isEmpty else { return false }

        // Advance cursor while the current window ends before the timestamp.
        while cursor < sortedPredictions.count - 1,
              sortedPredictions[cursor].windowEnd <= timestamp {
            cursor += 1
        }

        let window = sortedPredictions[cursor]
        if timestamp >= window.windowStart && timestamp < window.windowEnd {
            return window.state == "chewing"
        }

        // Timestamp is before the first window starts (still in the
        // ramp-up before the first feature window completes) — treat as
        // not chewing, matching the live UI.
        if timestamp < window.windowStart {
            return false
        }

        // Timestamp is past the last window's end — fall back to the last
        // window's state so the trailing 100 ms aren't mislabeled.
        return window.state == "chewing"
    }

    // MARK: - Motion Handling

    private func handleMotion(_ motion: CMDeviceMotion) {
        let now = motion.timestamp

        let ua = motion.userAcceleration
        let rr = motion.rotationRate

        let sample = MotionSample(
            timestamp: now,
            ax: ua.x,
            ay: ua.y,
            az: ua.z,
            gx: rr.x,
            gy: rr.y,
            gz: rr.z
        )

        writeMotionSample(sample)
        samples.append(sample)

        let cutoff = now - windowDuration
        samples.removeAll { $0.timestamp < cutoff }

        // Throttle visualization updates to ~20 Hz (every 5th sample).
        vizSampleCounter += 1
        if vizSampleCounter >= 5 {
            vizSampleCounter = 0
            let capturedSample = sample
            let q = motion.attitude.quaternion
            let attitude = HeadAttitude(w: q.w, x: q.x, y: q.y, z: q.z)
            DispatchQueue.main.async {
                var buf = self.recentSamples
                buf.append(capturedSample)
                if buf.count > 200 { buf.removeFirst(buf.count - 200) }
                self.recentSamples = buf
                self.latestAttitude = attitude
            }
        }

        if now - lastFeatureTime >= stepDuration,
           let first = samples.first,
           now - first.timestamp >= windowDuration * 0.8 {
            lastFeatureTime = now
            processWindow()
        }
    }

    private func processWindow() {
        let windowSamples = samples
        guard let first = windowSamples.first, let last = windowSamples.last else { return }

        let fs = FeatureExtractor.estimateSamplingRate(from: windowSamples)
        let features = FeatureExtractor.extractFeatures(from: windowSamples, samplingRate: fs)
        let normalizedFeatures = NormalizationConstants.normalize(features)

        let tsStr = ISO8601DateFormatter().string(from: Date())
        let featuresJSON = jsonString(features)

        if model.isMouthShapeMode {
            let shape = model.predictMouthShape(features: normalizedFeatures)
            let stateStr = shape.mouthOpenness > 0.3 ? "chewing" : "idle"
            let probability = shape.mouthOpenness

            writePrediction(
                timestamp: tsStr,
                windowStart: first.timestamp,
                windowEnd: last.timestamp,
                probability: probability,
                smoothed: probability,
                state: stateStr,
                featuresJSON: featuresJSON
            )

            DispatchQueue.main.async {
                self.mouthShape = shape
                self.latestProb = probability
                self.latestSmoothed = probability
                self.isChewing = (stateStr == "chewing")
                self.statusText = "Running (mouth shape)"
            }
        } else {
            let prediction = model.predict(features: normalizedFeatures)
            let probability = prediction.eatingProbability
            let detResult = detector.update(probability: probability)
            let stateStr = detResult.state == .chewing ? "chewing" : "idle"

            let windowEnd = last.timestamp
            if model.isTwoHeadMode, let phaseSin = prediction.phaseSin {
                if detResult.state == .chewing {
                    if phaseCounter.push(timestamp: windowEnd, sin: phaseSin),
                       let timestamp = phaseCounter.lastEmittedTimestamp {
                        phaseStrokes.append(
                            ChewStroke(timestamp: timestamp, confidence: 1.0, source: .model)
                        )
                    }
                } else {
                    phaseCounter.pause()
                }
            } else {
                // Compatibility fallback for legacy one-head checkpoints.
                let chewBand = features.indices.contains(7) ? features[7] : 0.0
                strokeCounter.push(timestamp: windowEnd, bandpower: chewBand)
            }
            if detResult.startedEpisode {
                biteSegmenter.episodeStarted(at: first.timestamp)
            }
            var emittedBite: BiteEvent? = nil
            if detResult.endedEpisode {
                emittedBite = biteSegmenter.episodeEnded(
                    at: windowEnd,
                    smoothedProb: detResult.smoothedProb,
                    chewStrokes: activeChewStrokes
                )
            }

            writePrediction(
                timestamp: tsStr,
                windowStart: first.timestamp,
                windowEnd: last.timestamp,
                probability: probability,
                smoothed: detResult.smoothedProb,
                state: stateStr,
                featuresJSON: featuresJSON
            )

            DispatchQueue.main.async {
                self.latestProb = probability
                self.latestSmoothed = detResult.smoothedProb
                self.isChewing = (detResult.state == .chewing)
                self.statusText = "Running (\(stateStr))"
                if let bite = emittedBite {
                    self.mealMetrics.record(bite, now: windowEnd)
                }
            }
        }
    }

    private var activeChewStrokes: [ChewStroke] {
        model.isTwoHeadMode ? phaseStrokes : strokeCounter.strokes
    }

    // MARK: - Feedback

    @discardableResult
    func recordFeedback(eventType: FeedbackEventType, note: String = "") -> Bool {
        let fallbackPredictedState = isChewing ? "chewing" : "idle"

        motionQueue.addOperation { [weak self] in
            guard let self else { return }

            let predictedState = self.lastWindow?.state ?? fallbackPredictedState
            let correctedState = eventType.correctedState(predictedState: predictedState)
            let tsStr = ISO8601DateFormatter().string(from: Date())

            let windowStart = self.lastWindow?.windowStart ?? self.samples.first?.timestamp ?? 0
            let windowEnd = self.lastWindow?.windowEnd ?? self.samples.last?.timestamp ?? windowStart

            let columns = [
                tsStr,
                eventType.rawValue,
                predictedState,
                correctedState,
                String(format: "%.6f", windowStart),
                String(format: "%.6f", windowEnd),
                note
            ].map(self.csvEscape)

            self.appendLine(columns.joined(separator: ",") + "\n", to: self.feedbackFileHandle)

            DispatchQueue.main.async {
                self.feedbackCount += 1
            }
        }

        return true
    }

    // MARK: - Export

    /// Return the current/last field-test session folder URL.
    func exportSessionBundle() -> URL? {
        guard !isRunning, !isFinalizingSession else { return nil }
        closeSessionFiles()
        return currentSessionURL
    }

    func updateSessionNotes(_ notes: String) {
        guard let currentSessionURL else { return }
        try? notes.write(
            to: currentSessionURL.appendingPathComponent("session_notes.txt"),
            atomically: true,
            encoding: .utf8
        )
    }

    // MARK: - Configuration

    func updateDetectorConfig(_ config: ChewDetectorConfig) {
        detector.updateConfig(config)
    }

    // MARK: - Session Bundle

    private func createSessionBundle(
        metadata: SessionMetadataInput,
        detectorConfig: ChewDetectorConfig
    ) throws {
        let sessionNotes = metadata.sessionNotes
        let configJSON = metadata.configJSON
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        let id = "FieldTest-\(formatter.string(from: Date()))"
        sessionID = id

        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        let root = docs.appendingPathComponent("FieldTests", isDirectory: true)
        let sessionURL = root.appendingPathComponent(id, isDirectory: true)
        try? FileManager.default.createDirectory(at: sessionURL, withIntermediateDirectories: true)
        currentSessionURL = sessionURL
        latestCompletedSessionURL = nil

        motionFileHandle = openFile(
            sessionURL.appendingPathComponent("motion.csv"),
            header: "timestamp,ax,ay,az,gx,gy,gz\n"
        )
        predictionsFileHandle = openFile(
            sessionURL.appendingPathComponent("predictions.csv"),
            header: "timestamp,window_start,window_end,probability,smoothed,state,features_json\n"
        )
        feedbackFileHandle = openFile(
            sessionURL.appendingPathComponent("feedback.csv"),
            header: "timestamp,event_type,predicted_state,corrected_state,window_start,window_end,note\n"
        )

        if let configJSON {
            try? configJSON.write(
                to: sessionURL.appendingPathComponent("config.json"),
                atomically: true,
                encoding: .utf8
            )
        }

        try sessionNotes.write(
            to: sessionURL.appendingPathComponent("session_notes.txt"),
            atomically: true,
            encoding: .utf8
        )

        #if os(iOS)
        let recorder = videoRecorder ?? DebugVideoRecorder()
        let videoURL = sessionURL.appendingPathComponent("\(id).mov")
        // startRecording() calls configureIfNeededLocked() internally on sessionQueue;
        // a separate configureSessionOnly() (sessionQueue.sync from main) would cause
        // a priority inversion via AVFoundation's internal default-QoS queues.
        captureSession = recorder.exposedSession
        recorder.startRecording(to: videoURL) { [weak self] error in
            if let error {
                DispatchQueue.main.async {
                    self?.statusText = "Camera recording failed: \(error.localizedDescription)"
                }
                print("[MotionManager] startRecording failed: \(error)")
            }
        }
        videoRecorder = recorder
        recordingStartedAt = Date()
        #endif

        writeSessionMetadata(
            metadata: metadata,
            detectorConfig: detectorConfig,
            sessionURL: sessionURL,
            videoFileName: "\(id).mov"
        )
        appendLogLine("timestamp,probability,smoothed,state")
    }

    private func writeSessionMetadata(
        metadata: SessionMetadataInput,
        detectorConfig: ChewDetectorConfig,
        sessionURL: URL,
        videoFileName: String
    ) {
        let runtime: [String: Any] = [
            "alpha": detectorConfig.alpha,
            "high_threshold": detectorConfig.highThreshold,
            "low_threshold": detectorConfig.lowThreshold,
            "min_start_windows": detectorConfig.minStartWindows,
            "min_end_windows": detectorConfig.minEndWindows,
            "params_source": "bundled_model"
        ]

        let metadataDict: [String: Any] = [
            "session_id": sessionID ?? "",
            "started_at": ISO8601DateFormatter().string(from: Date()),
            "platform": platformName(),
            "device": deviceName(),
            "airpods_type": "",
            "app_version": appVersion(),
            "model_id": NormalizationConstants.MODEL_ID,
            "dataset_hash": NormalizationConstants.DATASET_HASH,
            "dataset_status": NormalizationConstants.DATASET_STATUS,
            "model_package_sha256": NormalizationConstants.MODEL_PACKAGE_SHA256,
            "wandb_run_id": NormalizationConstants.WANDB_RUN_ID,
            "wandb_run_url": NormalizationConstants.WANDB_RUN_URL,
            "model_mode": NormalizationConstants.MODE,
            "runtime_config": runtime,
            "test_scenario": metadata.testScenario,
            "activity": metadata.activitySlug,
            "activity_label": metadata.activityLabel,
            "expected_label_class": metadata.expectedLabelClass,
            "gold_lock_candidate": metadata.goldLockCandidate,
            "video_file": videoFileName,
            "review_status": "unreviewed"
        ]

        if let data = try? JSONSerialization.data(withJSONObject: metadataDict, options: [.prettyPrinted]) {
            try? data.write(to: sessionURL.appendingPathComponent("session_metadata.json"))
        }
    }

    private func openFile(_ url: URL, header: String) -> FileHandle? {
        FileManager.default.createFile(atPath: url.path, contents: header.data(using: .utf8))
        guard let handle = try? FileHandle(forWritingTo: url) else { return nil }
        // `FileHandle(forWritingTo:)` positions the cursor at offset 0 — without
        // seeking to the end, the first row written would overwrite the header
        // bytes we just created, leaving the file with no recognizable header
        // (which broke session export validation downstream).
        do {
            try handle.seekToEnd()
        } catch {
            handle.seekToEndOfFile()
        }
        return handle
    }

    private func closeSessionFiles() {
        [motionFileHandle, predictionsFileHandle, feedbackFileHandle].forEach { handle in
            try? handle?.synchronize()
            try? handle?.close()
        }
        motionFileHandle = nil
        predictionsFileHandle = nil
        feedbackFileHandle = nil
    }

    private func resetRuntimeState() {
        samples.removeAll()
        lastFeatureTime = 0
        lastWindow = nil
        sessionBaseTimestamp = nil
        recordedPredictions.removeAll()
        strokeCounter.reset()
        phaseCounter.reset()
        phaseStrokes.removeAll()
        biteSegmenter.reset()
        DispatchQueue.main.async { [weak self] in self?.mealMetrics.reset() }
        logLines.removeAll()
        feedbackCount = 0
        latestProb = 0
        latestSmoothed = 0
        isChewing = false
        latestCompletedSessionURL = nil
        livePredictionSpans = []
        liveTimelineDuration = 0
        recentSamples = []
        latestAttitude = .identity
        vizSampleCounter = 0
    }

    // MARK: - CSV Logging

    private func writeMotionSample(_ sample: MotionSample) {
        if sessionBaseTimestamp == nil {
            sessionBaseTimestamp = sample.timestamp
        }

        let line = [
            String(format: "%.6f", sample.timestamp),
            String(format: "%.8f", sample.ax),
            String(format: "%.8f", sample.ay),
            String(format: "%.8f", sample.az),
            String(format: "%.8f", sample.gx),
            String(format: "%.8f", sample.gy),
            String(format: "%.8f", sample.gz)
        ].joined(separator: ",") + "\n"
        appendLine(line, to: motionFileHandle)

        if let sessionBaseTimestamp {
            let relativeTime = max(0, sample.timestamp - sessionBaseTimestamp)
            DispatchQueue.main.async {
                self.liveTimelineDuration = max(self.liveTimelineDuration, relativeTime)
            }
        }
    }

    private func writePrediction(
        timestamp: String,
        windowStart: TimeInterval,
        windowEnd: TimeInterval,
        probability: Double,
        smoothed: Double,
        state: String,
        featuresJSON: String
    ) {
        let fields = [
            timestamp,
            String(format: "%.6f", windowStart),
            String(format: "%.6f", windowEnd),
            String(format: "%.4f", probability),
            String(format: "%.4f", smoothed),
            state,
            featuresJSON
        ].map(csvEscape)
        appendLine(fields.joined(separator: ",") + "\n", to: predictionsFileHandle)

        let window = LastPredictionWindow(
            timestamp: timestamp,
            windowStart: windowStart,
            windowEnd: windowEnd,
            probability: probability,
            smoothed: smoothed,
            state: state,
            featuresJSON: featuresJSON
        )
        lastWindow = window
        recordedPredictions.append(window)

        appendLogLine("\(timestamp),\(String(format: "%.4f", probability)),\(String(format: "%.4f", smoothed)),\(state)")

        let base = sessionBaseTimestamp ?? windowStart
        let liveSpan = PredictionSpan(
            startTime: max(0, windowStart - base),
            endTime: max(0, windowEnd - base),
            isChewing: state == "chewing",
            probability: probability,
            smoothedProbability: smoothed
        )

        DispatchQueue.main.async {
            self.livePredictionSpans.append(liveSpan)
            self.liveTimelineDuration = max(self.liveTimelineDuration, liveSpan.endTime)
        }
    }

    private func appendLine(_ line: String, to handle: FileHandle?) {
        guard let data = line.data(using: .utf8) else { return }
        handle?.write(data)
    }

    private func appendLogLine(_ line: String) {
        logLines.append(line)

        let tailCount = 50
        let preview = logLines.count > tailCount ? Array(logLines.suffix(tailCount)) : logLines

        DispatchQueue.main.async {
            self.logPreview = preview
        }
    }

    private func jsonString(_ values: [Double]) -> String {
        let strings = values.map { String(format: "%.8f", $0) }
        return "[" + strings.joined(separator: ",") + "]"
    }

    private func csvEscape(_ value: String) -> String {
        if value.contains(",") || value.contains("\"") || value.contains("\n") {
            return "\"" + value.replacingOccurrences(of: "\"", with: "\"\"") + "\""
        }
        return value
    }

    // MARK: - Metadata Helpers

    private func appVersion() -> String {
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "unknown"
        return "\(version) (\(build))"
    }

    private func platformName() -> String {
        #if targetEnvironment(macCatalyst)
        return "macCatalyst"
        #else
        #if os(iOS)
        return "iOS"
        #elseif os(macOS)
        return "macOS"
        #else
        return "unknown"
        #endif
        #endif
    }

    private func deviceName() -> String {
        #if os(iOS)
        return "\(UIDevice.current.model) \(UIDevice.current.systemVersion)"
        #elseif os(macOS)
        return Host.current().localizedName ?? "Mac"
        #else
        return "unknown"
        #endif
    }
}
