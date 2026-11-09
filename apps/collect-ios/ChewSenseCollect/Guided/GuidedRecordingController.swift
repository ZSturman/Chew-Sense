import Foundation
import Combine
import AudioToolbox
import UIKit

/// Runs one guided experiment: one continuous recording, the instruction
/// engine on the recording's host clock, quality prompts and the sidecar.
final class GuidedRecordingController: ObservableObject {
    enum Stage: Equatable {
        case ready
        case starting
        case recording
        case finishing
        case completed(folder: URL, sessionID: String)
        case failed(String)
    }

    @Published private(set) var stage: Stage = .ready
    @Published private(set) var now: Double = 0
    @Published private(set) var validation: GuidedRunEngine.Validation?
    /// True once the instruction schedule was saved as the recording's chewing labels.
    @Published private(set) var savedInstructedLabels = false
    @Published var cuesEnabled: Bool {
        didSet { UserDefaults.standard.set(cuesEnabled, forKey: "GuidedCuesEnabled") }
    }

    let entry: CatalogEntry
    let engine: GuidedRunEngine
    let repeatOf: String?
    let recorder: RecorderController
    let quality: QualityMonitor
    private let catalog: ExperimentCatalogStore
    private let uploads: UploadManager?
    private var timer: AnyCancellable?
    private var wasCaptureInterrupted = false
    private var startRequestedAt: Date?

    init(entry: CatalogEntry, recorder: RecorderController, quality: QualityMonitor,
         catalog: ExperimentCatalogStore, uploads: UploadManager?,
         onlyActivityIDs: [String]? = nil, repeatOf: String? = nil) {
        self.entry = entry
        self.recorder = recorder
        self.quality = quality
        self.catalog = catalog
        self.uploads = uploads
        self.repeatOf = repeatOf
        engine = GuidedRunEngine(definition: entry.definition, onlyActivityIDs: onlyActivityIDs)
        cuesEnabled = UserDefaults.standard.object(forKey: "GuidedCuesEnabled") as? Bool ?? true
    }

    var isRepeat: Bool { repeatOf != nil }

    /// Everything required before Start is enabled.
    var preflightIssues: [String] {
        var issues: [String] = []
        if entry.definition.airpods.required && !recorder.isMotionAvailable { issues.append("Connect your AirPods") }
        let snap = quality.snapshot
        if entry.definition.camera.faceRequired && snap.faceVisible == false {
            if let b = snap.brightness, b < entry.definition.camera.minBrightness {
                issues.append("Too dark to see your face — turn on Screen light or face a lamp")
            } else {
                issues.append("Bring your face into the camera view")
            }
        }
        return issues
    }

    /// Dim but usable: shown as advice, never blocks.
    var isDimButUsable: Bool {
        let snap = quality.snapshot
        guard entry.definition.camera.faceRequired, snap.faceVisible == true, let b = snap.brightness else { return false }
        return b < entry.definition.camera.minBrightness
    }

    func prepare() {
        recorder.videoFrameObserver = quality.frameObserver
        recorder.prepare()
        recorder.startCameraPreview()
        quality.start(motionFresh: { [weak self] in self?.recorder.motionIsFresh() ?? false },
                      captureRunning: { [weak self] in !(self?.recorder.isCaptureInterrupted ?? false) })
        recorder.onMotionAvailabilityChanged = { [weak self] available in
            guard let self, self.stage == .recording else { return }
            self.engine.note(available ? "motion_restored" : "motion_lost", at: self.recorder.hostTimeNow)
        }
    }

    func teardown() {
        timer = nil
        quality.stop()
        recorder.onMotionAvailabilityChanged = nil
        UIApplication.shared.isIdleTimerDisabled = false
    }

    func begin() {
        guard stage == .ready else { return }
        stage = .starting
        startRequestedAt = Date()
        UIApplication.shared.isIdleTimerDisabled = true
        recorder.startGuided(experimentID: entry.definition.experimentID) { [weak self] folder in
            self?.recordingSaved(folder)
        }
        timer = Timer.publish(every: 0.1, on: .main, in: .common).autoconnect().sink { [weak self] _ in
            self?.tick()
        }
    }

    func endEarly() {
        guard stage == .recording else { return }
        engine.abandon(at: recorder.hostTimeNow)
        finish()
    }

    private func tick() {
        let t = recorder.hostTimeNow
        now = t
        switch stage {
        case .starting:
            if recorder.isRecording {
                engine.start(at: t)
                stage = .recording
                cue(strong: true)
            } else if let requested = startRequestedAt, Date().timeIntervalSince(requested) > 10 {
                stage = .failed("Recording did not start. Check camera and AirPods, then try again.")
                teardown()
            }
        case .recording:
            let interrupted = recorder.isCaptureInterrupted
            if interrupted != wasCaptureInterrupted {
                engine.note(interrupted ? "capture_interrupted" : "capture_resumed", at: t)
                wasCaptureInterrupted = interrupted
            }
            for effect in engine.tick(at: t, quality: quality.snapshot) {
                switch effect {
                case .activityChanged, .restarted: cue(strong: false)
                case .paused: warn()
                case .resumed: break
                case .finished: cue(strong: true)
                }
            }
            if engine.phase == .finished { finish() }
            // A hard ceiling so a stalled experiment cannot record indefinitely.
            if let started = engine.startedAt, t - started > engine.plannedSeconds * 3 + 120 {
                engine.abandon(at: t)
                finish()
            }
        default:
            break
        }
    }

    private func finish() {
        guard stage == .recording else { return }
        stage = .finishing
        timer = nil
        recorder.stopGuided()
    }

    private func recordingSaved(_ folder: URL?) {
        guard let folder else {
            if stage != .finishing {
                stage = .failed("Recording could not start. Check camera and AirPods permissions.")
            } else {
                stage = .failed("The recording could not be saved.")
            }
            teardown()
            return
        }
        do {
            let manifest = try JSONSerialization.jsonObject(
                with: Data(contentsOf: folder.appendingPathComponent("session.json"))) as? [String: Any] ?? [:]
            guard let clock = manifest["clock_sync"] as? [String: Any],
                  let origin = clock["video_clock_origin_host_time_s"] as? Double else {
                throw Err("session.json has no video clock origin; guided timing was not written.")
            }
            let duration = (manifest["capture"] as? [String: Any])?["duration_s"] as? Double ?? 0
            let coverage = recorder.motionCoverage(videoDuration: duration)
            let sessionID = manifest["session_id"] as? String ?? folder.lastPathComponent
            let data = try engine.sidecar(sessionID: sessionID, experimentSHA256: entry.definitionSHA256,
                                          rawDefinition: entry.rawDefinition,
                                          catalogVersion: catalog.catalog.catalogVersion,
                                          videoClockOrigin: origin, motionCoverage: coverage,
                                          repeatOf: repeatOf)
            try data.write(to: folder.appendingPathComponent("guided_session.json"), options: .atomic)
            let result = engine.validate(motionCoverage: coverage)
            validation = result
            if result.complete { catalog.recordLocalCompletion(of: entry.definition.experimentID) }
            writeInstructedLabels(folder: folder, manifest: manifest, origin: origin, duration: duration)
            // Sent when the wearer closes the completion screen, so the
            // instructed spans can still be adjusted first.
            stage = .completed(folder: folder, sessionID: sessionID)
        } catch {
            stage = .failed(error.localizedDescription)
        }
        teardown()
    }

    /// Saves the instruction schedule as the recording's chewing spans,
    /// assuming the wearer followed it. The spans stay editable until the
    /// recording is sent; free-form experiments are left for manual marking.
    private func writeInstructedLabels(folder: URL, manifest: [String: Any], origin: Double, duration: Double) {
        guard let motion = (manifest["motion"] as? [String: Any])?["path"] as? String,
              let intervals = engine.instructedCoarseLabels(videoClockOrigin: origin, duration: duration) else { return }
        do {
            try CanonicalBundleWriter.writeCoarseLabels(intervals: intervals,
                                                        forMotionCSV: folder.appendingPathComponent(motion))
            recorder.setLabelled(true, for: folder)
            savedInstructedLabels = true
        } catch {
            savedInstructedLabels = false  // Manual coarse review remains available.
        }
    }

    /// The wearer's explicit statement that nothing was eaten or chewed during
    /// the whole recording. Stored as a human coarse decision, distinct from
    /// the instructed schedule, and only offered for food-free experiments.
    func attestNoFood(folder: URL) throws {
        guard entry.definition.labeling.wearerNoFoodAttestation, !entry.definition.food.needsFoodOrDrink else {
            throw Err("This experiment does not support a no-food confirmation.")
        }
        let manifest = try JSONSerialization.jsonObject(
            with: Data(contentsOf: folder.appendingPathComponent("session.json"))) as? [String: Any] ?? [:]
        guard let duration = (manifest["capture"] as? [String: Any])?["duration_s"] as? Double, duration > 0,
              let motion = (manifest["motion"] as? [String: Any])?["path"] as? String else {
            throw BundleError.invalidVideoDuration
        }
        try CanonicalBundleWriter.writeCoarseLabels(
            intervals: [[
                "start_ts": 0.0, "end_ts": duration, "label": "not_chewing", "source": "human_coarse",
                "basis": "wearer_no_food_attestation",
            ]],
            forMotionCSV: folder.appendingPathComponent(motion))
        recorder.setLabelled(true, for: folder)
        uploads?.recordingFinished(folder)
    }

    var timeRemaining: Double { engine.remainingSeconds(at: now) }

    private func cue(strong: Bool) {
        guard cuesEnabled else { return }
        UINotificationFeedbackGenerator().notificationOccurred(strong ? .success : .warning)
        AudioServicesPlaySystemSound(strong ? 1113 : 1057)
    }

    private func warn() {
        guard cuesEnabled else { return }
        UINotificationFeedbackGenerator().notificationOccurred(.error)
    }
}
