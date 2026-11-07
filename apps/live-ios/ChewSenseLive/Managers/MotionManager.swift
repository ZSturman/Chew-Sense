//
//  MotionManager.swift
//  ChewSenseLive
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
    let eatingLogit: Double
    let chewEventLogit: Double?
    let chewEventProbability: Double?
    let phaseSin: Double?
    let phaseCos: Double?
    let phaseConfidence: Double?
    let gapCoverage: Double
    let decodedChewEventTimestamp: Double?
}

/// Manages motion data collection and chewing detection
final class MotionManager: NSObject, ObservableObject, CMHeadphoneMotionManagerDelegate {

    // MARK: - Configuration

    private var windowDuration: TimeInterval { model.isTemporalMode ? 3.0 : 0.75 }
    private let stepDuration: TimeInterval = 0.10

    // MARK: - Dependencies

    private let headphoneManager = CMHeadphoneMotionManager()
    private var motionHealth = HeadphoneMotionHealth()
    private var motionStreamID = UUID()
    private var motionCaptureActive = false
    private var motionMonitoringActive = false
    private var motionRetryTimer: DispatchSourceTimer?
    private var motionCallbackCount = 0
    private var motionSubscriptionCount = 0
    private var lastCallbackSampleTime: Double?
    #if os(iOS)
    private var audioRouteObserver: NSObjectProtocol?
    private var connectionTestPlayer: AVAudioPlayer?
    #endif
    private let detector: ChewDetector
    private let strokeCounter = ChewStrokeCounter()
    private let eventDecoder = ChewEventDecoder()
    private let boutSegmenter = ChewingBoutSegmenter()
    let mealMetrics = MealMetricsTracker()
    private let model = ChewModel.shared
    private let baselineStore = PersonalBaselineStore.shared
    /// Exported motion-only detector (`EatingDetector/eating-detector.json`).
    /// Runs in shadow unless the experimental mode is selected.
    private let eatingRuntime: EatingDetectorRuntime? = try? EatingDetectorRuntime(spec: EatingDetectorSpec.bundled.get())
    private var eatingMode: EatingDetectorMode = .bundledChewNetWithShadow
    private var eatingFileHandle: FileHandle?
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
    private var activeSessionMetadata: SessionMetadataInput?
    private var sessionCreatedAt: String?
    private var requestedVideoOriginHostTime: TimeInterval?
    private var firstMotionHostTime: TimeInterval?
    private var lastMotionHostTime: TimeInterval?
    private var motionSampleCount = 0
    private var motionSampleGapCount = 0
    private var largestMotionSampleGapSec = 0.0
    private var motionInterruptionCount = 0
    /// Counter used to throttle waveform/attitude UI updates to ~20 Hz (every 5th
    /// sample at ~100 Hz).
    private var vizSampleCounter: Int = 0

    // MARK: - Published Properties

    @Published var isRunning: Bool = false
    @Published var isStartingSession: Bool = false
    @Published var statusText: String = "Idle"
    @Published var latestProb: Double = 0.0
    @Published var latestSmoothed: Double = 0.0
    @Published var isChewing: Bool = false
    @Published var logPreview: [String] = []
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
    /// Latest state of the exported detector (shadow or driving).
    @Published var exportedDetectorChewing: Bool = false
    @Published var exportedDetectorProbability: Double?
    @Published private(set) var hasChewNetPrediction = false
    @Published private(set) var hasFreshMotion = false
    @Published private(set) var capturedMotionSamples = 0
    @Published private(set) var audioRouteName = "No audio output selected"
    @Published private(set) var airPodsMessage = "Wear your AirPods and choose them as the audio output."
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
        super.init()
        headphoneManager.delegate = self
        headphoneManager.startConnectionStatusUpdates()
        #if os(iOS)
        updateAudioRoute()
        audioRouteObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            self?.updateAudioRoute()
            self?.motionQueue.addOperation { [weak self] in self?.restartMotionSubscription() }
        }
        #endif
    }

    deinit {
        motionRetryTimer?.cancel()
        headphoneManager.stopDeviceMotionUpdates()
        headphoneManager.stopConnectionStatusUpdates()
        #if os(iOS)
        if let audioRouteObserver { NotificationCenter.default.removeObserver(audioRouteObserver) }
        #endif
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
            if !isRunning { try audio.setCategory(.playback, options: [.mixWithOthers]) }
            try audio.setActive(true)
            updateAudioRoute()
        } catch {
            statusText = "Audio route setup failed: \(error.localizedDescription)"
        }
    }

    private func updateAudioRoute() {
        let outputs = AVAudioSession.sharedInstance().currentRoute.outputs.map(\.portName)
        audioRouteName = outputs.isEmpty ? "No audio output selected" : outputs.joined(separator: ", ")
    }

    func playAirPodsTestSound() {
        activateAudioSession()
        do {
            let player = try AVAudioPlayer(data: AirPodsConnectionTone.data())
            player.volume = 0.35
            connectionTestPlayer = player
            player.prepareToPlay()
            guard player.play() else { throw NSError(domain: "AirPods", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Test sound could not start."]) }
            airPodsMessage = "Test sound plays through \(audioRouteName). Select your AirPods if you hear it from the phone."
        } catch { airPodsMessage = error.localizedDescription }
    }

    #endif

    func prepareAirPodsConnection() {
        guard !isRunning, !isStartingSession, !isFinalizingSession, !hasFreshMotion else { return }
        #if os(iOS)
        activateAudioSession()
        #endif
        beginMotionUpdates(recording: false)
    }

    func retryAirPodsMotion() {
        #if os(iOS)
        activateAudioSession()
        #endif
        airPodsMessage = "Retrying motion. Wear your AirPods and select them under Choose AirPods if needed."
        if !isRunning { beginMotionUpdates(recording: false) }
        else { motionQueue.addOperation { [weak self] in self?.restartMotionSubscription() } }
    }

    func endAirPodsConnectionCheck() {
        guard !isRunning, !isStartingSession else { return }
        motionQueue.addOperation { [weak self] in
            self?.motionMonitoringActive = false
            self?.headphoneManager.stopDeviceMotionUpdates()
            self?.motionRetryTimer?.cancel()
            self?.motionRetryTimer = nil
            self?.motionHealth.invalidateSamples()
            DispatchQueue.main.async { self?.hasFreshMotion = false }
        }
    }

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
        /// Required pre-meal texture choice. Unknown still permits cadence
        /// metrics but can never enable a chew-count goal.
        var mealTexture: MealTexture = .unknown
        /// Optional offline reference; neither detector consumes camera input.
        var recordReferenceVideo: Bool = true
    }

    func start(metadata: SessionMetadataInput) {
        guard !isRunning, !isStartingSession, !isFinalizingSession else { return }

        #if os(iOS)
        if !metadata.recordReferenceVideo {
            stopCameraPreview()
            startSession(metadata: metadata)
            return
        }
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
        guard !isRunning, !isStartingSession, !isFinalizingSession else { return }
        isStartingSession = true

        #if os(iOS)
        activateAudioSession()
        #endif

        guard headphoneManager.isDeviceMotionAvailable else {
            DispatchQueue.main.async {
                self.isStartingSession = false
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
        eatingMode = eatingRuntime == nil ? .bundledChewNetWithShadow : EatingDetectorMode.stored
        let coachingGoals = baselineStore.goals(
            for: metadata.mealTexture,
            capabilities: model.capabilities
        )
        DispatchQueue.main.async { [weak self] in
            self?.mealMetrics.configure(
                texture: metadata.mealTexture,
                goals: coachingGoals
            )
        }

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
                self.isStartingSession = false
                self.statusText = "Could not start session: \(error.localizedDescription)"
            }
            return
        }

        #if !os(iOS)
        beginMotionUpdates()
        #else
        if metadata.recordReferenceVideo {
            statusText = "Starting synchronized recording..."
        } else {
            beginMotionUpdates()
        }
        #endif
    }

    /// Starts AirPods capture only after the video delegate has frozen the
    /// host-clock origin. This prevents negative or first-motion-shifted video
    /// timestamps in evaluation bundles.
    private func beginMotionUpdates(recording: Bool = true) {
        motionQueue.addOperation { [weak self] in
            guard let self else { return }
            if recording {
                self.motionCaptureActive = true
                self.motionCallbackCount = 0
                self.motionSubscriptionCount = 0
                self.lastCallbackSampleTime = nil
            } else {
                guard !self.motionCaptureActive else { return }
                self.motionMonitoringActive = true
            }
            self.motionHealth = HeadphoneMotionHealth()
            self.restartMotionSubscription()
            guard self.motionRetryTimer == nil else { return }
            let timer = DispatchSource.makeTimerSource(queue: .global(qos: .userInitiated))
            timer.schedule(deadline: .now() + .milliseconds(500), repeating: .milliseconds(500))
            timer.setEventHandler { [weak self] in
                self?.motionQueue.addOperation { [weak self] in
                    guard let self, self.motionCaptureActive || self.motionMonitoringActive else { return }
                    let now = ProcessInfo.processInfo.systemUptime
                    guard !self.motionHealth.isFresh(at: now) else { return }
                    DispatchQueue.main.async {
                        self.hasFreshMotion = false
                        self.hasChewNetPrediction = false
                        self.exportedDetectorProbability = nil
                        self.statusText = "Waiting for fresh AirPods motion"
                        self.airPodsMessage = "No fresh motion yet. Select your AirPods and retry the connection."
                    }
                    if self.motionHealth.shouldRestart(at: now) { self.restartMotionSubscription() }
                }
            }
            self.motionRetryTimer = timer
            timer.resume()
        }
        guard recording else { return }
        DispatchQueue.main.async {
            self.hasFreshMotion = false
            self.capturedMotionSamples = 0
            self.isStartingSession = false
            self.isRunning = true
            self.statusText = "Waiting for fresh AirPods motion"
            self.recordingStartedAt = Date()
        }
    }

    /// Serialized with sample handling; retries preserve the session clock and gaps.
    private func restartMotionSubscription() {
        guard motionCaptureActive || motionMonitoringActive else { return }
        motionSubscriptionCount += 1
        motionStreamID = UUID()
        let streamID = motionStreamID
        motionHealth.didRestart(at: ProcessInfo.processInfo.systemUptime)
        headphoneManager.stopDeviceMotionUpdates()
        headphoneManager.startDeviceMotionUpdates(to: motionQueue) { [weak self] motion, error in
            guard let self, self.motionCaptureActive || self.motionMonitoringActive,
                  self.motionStreamID == streamID else { return }
            self.motionCallbackCount += 1
            if let error {
                self.motionHealth.invalidateSamples()
                self.motionInterruptionCount += 1
                DispatchQueue.main.async {
                    self.hasFreshMotion = false
                    self.hasChewNetPrediction = false
                    self.exportedDetectorProbability = nil
                    self.statusText = "Motion interruption: \(error.localizedDescription)"
                    self.airPodsMessage = error.localizedDescription
                }
                self.writeMotionConnectionDiagnostics(reason: "callback_error")
                return
            }
            guard let motion else { return }
            self.lastCallbackSampleTime = motion.timestamp
            let now = ProcessInfo.processInfo.systemUptime
            let wasFresh = self.motionHealth.isFresh(at: now)
            guard self.lastMotionHostTime.map({ motion.timestamp > $0 }) ?? true,
                  [motion.userAcceleration.x, motion.userAcceleration.y, motion.userAcceleration.z,
                   motion.rotationRate.x, motion.rotationRate.y, motion.rotationRate.z].allSatisfy(\.isFinite),
                  self.motionHealth.didReceive(sampleTime: motion.timestamp, at: now)
            else { return }
            if self.motionCaptureActive { self.handleMotion(motion) }
            if !wasFresh {
                DispatchQueue.main.async {
                    self.hasFreshMotion = true
                    self.statusText = self.isRunning ? "Running" : "AirPods motion is ready"
                    self.airPodsMessage = "Fresh AirPods motion is arriving. You can start a model check."
                }
                self.writeMotionConnectionDiagnostics(reason: "fresh_motion")
            }
        }
        writeMotionConnectionDiagnostics(reason: "subscription_started")
    }

    private func writeMotionConnectionDiagnostics(reason: String) {
        let directory: URL
        if motionCaptureActive, let currentSessionURL { directory = currentSessionURL }
        else {
            directory = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("AirPodsConnection")
        }
        let payload: [String: Any] = [
            "reason": reason,
            "authorization_status": CMHeadphoneMotionManager.authorizationStatus().rawValue,
            "motion_available": headphoneManager.isDeviceMotionAvailable,
            "motion_active": headphoneManager.isDeviceMotionActive,
            "subscriptions": motionSubscriptionCount,
            "callbacks": motionCallbackCount,
            "captured_samples": motionCaptureActive ? motionSampleCount : 0,
            "capture_active": motionCaptureActive,
            "host_time": ProcessInfo.processInfo.systemUptime,
            "last_callback_sample_time": lastCallbackSampleTime.map { $0 as Any } ?? NSNull(),
            "last_arrival_time": motionHealth.lastArrivalTime.map { $0 as Any } ?? NSNull(),
            "source": "CoreMotion; no synthetic sample generation",
        ]
        var result = payload
        #if os(iOS)
        result["audio_route"] = AVAudioSession.sharedInstance().currentRoute.outputs.map(\.portName)
        #endif
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try CanonicalCaptureContract.writeJSON(result,
                to: directory.appendingPathComponent("motion-connection.json"))
        } catch { print("[MotionManager] connection diagnostics failed: \(error)") }
    }

    func headphoneMotionManagerDidConnect(_ manager: CMHeadphoneMotionManager) {
        motionQueue.addOperation { [weak self] in self?.restartMotionSubscription() }
    }

    func headphoneMotionManagerDidDisconnect(_ manager: CMHeadphoneMotionManager) {
        motionQueue.addOperation { [weak self] in
            self?.motionHealth.invalidateSamples()
            DispatchQueue.main.async {
                self?.hasFreshMotion = false
                self?.hasChewNetPrediction = false
                self?.exportedDetectorProbability = nil
                self?.statusText = "AirPods disconnected; waiting for motion"
            }
        }
    }

    func stop() {
        guard isRunning else { return }

        isFinalizingSession = true
        statusText = "Finishing video..."

        headphoneManager.stopDeviceMotionUpdates()
        motionQueue.addOperation { [weak self] in
            self?.writeMotionConnectionDiagnostics(reason: "capture_stopped")
            self?.motionCaptureActive = false
            self?.motionMonitoringActive = false
            self?.motionStreamID = UUID()
            self?.headphoneManager.stopDeviceMotionUpdates()
            self?.motionRetryTimer?.cancel()
            self?.motionRetryTimer = nil
            self?.motionHealth.invalidateSamples()
        }

        let finishedSessionURL = currentSessionURL
        let finish: () -> Void = { [weak self] in
            guard let self else { return }
            self.isRunning = false
            self.isStartingSession = false
            self.isFinalizingSession = false
            self.hasFreshMotion = false
            self.recordingStartedAt = nil
            self.statusText = self.motionSampleCount > 0
                ? "Stopped" : "No AirPods samples captured. Check motion access and reconnect your AirPods."
            self.latestCompletedSessionURL = finishedSessionURL
            #if os(iOS)
            // Keep the recorder configured but stop the running preview so the
            // next New flow can resume quickly without reconfiguring inputs.
            self.captureSession = nil
            self.videoRecorder?.stopPreview()
            #endif
        }

        // Finalize model telemetry (write `predicted_eating`, never a truth
        // label, and rewrite predictions.csv from the in-memory record). Runs on motionQueue so
        // it doesn't block the main thread; we only expose the completed
        // session URL once every export and post-meal artifact is present.
        let finalizeAndFinish: () -> Void = { [weak self] in
            guard let self else { return }
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                let mealMetricsPayload = self.mealMetrics.snapshotJSON(
                    capabilities: self.model.capabilities
                )
                self.motionQueue.addOperation { [weak self] in
                    guard let self else { return }
                    if let url = finishedSessionURL {
                        DispatchQueue.main.async {
                            self.statusText = "Finalizing session..."
                        }
                        self.finalizeSessionArtifacts(
                            at: url,
                            mealMetricsPayload: mealMetricsPayload
                        )
                    }
                    DispatchQueue.main.async {
                        finish()
                    }
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

    /// Re-writes `predictions.csv` and materializes per-row model telemetry.
    /// Automatic predictions are never written as human-reviewed truth.
    private func finalizeSessionArtifacts(
        at sessionURL: URL,
        mealMetricsPayload: [String: Any]
    ) {
        // Snapshot to avoid races with any in-flight motion handling.
        let predictions = self.recordedPredictions
        rewritePredictionsCSV(at: sessionURL, predictions: predictions)
        materializePredictionTelemetry(at: sessionURL, predictions: predictions)
        writeBoutAndChewArtifacts(
            at: sessionURL,
            mealMetricsPayload: mealMetricsPayload
        )
        writeCanonicalSessionManifest(at: sessionURL, finalized: true)
    }

    /// Persist learned chew events plus honestly named diagnostic bouts.
    private func writeBoutAndChewArtifacts(
        at sessionURL: URL,
        mealMetricsPayload: [String: Any]
    ) {
        let bouts = self.boutSegmenter.bouts
        let strokes = self.activeChewStrokes

        let videoOrigin = canonicalVideoOriginHostTime
        var boutCSV = "start_host_ts,end_host_ts,start_video_ts,end_video_ts,duration,confidence,chew_count,provenance\n"
        for bout in bouts {
            boutCSV += String(
                format: "%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%d,model_diagnostic\n",
                bout.startTime,
                bout.endTime,
                bout.startTime - videoOrigin,
                bout.endTime - videoOrigin,
                bout.duration,
                bout.confidence,
                bout.chewCount
            )
        }
        try? boutCSV.data(using: .utf8)?.write(
            to: sessionURL.appendingPathComponent("chewing_bouts.csv"),
            options: .atomic
        )

        var chewCSV = "host_timestamp,video_ts,chewing_bout_index,confidence,source,provenance\n"
        for s in strokes {
            let boutIndex = bouts.firstIndex(where: {
                s.timestamp >= $0.startTime && s.timestamp < $0.endTime
            }) ?? -1
            chewCSV += String(
                format: "%.6f,%.6f,%d,%.6f,%@,model_telemetry\n",
                s.timestamp,
                s.timestamp - videoOrigin,
                boutIndex,
                s.confidence,
                s.source.rawValue as CVarArg
            )
        }
        try? chewCSV.data(using: .utf8)?.write(
            to: sessionURL.appendingPathComponent("chew_event_telemetry.csv"),
            options: .atomic
        )

        // The main-actor snapshot is captured before this motion-queue
        // finalizer starts, then written synchronously. The completed bundle
        // is never published before its post-meal summary exists.
        try? LiveCanonicalSessionWriter.writeMealMetrics(
            mealMetricsPayload,
            to: sessionURL
        )
    }

    private func rewritePredictionsCSV(at sessionURL: URL, predictions: [LastPredictionWindow]) {
        let url = sessionURL.appendingPathComponent("predictions.csv")
        let videoOrigin = canonicalVideoOriginHostTime
        var csv = "timestamp,window_start,window_end,window_start_video_ts,window_end_video_ts,eating_logit,probability,smoothed,state,chew_event_logit,chew_event_probability,phase_sin,phase_cos,phase_confidence,gap_coverage,decoded_chew_event_ts,decoded_chew_event_video_ts,features_json\n"
        for window in predictions {
            let fields = [
                window.timestamp,
                String(format: "%.6f", window.windowStart),
                String(format: "%.6f", window.windowEnd),
                String(format: "%.6f", window.windowStart - videoOrigin),
                String(format: "%.6f", window.windowEnd - videoOrigin),
                String(format: "%.8f", window.eatingLogit),
                String(format: "%.4f", window.probability),
                String(format: "%.4f", window.smoothed),
                window.state,
                csvOptional(window.chewEventLogit),
                csvOptional(window.chewEventProbability),
                csvOptional(window.phaseSin),
                csvOptional(window.phaseCos),
                csvOptional(window.phaseConfidence),
                String(format: "%.6f", window.gapCoverage),
                csvOptional(window.decodedChewEventTimestamp),
                csvOptional(window.decodedChewEventTimestamp.map { $0 - videoOrigin }),
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

    private func materializePredictionTelemetry(
        at sessionURL: URL,
        predictions: [LastPredictionWindow]
    ) {
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

        // Header: a derived prediction column, intentionally not `label`.
        var header = lines[0]
            .split(separator: ",", omittingEmptySubsequences: false)
            .map(String.init)
        guard let timestampIndex = header.firstIndex(where: { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "timestamp" }) else {
            return
        }

        let predictionIndex: Int
        if let existing = header.firstIndex(where: {
            $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
                == "predicted_eating"
        }) {
            predictionIndex = existing
        } else {
            header.append("predicted_eating")
            predictionIndex = header.count - 1
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
            while columns.count <= predictionIndex {
                columns.append("")
            }

            guard timestampIndex < columns.count,
                  let timestamp = Double(columns[timestampIndex].trimmingCharacters(in: .whitespacesAndNewlines)) else {
                lines[rowIndex] = columns.joined(separator: ",")
                continue
            }

            let isChewing = predictedChewing(at: timestamp, sortedPredictions: sorted, cursor: &cursor)
            columns[predictionIndex] = isChewing ? "true" : "false"
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
        runExportedDetector(sample)

        let cutoff = now - windowDuration
        samples.removeAll { $0.timestamp < cutoff }

        // Throttle visualization updates to ~20 Hz (every 5th sample).
        vizSampleCounter += 1
        if vizSampleCounter >= 5 {
            vizSampleCounter = 0
            let capturedSample = sample
            let q = motion.attitude.quaternion
            let attitude = HeadAttitude(w: q.w, x: q.x, y: q.y, z: q.z)
            let count = motionSampleCount
            DispatchQueue.main.async {
                self.capturedMotionSamples = count
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

        let temporalWindow = model.isTemporalMode
            ? TemporalWindowBuilder.build(samples: windowSamples, endTimestamp: last.timestamp)
            : nil
        guard !model.isTemporalMode || temporalWindow != nil else { return }
        let prediction = temporalWindow.map { model.predictTemporal(window: $0) }
            ?? model.predict(features: normalizedFeatures)
        let probability = prediction.eatingProbability
        let detResult = detector.update(probability: probability)
        let stateStr = detResult.state == .chewing ? "chewing" : "idle"

        let windowEnd = last.timestamp
        var emittedChew: ChewStroke?
        if model.isTemporalMode,
           let eventLogit = prediction.chewEventLogit,
           let phaseSin = prediction.phaseSin,
           let phaseCos = prediction.phaseCos,
           let phaseConfidence = prediction.phaseConfidence {
            emittedChew = eventDecoder.push(
                TemporalInferenceOutput(
                    timestamp: windowEnd,
                    eatingLogit: prediction.eatingLogit,
                    chewEventLogit: eventLogit,
                    phaseSin: phaseSin,
                    phaseCos: phaseCos,
                    phaseConfidence: phaseConfidence,
                    gapCoverage: prediction.gapCoverage
                )
            )
        } else {
            // Explicit legacy diagnostic fallback. These heuristic peaks
            // never drive personalized coaching or promotion evidence.
            let chewBand = features.indices.contains(7) ? features[7] : 0.0
            strokeCounter.push(timestamp: windowEnd, bandpower: chewBand)
        }
        let legacyDrivesLive = eatingMode == .bundledChewNetWithShadow
        if legacyDrivesLive, detResult.startedEpisode {
            boutSegmenter.episodeStarted(at: windowEnd)
        }
        var emittedBout: ChewingBout?
        if legacyDrivesLive, detResult.endedEpisode {
            emittedBout = boutSegmenter.episodeEnded(
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
            featuresJSON: featuresJSON,
            modelPrediction: prediction,
            decodedChewEventTimestamp: emittedChew?.timestamp
        )

        DispatchQueue.main.async {
            if legacyDrivesLive {
                self.hasChewNetPrediction = true
                self.latestProb = probability
                self.latestSmoothed = detResult.smoothedProb
                self.isChewing = (detResult.state == .chewing)
                self.statusText = "Running (\(stateStr))"
            }
            if self.model.isTemporalMode {
                let confident = prediction.gapCoverage >= RuntimeConfig.minGapCoverage
                    && (prediction.phaseConfidence ?? 0) >= 0.5
                self.mealMetrics.recordInference(
                    stepDurationSec: self.stepDuration,
                    confident: confident
                )
                if self.model.capabilities.temporalChewEventHead, let emittedChew {
                    if self.mealMetrics.recordChew(emittedChew) != nil {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 4) { [weak self] in
                            self?.mealMetrics.clearCue()
                        }
                    }
                }
            }
            if let emittedBout {
                self.mealMetrics.recordDiagnosticBout(emittedBout)
            }
        }
    }

    /// Streams one sample through the exported detector and logs every window
    /// it evaluates. In the experimental mode its state also drives Live.
    private func runExportedDetector(_ sample: MotionSample) {
        guard let eatingRuntime else { return }
        let outputs = eatingRuntime.push(EatingMotionSample(
            timestamp: sample.timestamp,
            ax: sample.ax, ay: sample.ay, az: sample.az,
            gx: sample.gx, gy: sample.gy, gz: sample.gz
        ))
        guard !outputs.isEmpty else { return }
        let drives = eatingMode == .exportedDetectorExperimental
        let origin = canonicalVideoOriginHostTime
        for output in outputs {
            let line = [
                String(format: "%.6f", output.windowEnd),
                String(format: "%.6f", output.windowEnd - origin),
                String(format: "%.6f", output.validFraction),
                output.probability.map { String(format: "%.8f", $0) } ?? "",
                String(format: "%.8f", output.decoderInput),
                String(format: "%.8f", output.smoothed),
                output.chewing ? "chewing" : "idle",
                drives ? "1" : "0",
            ].joined(separator: ",") + "\n"
            appendLine(line, to: eatingFileHandle)
            if drives {
                if output.startedBout { boutSegmenter.episodeStarted(at: output.windowEnd) }
                if output.endedBout {
                    let bout = boutSegmenter.episodeEnded(
                        at: output.windowEnd,
                        smoothedProb: output.smoothed,
                        chewStrokes: activeChewStrokes
                    )
                    if let bout {
                        DispatchQueue.main.async { self.mealMetrics.recordDiagnosticBout(bout) }
                    }
                }
            }
        }
        guard let last = outputs.last else { return }
        DispatchQueue.main.async {
            self.exportedDetectorChewing = last.chewing
            self.exportedDetectorProbability = last.probability
            if drives {
                self.latestProb = last.probability ?? 0
                self.latestSmoothed = last.smoothed
                self.isChewing = last.chewing
                self.statusText = "Running (\(last.chewing ? "chewing" : "idle"), exported detector)"
            }
        }
    }

    private var activeChewStrokes: [ChewStroke] {
        model.isTemporalMode ? eventDecoder.decodedEvents : strokeCounter.strokes
    }

    // MARK: - Feedback

    @discardableResult
    func recordFeedback(eventType: FeedbackEventType, note: String = "") -> Bool {
        let fallbackPredictedState = isChewing ? "chewing" : "idle"

        motionQueue.addOperation { [weak self] in
            guard let self else { return }

            // Record the state the wearer was shown.
            let predictedState = self.eatingMode == .exportedDetectorExperimental
                ? fallbackPredictedState
                : (self.lastWindow?.state ?? fallbackPredictedState)
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

    /// Called only after the wearer verifies a completed calibration meal.
    /// Three verified meals for a texture are required before goals exist.
    func registerVerifiedCalibrationMeal(
        texture: MealTexture,
        medianInterChewIntervalSec: Double,
        medianChewsPerVerifiedFoodEntry: Double? = nil
    ) {
        baselineStore.addVerifiedMeal(
            texture: texture,
            medianInterChewIntervalSec: medianInterChewIntervalSec,
            medianChewsPerVerifiedFoodEntry: medianChewsPerVerifiedFoodEntry
        )
    }

    /// Explicit user confirmation path for a completed session. The verifier
    /// rejects legacy/unpromoted model output and de-duplicates by session ID.
    func verifyCompletedCalibrationMeal(
        at sessionURL: URL
    ) throws -> CalibrationVerificationReceipt {
        try CalibrationMealVerifier.verify(
            sessionURL: sessionURL,
            store: baselineStore
        )
    }

    func calibrationProgress(for texture: MealTexture) -> CalibrationProgress {
        let count = baselineStore.book.verifiedMealCount(for: texture)
        return CalibrationProgress(
            texture: texture,
            verifiedMealCount: count,
            cadenceGoalActivated: count >= 3
        )
    }

    func personalizedGoals(for texture: MealTexture) -> PersonalizedCoachingGoals? {
        baselineStore.goals(for: texture, capabilities: model.capabilities)
    }

    func updatePersonalizedCadenceGoal(
        texture: MealTexture,
        minimumMedianIntervalSec: Double
    ) {
        guard var goals = personalizedGoals(for: texture),
              minimumMedianIntervalSec > 0 else { return }
        goals.minimumMedianInterChewIntervalSec = minimumMedianIntervalSec
        baselineStore.setGoals(goals, for: texture)
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
        let id = "FieldTest-\(formatter.string(from: Date()))-\(UUID().uuidString.prefix(8))"
        sessionID = id
        activeSessionMetadata = metadata
        sessionCreatedAt = ISO8601DateFormatter().string(from: Date())
        requestedVideoOriginHostTime = ProcessInfo.processInfo.systemUptime

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
            header: "timestamp,window_start,window_end,window_start_video_ts,window_end_video_ts,eating_logit,probability,smoothed,state,chew_event_logit,chew_event_probability,phase_sin,phase_cos,phase_confidence,gap_coverage,decoded_chew_event_ts,decoded_chew_event_video_ts,features_json\n"
        )
        feedbackFileHandle = openFile(
            sessionURL.appendingPathComponent("feedback.csv"),
            header: "timestamp,event_type,predicted_state,corrected_state,window_start,window_end,note\n"
        )
        if eatingRuntime != nil {
            eatingFileHandle = openFile(
                sessionURL.appendingPathComponent("eating_detector.csv"),
                header: "window_end,window_end_video_ts,valid_fraction,probability,decoder_input,smoothed,state,drives_live\n"
            )
        }

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
        if metadata.recordReferenceVideo {
            let recorder = videoRecorder ?? DebugVideoRecorder()
            let videoURL = sessionURL.appendingPathComponent("\(id).mov")
            // startRecording() calls configureIfNeededLocked() internally on sessionQueue;
            // a separate configureSessionOnly() (sessionQueue.sync from main) would cause
            // a priority inversion via AVFoundation's internal default-QoS queues.
            captureSession = recorder.exposedSession
            recorder.startRecording(to: videoURL) { [weak self] error in
                if let error {
                    DispatchQueue.main.async {
                        self?.closeSessionFiles()
                        self?.isStartingSession = false
                        self?.currentSessionURL = nil
                        self?.statusText = "Camera recording failed: \(error.localizedDescription)"
                    }
                    print("[MotionManager] startRecording failed: \(error)")
                    return
                }
                self?.writeCanonicalSessionManifest(at: sessionURL, finalized: false)
                self?.beginMotionUpdates()
            }
            videoRecorder = recorder
        }
        #endif

        writeSessionMetadata(
            metadata: metadata,
            detectorConfig: detectorConfig,
            sessionURL: sessionURL,
            videoFileName: metadata.recordReferenceVideo ? "\(id).mov" : nil
        )
        writeCanonicalSessionManifest(at: sessionURL, finalized: false)
        appendLogLine("timestamp,probability,smoothed,state")
    }

    private func writeSessionMetadata(
        metadata: SessionMetadataInput,
        detectorConfig: ChewDetectorConfig,
        sessionURL: URL,
        videoFileName: String?
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
            "eating_detector": [
                "mode": eatingMode.rawValue,
                "model_id": eatingRuntime?.modelID ?? "",
                "drives_live": eatingMode == .exportedDetectorExperimental,
                "log": eatingRuntime == nil ? "" : "eating_detector.csv",
            ] as [String: Any],
            "test_scenario": metadata.testScenario,
            "activity": metadata.activitySlug,
            "activity_label": metadata.activityLabel,
            "expected_label_class": metadata.recordReferenceVideo ? metadata.expectedLabelClass : "unknown",
            "gold_lock_candidate": metadata.recordReferenceVideo && metadata.goldLockCandidate,
            "meal_texture": metadata.mealTexture.rawValue,
            "video_file": videoFileName as Any? ?? NSNull(),
            "record_reference_video": metadata.recordReferenceVideo,
            "review_status": "unreviewed"
        ]

        if let data = try? JSONSerialization.data(withJSONObject: metadataDict, options: [.prettyPrinted]) {
            try? data.write(to: sessionURL.appendingPathComponent("session_metadata.json"))
        }
    }

    private var canonicalVideoOriginHostTime: TimeInterval {
        #if os(iOS)
        if let confirmed = videoRecorder?.recordingStartHostTime {
            return confirmed
        }
        #endif
        return requestedVideoOriginHostTime ?? firstMotionHostTime ?? 0
    }

    private var hasConfirmedVideoOrigin: Bool {
        #if os(iOS)
        return videoRecorder?.recordingStartHostTime != nil
        #else
        return false
        #endif
    }

    private func writeCanonicalSessionManifest(at sessionURL: URL, finalized: Bool) {
        guard let sessionID,
              let metadata = activeSessionMetadata,
              let createdAt = sessionCreatedAt else { return }
        let snapshot = LiveCaptureSnapshot(
            sessionID: sessionID,
            createdAt: createdAt,
            activity: metadata.activitySlug,
            videoFileName: metadata.recordReferenceVideo ? "\(sessionID).mov" : nil,
            motionFileName: "motion.csv",
            videoClockOriginHostTime: canonicalVideoOriginHostTime,
            usedConfirmedVideoOrigin: hasConfirmedVideoOrigin,
            firstMotionHostTime: firstMotionHostTime,
            lastMotionHostTime: lastMotionHostTime,
            motionSampleCount: motionSampleCount,
            motionSampleGapCount: motionSampleGapCount,
            largestMotionSampleGapSec: largestMotionSampleGapSec,
            interrupted: motionInterruptionCount > 0,
            interruptionCount: motionInterruptionCount,
            finalized: finalized,
            modelID: NormalizationConstants.MODEL_ID,
            modelMode: NormalizationConstants.MODE
        )
        do {
            try LiveCanonicalSessionWriter.write(
                snapshot: snapshot,
                sessionURL: sessionURL,
                sourceAppVersion: appVersion()
            )
        } catch {
            print("[MotionManager] session.json write failed: \(error)")
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
        [motionFileHandle, predictionsFileHandle, feedbackFileHandle, eatingFileHandle].forEach { handle in
            try? handle?.synchronize()
            try? handle?.close()
        }
        motionFileHandle = nil
        predictionsFileHandle = nil
        feedbackFileHandle = nil
        eatingFileHandle = nil
    }

    private func resetRuntimeState() {
        samples.removeAll()
        lastFeatureTime = 0
        lastWindow = nil
        sessionBaseTimestamp = nil
        recordedPredictions.removeAll()
        activeSessionMetadata = nil
        sessionCreatedAt = nil
        requestedVideoOriginHostTime = nil
        firstMotionHostTime = nil
        lastMotionHostTime = nil
        motionSampleCount = 0
        motionSampleGapCount = 0
        largestMotionSampleGapSec = 0
        motionInterruptionCount = 0
        strokeCounter.reset()
        eventDecoder.reset()
        boutSegmenter.reset()
        eatingRuntime?.reset()
        exportedDetectorChewing = false
        exportedDetectorProbability = nil
        hasChewNetPrediction = false
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
        if firstMotionHostTime == nil {
            firstMotionHostTime = sample.timestamp
        }
        if let previous = lastMotionHostTime {
            let gap = sample.timestamp - previous
            if gap > CanonicalCaptureContract.motionGapThresholdSeconds {
                motionSampleGapCount += 1
                largestMotionSampleGapSec = max(largestMotionSampleGapSec, gap)
            }
        }
        lastMotionHostTime = sample.timestamp
        motionSampleCount += 1

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
        featuresJSON: String,
        modelPrediction: ChewPrediction? = nil,
        decodedChewEventTimestamp: Double? = nil
    ) {
        let fields = [
            timestamp,
            String(format: "%.6f", windowStart),
            String(format: "%.6f", windowEnd),
            String(format: "%.6f", windowStart - canonicalVideoOriginHostTime),
            String(format: "%.6f", windowEnd - canonicalVideoOriginHostTime),
            String(format: "%.8f", modelPrediction?.eatingLogit ?? 0),
            String(format: "%.4f", probability),
            String(format: "%.4f", smoothed),
            state,
            csvOptional(modelPrediction?.chewEventLogit),
            csvOptional(modelPrediction?.chewEventProbability),
            csvOptional(modelPrediction?.phaseSin),
            csvOptional(modelPrediction?.phaseCos),
            csvOptional(modelPrediction?.phaseConfidence),
            String(format: "%.6f", modelPrediction?.gapCoverage ?? 0),
            csvOptional(decodedChewEventTimestamp),
            csvOptional(decodedChewEventTimestamp.map { $0 - canonicalVideoOriginHostTime }),
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
            featuresJSON: featuresJSON,
            eatingLogit: modelPrediction?.eatingLogit ?? 0,
            chewEventLogit: modelPrediction?.chewEventLogit,
            chewEventProbability: modelPrediction?.chewEventProbability,
            phaseSin: modelPrediction?.phaseSin,
            phaseCos: modelPrediction?.phaseCos,
            phaseConfidence: modelPrediction?.phaseConfidence,
            gapCoverage: modelPrediction?.gapCoverage ?? 0,
            decodedChewEventTimestamp: decodedChewEventTimestamp
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

    private func csvOptional(_ value: Double?) -> String {
        value.map { String(format: "%.8f", $0) } ?? ""
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
