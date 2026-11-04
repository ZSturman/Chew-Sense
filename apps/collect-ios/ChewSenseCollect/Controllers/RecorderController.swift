//
//  RecorderController.swift
//  ChewSenseCollect
//
//  Created by Zachary Sturman on 11/13/25.
//

import Foundation
import AVFoundation
import Combine
import CoreMotion
import QuartzCore

struct RecordingMetadata: Codable {
    var labelled: Bool
    var shared: Bool
}

final class RecorderController: NSObject, ObservableObject {
    // Exposed to UI
    @Published var isRecording = false
    @Published var isPaused = false
    @Published var canResumeRecording = false
    
    @Published var statusText = "Idle"
    @Published var alert: AppAlert?
    @Published var isMotionAvailable: Bool = false
    @Published private(set) var audioRouteName = "iPad / iPhone speaker"
    @Published private(set) var airPodsMessage = "Wear your AirPods and choose them as the audio output."
    private var audioRouteObserver: NSObjectProtocol?
    private var connectionTestPlayer: AVAudioPlayer?

    @Published var latestMotion: (ax: Double, ay: Double, az: Double)?
    @Published private(set) var reviewAudioPermission = "not_requested"
    @Published var captureReviewAudio: Bool {
        didSet { UserDefaults.standard.set(captureReviewAudio, forKey: reviewAudioDefaultsKey) }
    }

    // Capture session to show live preview (owned by VideoIMURecorder internally)
    @Published var captureSession: AVCaptureSession?

    // Last recorded file URL (in app’s tmp, then moved)
    @Published var lastURL: URL?
    @Published var lastSessionFolder: URL?   // Folder where the current session files are saved (named by label prefix)
    
    @Published private(set) var sessionMetadata: [String: RecordingMetadata] = [:]
    private let metadataDefaultsKey = "RecordingSessionMetadata"
    private let reviewAudioDefaultsKey = "CaptureLocalReviewAudio"
    private var currentEatingLabel: Bool?

    private var recorder: VideoIMURecorder?
    /// Guided experiments keep recording through AirPods loss and mark the gap
    /// instead of stopping and splitting the session.
    @Published private(set) var guidedModeActive = false
    var onMotionAvailabilityChanged: ((Bool) -> Void)?
    /// Installed before preview so guided quality checks see preview frames.
    var videoFrameObserver: ((CMSampleBuffer) -> Void)? {
        didSet { recorder?.videoFrameObserver = videoFrameObserver }
    }
    private var cancellables = Set<AnyCancellable>()
    private var imuCheckTimer: DispatchSourceTimer?
    private var isPreparingRecording = false

    override init() {
        self.captureReviewAudio = UserDefaults.standard.bool(forKey: "CaptureLocalReviewAudio")
        super.init()
        loadMetadata()
        updateAudioRoute()
        audioRouteObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            self?.updateAudioRoute()
            self?.recorder?.refreshMotionConnection()
        }
    }

    func activateAudioSession() {
        let audio = AVAudioSession.sharedInstance()
        do {
            // An active recording may own a microphone route. Do not replace
            // its category or interrupt the video to retry headphone motion.
            if !isRecording { try audio.setCategory(.playback, options: [.mixWithOthers]) }
            try audio.setActive(true)
            updateAudioRoute()
        } catch {
            airPodsMessage = "Audio output could not activate: \(error.localizedDescription)"
        }
    }

    private func updateAudioRoute() {
        let names = AVAudioSession.sharedInstance().currentRoute.outputs.map(\.portName)
        audioRouteName = names.isEmpty ? "No audio output selected" : names.joined(separator: ", ")
    }

    func retryAirPodsMotion() {
        guard !CollectionTestMode.isEnabled else { return }
        airPodsMessage = "Retrying motion. Wear the AirPod and select it under Choose AirPods if needed."
        activateAudioSession()
        if let recorder { recorder.reconnectMotion() }
        else { prepare() }
    }

    func playAirPodsTestSound() {
        guard !CollectionTestMode.isEnabled else { return }
        activateAudioSession()
        do {
            let player = try AVAudioPlayer(data: Self.connectionTestTone())
            player.volume = 0.35
            connectionTestPlayer = player
            player.prepareToPlay()
            guard player.play() else { throw Err("Test sound could not start.") }
            airPodsMessage = "Test sound plays through \(audioRouteName). Choose your AirPods if you hear it from the device."
            recorder?.reconnectMotion()
        } catch { airPodsMessage = error.localizedDescription }
    }

    private static func connectionTestTone() -> Data {
        let rate = 22_050, count = rate * 3 / 10
        var data = Data()
        func word<T: FixedWidthInteger>(_ value: T) {
            var little = value.littleEndian
            withUnsafeBytes(of: &little) { data.append(contentsOf: $0) }
        }
        data.append(Data("RIFF".utf8)); word(UInt32(36 + count * 2)); data.append(Data("WAVEfmt ".utf8))
        word(UInt32(16)); word(UInt16(1)); word(UInt16(1)); word(UInt32(rate)); word(UInt32(rate * 2))
        word(UInt16(2)); word(UInt16(16)); data.append(Data("data".utf8)); word(UInt32(count * 2))
        for index in 0..<count {
            let fade = min(1, min(Double(index), Double(count - index - 1)) / 220)
            word(Int16(sin(2 * .pi * 660 * Double(index) / Double(rate)) * fade * 6_000))
        }
        return data
    }

    private func makeTimestampString() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        return formatter.string(from: Date())
    }

    func prepare(startPreview: Bool = true) {
        if CollectionTestMode.isEnabled {
            isMotionAvailable = CollectionTestMode.simulatedMotionAvailable
            statusText = isMotionAvailable ? "Ready – simulated AirPods" : "Ready"
            return
        }
        Task { @MainActor in
            do {
                try await requestPermissions()
                self.activateAudioSession()
                if self.recorder == nil {
                    let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("warmup.mov")
                    let r = VideoIMURecorder(
                        outputURL: tmp,
                        captureReviewAudio: self.captureReviewAudio,
                        reviewAudioPermission: self.reviewAudioPermission
                    )
                    
                    r.onError = { [weak self] err in
                        DispatchQueue.main.async { self?.alert = AppAlert("Recorder error", err.localizedDescription) }
                    }
                    try r.configureSessionOnly()
                    r.videoFrameObserver = self.videoFrameObserver
                    self.recorder = r
                    self.captureSession = r.exposedSession
                    if startPreview { r.startPreview() }
                    
                    r.checkMotionReadiness { [weak self] ready in
                        DispatchQueue.main.async {
                            self?.isMotionAvailable = ready
                        }
                    }
                    
                    let publisher = r.publisher(for: \.isMotionAvailable, options: [.initial, .new])
                        .receive(on: DispatchQueue.main)
                        .sink { [weak self] available in
                            guard let self = self else { return }
                            self.handleMotionAvailabilityChange(available)
                        }
                    self.cancellables.insert(publisher)
                    
                    // New: Setup deviceMotion update handler to update latestMotion
                    r.deviceMotionHandler = { [weak self] deviceMotion in
                        guard let dm = deviceMotion else {
                            DispatchQueue.main.async {
                                self?.latestMotion = nil
                            }
                            return
                        }
                        let ax = dm.userAcceleration.x
                        let ay = dm.userAcceleration.y
                        let az = dm.userAcceleration.z
                        DispatchQueue.main.async {
                            self?.latestMotion = (ax, ay, az)
                        }
                    }
                } else {
                    try self.recorder?.updateReviewAudioConfiguration(
                        enabled: self.captureReviewAudio,
                        permission: self.reviewAudioPermission
                    )
                    self.captureSession = self.recorder?.exposedSession
                    if startPreview { self.recorder?.startPreview() }
                }

                imuCheckTimer?.cancel()
                let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .userInitiated))
                timer.schedule(deadline: .now(), repeating: 2.5)
                timer.setEventHandler { [weak self] in self?.checkIMUReadiness() }
                timer.resume()
                imuCheckTimer = timer

                statusText = "Ready"
            } catch {
                alert = AppAlert("Permissions needed", error.localizedDescription)
            }
        }
    }
    
    func startCameraPreview() {
        recorder?.startPreview()
    }

    func stopCameraPreview() {
        recorder?.stopPreview()
    }
    
    func checkIMUReadiness() {
        recorder?.checkMotionReadiness { [weak self] ready in
            DispatchQueue.main.async {
                self?.handleMotionAvailabilityChange(ready)
            }
        }
    }
    
    private func handleMotionAvailabilityChange(_ available: Bool) {
        let changed = isMotionAvailable != available
        isMotionAvailable = available
        if changed {
            onMotionAvailabilityChanged?(available)
            airPodsMessage = available ? "Receiving fresh AirPods motion." : "Motion stopped. Wear your AirPod, then choose it or retry motion here."
        }

        if guidedModeActive {
            return
        }
        if isRecording && !available {
            // Motion disappeared during an active recording: pause.
            pauseDueToMotionLoss()
        } else if isPaused && available {
            // Motion has come back while we are in paused state:
            // allow the user to resume.
            canResumeRecording = true
        }
    }

    // MARK: - Guided capture helpers

    var hostTimeNow: Double {
        CollectionTestMode.isEnabled ? CACurrentMediaTime() : VideoIMURecorder.hostTimeNow()
    }

    /// True when a headphone sample arrived within `window` seconds.
    func motionIsFresh(window: Double = 0.5) -> Bool {
        if CollectionTestMode.isEnabled { return isMotionAvailable }
        guard isRecording, let last = recorder?.latestMotionHostTime else { return isMotionAvailable }
        return hostTimeNow - last <= window
    }

    var isCaptureInterrupted: Bool { recorder?.isCaptureInterrupted ?? false }

    func motionCoverage(videoDuration: Double) -> Double? {
        if CollectionTestMode.isEnabled { return 1.0 }
        return recorder?.motionCoverage(videoDuration: videoDuration)
    }

    /// Starts a continuous guided recording named for the experiment.
    /// `completion` receives the saved folder after stop, or nil if capture
    /// could not start.
    func startGuided(experimentID: String, completion: @escaping (URL?) -> Void) {
        guidedModeActive = true
        guidedNamePrefix = "Guided-\(experimentID)"
        guidedStopCompletion = completion
        start(withLabel: nil)
    }

    func stopGuided() {
        stop()
    }

    private var guidedNamePrefix: String?
    private var guidedStopCompletion: ((URL?) -> Void)?

    private func finishGuidedStop(folder: URL?) {
        guidedModeActive = false
        guidedNamePrefix = nil
        let completion = guidedStopCompletion
        guidedStopCompletion = nil
        completion?(folder)
    }

    func start(withLabel label: Bool?) {
        if CollectionTestMode.isEnabled {
            startSimulatedRecording(withLabel: label)
            return
        }
        guard !isRecording, !isPreparingRecording else { return }
        guard recorder != nil else {
            alert = AppAlert("Not ready", "Recorder is not initialized yet.")
            return
        }

        isPreparingRecording = true
        statusText = "Preparing capture…"
        Task { @MainActor [weak self] in
            guard let self else { return }
            defer { self.isPreparingRecording = false }
            do {
                try await self.resolveReviewAudioPreference()
                guard let recorder = self.recorder else {
                    throw Err("Recorder is not initialized yet.")
                }
                try recorder.updateReviewAudioConfiguration(
                    enabled: self.captureReviewAudio,
                    permission: self.reviewAudioPermission
                )
                guard self.isMotionAvailable else {
                    throw Err("AirPods motion became unavailable before capture started.")
                }
                self.startPreparedRecording(withLabel: label, recorder: recorder)
            } catch {
                self.statusText = "Ready"
                self.alert = AppAlert("Could not start capture", error.localizedDescription)
                if self.guidedModeActive { self.finishGuidedStop(folder: nil) }
            }
        }
    }

    private func startPreparedRecording(
        withLabel label: Bool?,
        recorder r: VideoIMURecorder
    ) {

        currentEatingLabel = label

        let timestamp = makeTimestampString()
        // Determine base name prefix based on label semantics
        // "Eating" if true, "Not-eating" if false, "Session" if nil
        let basePrefix: String
        if let guided = guidedNamePrefix {
            basePrefix = guided
        } else if let lbl = label {
            basePrefix = lbl ? "Eating" : "Not-eating"
        } else {
            basePrefix = "Session"
        }
        let baseName = "\(basePrefix)-\(timestamp)-\(UUID().uuidString.prefix(8))"
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        let folder = docs.appendingPathComponent(baseName, isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)

        let videoName = "\(baseName).mov"
        let url = folder.appendingPathComponent(videoName)

        // Set recording label for events
        r.recordingLabel = label

        // Start recording
        r.startRecording(to: url)

        guard r.isRecording else {
            self.statusText = "Could not start capture"
            if guidedModeActive { finishGuidedStop(folder: nil) }
            return
        }
        self.isRecording = true
        self.statusText = "Recording…"
        self.lastURL = nil
        self.lastSessionFolder = folder
    }

    func start() {
        start(withLabel: nil)
    }

    func stop() {
        if CollectionTestMode.isEnabled {
            finishSimulatedRecording()
            return
        }
        guard isRecording, let r = recorder else { return }
        // Flip immediately so UI can't call stop again
        isRecording = false
        statusText = "Finishing…"
        r.stop { [weak self] in
            DispatchQueue.main.async {
                guard let self else { return }
                self.statusText = "Saved"
                let url = r.outputURL
                self.lastURL = url
                self.lastSessionFolder = url.deletingLastPathComponent()
                if let folder = self.lastSessionFolder {
                    let labelledValue = self.currentEatingLabel == false   // Not Eating -> labelled = true, Eating -> labelled = false
                    self.setMetadata(for: folder, labelled: labelledValue, shared: false)
                    self.currentEatingLabel = nil
                    // Force a directory refresh by briefly touching the folder; ShareLink reads a fresh listing.
                    _ = try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
                }
                if self.guidedModeActive { self.finishGuidedStop(folder: self.lastSessionFolder) }
            }
        }
    }
    
    /// Automatically invoked when IMU data becomes unavailable during an active recording.
    private func pauseDueToMotionLoss() {
        guard isRecording, let r = recorder else { return }

        r.markInterrupted()

        // Flip flags so the UI enters the paused state.
        isRecording = false
        isPaused = true
        canResumeRecording = false
        statusText = "Paused – lost motion data"

        r.stop { [weak self] in
            DispatchQueue.main.async {
                guard let self else { return }

                self.statusText = "Paused – saved partial segment"

                let url = r.outputURL
                self.lastURL = url
                self.lastSessionFolder = url.deletingLastPathComponent()

                // Preserve currentEatingLabel so we can reuse it on resume,
                // but still record metadata for this partial segment.
                if let folder = self.lastSessionFolder {
                    let labelledValue = self.currentEatingLabel == false   // Not Eating -> labelled = true, Eating -> labelled = false
                    self.setMetadata(for: folder, labelled: labelledValue, shared: false)
                    // Note: do NOT clear currentEatingLabel here.
                }
            }
        }
    }

    /// Called when the user decides they are done after a pause.
    func cancelPausedRecording() {
        isPaused = false
        canResumeRecording = false
        currentEatingLabel = nil
        statusText = "Cancelled"
        // We do not delete the partial recording; it remains as its own session.
    }

    /// Called when the user taps Resume after motion becomes available again.
    func resumeAfterMotionReturns() {
        guard isPaused, isMotionAvailable, canResumeRecording else { return }
        isPaused = false
        canResumeRecording = false
        statusText = "Resuming…"

        // Resume with the same "Eating"/"Not Eating" choice as before.
        start(withLabel: currentEatingLabel)
    }

    // MARK: - Deterministic UI-test capture

    private func startSimulatedRecording(withLabel label: Bool?) {
        guard !isRecording, isMotionAvailable else { return }
        currentEatingLabel = label
        isRecording = true
        isPaused = false
        statusText = "Recording…"
    }

    private func finishSimulatedRecording() {
        guard isRecording else { return }
        isRecording = false
        let isEating = currentEatingLabel ?? true
        let baseName = guidedNamePrefix.map { "\($0)-UI-Test" }
            ?? (isEating ? "Eating-UI-Test" : "Not-eating-UI-Test")
        guard let documents = FileManager.default.urls(
            for: .documentDirectory,
            in: .userDomainMask
        ).first else { return }
        let folder = documents.appendingPathComponent(baseName, isDirectory: true)
        try? FileManager.default.removeItem(at: folder)
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let videoURL = folder.appendingPathComponent("\(baseName).mov")
            let motionURL = folder.appendingPathComponent("\(baseName).csv")
            try Data("ui-test-video".utf8).write(to: videoURL)
            try Data(
                "timestamp,ax,ay,az,gx,gy,gz\n100.00,0,0,0,0,0,0\n100.02,0,0,0,0,0,0\n".utf8
            ).write(to: motionURL)
            try CanonicalBundleWriter.writeInitialBundle(
                videoURL: videoURL,
                isEating: guidedModeActive ? nil : isEating,
                timing: CaptureTimingSnapshot(
                    videoStartHostTime: 100.0,
                    firstVideoFrameHostTime: 100.0,
                    firstMotionHostTime: 100.0,
                    lastMotionHostTime: 100.02,
                    motionSampleCount: 2,
                    motionSampleGapCount: 0,
                    largestMotionSampleGap: nil,
                    interrupted: false,
                    interruptionCount: 0,
                    reviewAudioEnabled: false,
                    reviewAudioPermission: "not_requested",
                    reviewAudioPath: nil
                )
            )
            lastURL = videoURL
            lastSessionFolder = folder
            setMetadata(for: folder, labelled: guidedModeActive ? false : !isEating, shared: false)
            statusText = "Saved"
        } catch {
            alert = AppAlert("Test capture failed", error.localizedDescription)
            statusText = "Ready"
        }
        currentEatingLabel = nil
        if guidedModeActive { finishGuidedStop(folder: lastSessionFolder) }
    }


    // MARK: - Permissions
    private func requestPermissions() async throws {
        // Camera
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: break
        case .notDetermined:
            let ok = await AVCaptureDevice.requestAccess(for: .video)
            if !ok { throw Err("Camera access denied") }
        default:
            throw Err("Camera access denied")
        }
        try await resolveReviewAudioPreference()
        // Headphone motion prompts when starting the manager; no preflight API needed.
    }

    private func resolveReviewAudioPreference() async throws {
        if captureReviewAudio {
            let allowed = await withCheckedContinuation { continuation in
                AVAudioSession.sharedInstance().requestRecordPermission { granted in
                    continuation.resume(returning: granted)
                }
            }
            reviewAudioPermission = allowed ? "granted" : "denied"
            if !allowed {
                captureReviewAudio = false
            }
        } else {
            reviewAudioPermission = "not_requested"
        }
    }

    // MARK: - Sessions folder helpers

    func allSessionFolders() -> [URL] {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        let contents = (try? FileManager.default.contentsOfDirectory(at: docs, includingPropertiesForKeys: [.creationDateKey], options: .skipsHiddenFiles)) ?? []
        let sessionFolders = contents.filter { url in
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir),
                  isDir.boolValue else {
                return false
            }
            let name = url.lastPathComponent
            return !name.hasPrefix(".")
        }
        return sessionFolders.sorted { lhs, rhs in
            let lhsDate = (try? lhs.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? Date.distantPast
            let rhsDate = (try? rhs.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? Date.distantPast
            return lhsDate > rhsDate
        }
    }

    /// Deletes the given session folder and all its contents.
    func deleteSessionFolder(_ url: URL) {
        try? FileManager.default.removeItem(at: url)
        sessionMetadata.removeValue(forKey: url.lastPathComponent)
        persistMetadata()
    }

    @discardableResult
    func zipSessionFolder(_ folder: URL) throws -> URL {
        guard !isRecording, !isPreparingRecording else {
            throw Err("Stop and finish the recording before export.")
        }
        let manifestURL = folder.appendingPathComponent("session.json")
        let data = try Data(contentsOf: manifestURL)
        guard let manifest = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw Err("Session metadata is invalid.")
        }
        for key in ["video", "motion", "audio"] {
            guard let reference = manifest[key] as? [String: Any] else {
                if key != "audio" { throw Err("Missing \(key) metadata") }
                continue
            }
            if key == "audio", reference["enabled"] as? Bool != true { continue }
            guard let path = reference["path"] as? String else { throw Err("Missing \(key) reference") }
            let media = folder.appendingPathComponent(path).standardizedFileURL
            guard media.path.hasPrefix(folder.standardizedFileURL.path + "/"),
                  FileManager.default.fileExists(atPath: media.path) else { throw Err("Missing \(key) media") }
            if let expected = reference["sha256"] as? String {
                let actual = try CanonicalCaptureContract.sha256(of: media)
                guard expected == actual else { throw Err("Export blocked: \(key) hash mismatch") }
            }
        }
        let destination = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(folder.lastPathComponent)-\(UUID().uuidString).zip")
        var coordinationError: NSError?
        var copyError: Error?
        NSFileCoordinator().coordinate(readingItemAt: folder, options: .forUploading, error: &coordinationError) { archive in
            do { try FileManager.default.copyItem(at: archive, to: destination) }
            catch { copyError = error }
        }
        if let error = coordinationError ?? copyError as NSError? { throw error }
        guard FileManager.default.fileExists(atPath: destination.path) else { throw Err("Session archive was not created") }
        return destination
    }

    private func loadMetadata() {
        guard let data = UserDefaults.standard.data(forKey: metadataDefaultsKey) else { return }
        if let decoded = try? JSONDecoder().decode([String: RecordingMetadata].self, from: data) {
            sessionMetadata = decoded
        }
    }

    private func persistMetadata() {
        if let data = try? JSONEncoder().encode(sessionMetadata) {
            UserDefaults.standard.set(data, forKey: metadataDefaultsKey)
        }
    }

    private func setMetadata(for folder: URL, labelled: Bool, shared: Bool) {
        let key = folder.lastPathComponent
        sessionMetadata[key] = RecordingMetadata(labelled: labelled, shared: shared)
        persistMetadata()
    }

    func metadata(for folder: URL) -> RecordingMetadata? {
        let key = folder.lastPathComponent
        return sessionMetadata[key]
    }

    func markShared(for folder: URL) {
        let key = folder.lastPathComponent
        var meta = sessionMetadata[key] ?? RecordingMetadata(labelled: false, shared: false)
        meta.shared = true
        sessionMetadata[key] = meta
        persistMetadata()
    }

    func markShared(for folders: [URL]) {
        for folder in folders {
            markShared(for: folder)
        }
    }

    /// Updates the `labelled` flag for the given session folder while preserving the current `shared` value.
    func setLabelled(_ labelled: Bool, for folder: URL) {
        let key = folder.lastPathComponent
        var meta = sessionMetadata[key] ?? RecordingMetadata(labelled: false, shared: false)
        meta.labelled = labelled
        sessionMetadata[key] = meta
        persistMetadata()
    }

    deinit {
        if let audioRouteObserver { NotificationCenter.default.removeObserver(audioRouteObserver) }
        imuCheckTimer?.cancel()
        imuCheckTimer = nil
    }

}

// MARK: - Small alert type
struct AppAlert: Identifiable {
    let id = UUID()
    let title: String
    let message: String
    init(_ title: String, _ message: String) { self.title = title; self.message = message }
}
struct Err: LocalizedError { let message: String; init(_ m:String){message=m}; var errorDescription:String?{message} }
