//
//  VideoIMURecorder.swift
//  ChewSenseCollect
//
//  Created by Zachary Sturman on 11/13/25.
//

import Foundation
import AVFoundation
import AudioToolbox
import CoreMedia
import CoreMotion


/// Records original video plus a host-clock motion CSV and optional review audio.
final class VideoIMURecorder: NSObject,
    AVCaptureVideoDataOutputSampleBufferDelegate,
    AVCaptureAudioDataOutputSampleBufferDelegate,
    CMHeadphoneMotionManagerDelegate {
    // Public
    private(set) var outputURL: URL
    var onError: ((Error) -> Void)?
    /// Called with each new device motion sample (or nil if unavailable)
    var deviceMotionHandler: ((CMDeviceMotion?) -> Void)?
    var exposedSession: AVCaptureSession { session }
    /// Initial activity selection for the current recording (true = Eating, false = Not Eating).
    /// Used only to materialize a separate coarse-label artifact after capture;
    /// motion events are always saved without truth labels.
    var recordingLabel: Bool? = nil
    /// Called on the capture queue for every video frame, including preview
    /// frames. Must return quickly; guided quality analysis throttles itself.
    var videoFrameObserver: ((CMSampleBuffer) -> Void)?
    
    @objc dynamic private(set) var isMotionAvailable: Bool = false
    private var recordingActive = false
    private(set) var isRecording: Bool {
        get { captureLock.withLock { recordingActive } }
        set { captureLock.withLock { recordingActive = newValue } }
    }
    
    // Call this to build the AVCaptureSession without starting the writer yet.
    func configureSessionOnly() throws {
        try setupSession()
    }

    /// Applies the current user choice before every capture. In particular,
    /// disabling review audio removes the microphone input rather than merely
    /// omitting the sidecar writer.
    func updateReviewAudioConfiguration(
        enabled: Bool,
        permission: String
    ) throws {
        guard !isRecording, !isFinishing else {
            throw Err("Review-audio settings cannot change during a recording.")
        }
        reviewAudioPermission = permission
        guard isSessionConfigured else {
            captureReviewAudio = enabled
            return
        }
        guard captureReviewAudio != enabled else { return }

        session.beginConfiguration()
        defer { session.commitConfiguration() }
        try configureReviewAudioComponents(enabled: enabled)
        captureReviewAudio = enabled
    }

    // Call this to run the preview without writing to disk.
    func startPreview() {
        if !session.isRunning {
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                self?.session.startRunning()
            }
        }
    }

    func stopPreview() {
        if session.isRunning {
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                self?.session.stopRunning()
            }
        }
    }

    func startRecording(to url: URL) {
        captureLock.lock()
        defer { captureLock.unlock() }
        guard !isRecording, !isFinishing else {
            onError?(Err("The previous recording is still finishing."))
            return
        }
        guard hmm.isDeviceMotionAvailable else {
            onError?(Err("Connect AirPods before recording."))
            return
        }
        guard !FileManager.default.fileExists(atPath: url.path),
              !FileManager.default.fileExists(atPath: url.deletingPathExtension().appendingPathExtension("csv").path) else {
            onError?(Err("A recording already exists at this path; choose a new session."))
            return
        }
        // Reuse the already-configured capture session; just point the writer at a new URL
        self.outputURL = url
        do {
            // Prepare CSV sidecar file
            let csvURL = url.deletingPathExtension().appendingPathExtension("csv")
            self.csvURL = csvURL
            FileManager.default.createFile(atPath: csvURL.path, contents: Data("timestamp,dtNs,ax,ay,az,gx,gy,gz\n".utf8), attributes: nil)
            csvFileHandle = try FileHandle(forWritingTo: csvURL)

            // (Re)create the asset writer for the new URL
            try setupWriter()

            // Producers may run immediately after registration.
            isRecording = true
            setupMotion()
            startBatchTimer()

            // Make sure the session is running for both preview and capture
            if !session.isRunning {
                DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                    self?.session.startRunning()
                }
            }
            isRecording = true
        } catch {
            isRecording = false
            writer?.cancelWriting()
            audioWriter?.cancelWriting()
            csvFileHandle?.closeFile()
            csvFileHandle = nil
            onError?(error)
        }
    }

    func startRecording(to url: URL, isEating: Bool) {
        // Store the initial selection for post-processing; events are saved without labels during recording.
        self.recordingLabel = isEating
        startRecording(to: url)
    }

    // Private
    private let session = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private var rotationCoordinator: AVCaptureDevice.RotationCoordinator?
    private var rotationObservation: NSKeyValueObservation?
    private var recordingRotationAngle: CGFloat = 90
    private let audioOutput = AVCaptureAudioDataOutput()
    private var writer: AVAssetWriter!
    private var videoInput: AVAssetWriterInput!
    private var audioWriter: AVAssetWriter?
    private var audioInput: AVAssetWriterInput?
    private var reviewAudioURL: URL?
    private var microphoneInput: AVCaptureDeviceInput?
    private let hmm = CMHeadphoneMotionManager()
    private let motionQueue = OperationQueue()
    private let audioCaptureQueue = DispatchQueue(label: "audio.queue")

    // Batching
    private let batchWindowNs: UInt32 = 10_000_000 // 10 ms
    private var batchStartSeconds: Double?
    private var events: [MotionEvent] = []
    private var batchTimer: DispatchSourceTimer?
#if DEBUG
    // Print IMU samples to the Xcode console while recording. Set to 1 to print every sample,
    // or increase (e.g., 10) to throttle.
    private let debugPrintIMUSamplesEvery = 1
    private var debugIMUSampleCounter = 0
#endif

    // Time
    private let hostClock = CMClockGetHostTimeClock()
    private let hostTimescale: Int32 = 1_000_000_000 // ns
    private var videoStartHostTimeSeconds: Double?
    private var firstVideoFrameHostTimeSeconds: Double?
    private var firstMotionHostTimeSeconds: Double?
    private var lastMotionHostTimeSeconds: Double?
    private var motionSampleCount: Int = 0
    private var previousMotionHostTimeSeconds: Double?
    private var motionSampleGapCount: Int = 0
    private var largestMotionSampleGapSeconds: Double?
    private var motionGapSecondsTotal: Double = 0
    private var captureInterruptionActive = false
    private let interruptionTracker = CaptureInterruptionTracker()
    private var captureNotificationTokens: [NSObjectProtocol] = []

    private var captureReviewAudio: Bool
    private var reviewAudioPermission: String
    private var isSessionConfigured = false

    private var csvFileHandle: FileHandle?
    private var csvURL: URL?

    // All writer, CSV, motion buffer and lifecycle access shares this lock.
    // Append synchronously under it: stop cannot finish a writer ahead of queued appends.
    private let captureLock = NSRecursiveLock()
    private var isFinishing = false
    private var motionStreamID = UUID()
    private var motionHealth = HeadphoneMotionHealth()
    
    private var motionAvailabilityTimer: DispatchSourceTimer?

    init(
        outputURL: URL,
        captureReviewAudio: Bool = false,
        reviewAudioPermission: String = "not_requested"
    ) {
        self.outputURL = outputURL
        self.captureReviewAudio = captureReviewAudio
        self.reviewAudioPermission = reviewAudioPermission
        super.init()
        installCaptureInterruptionObservers()
        hmm.delegate = self
        hmm.startConnectionStatusUpdates()
        // Capability alone is not connection/readiness. Watch real samples
        // and recover a stale subscription even if availability never toggles.
        let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .userInitiated))
        timer.schedule(deadline: .now(), repeating: .milliseconds(500))
        timer.setEventHandler { [weak self] in
            guard let self = self else { return }
            self.refreshMotionConnection()
        }
        timer.resume()
        motionAvailabilityTimer = timer
    }
    
    deinit {
        motionAvailabilityTimer?.cancel()
        motionAvailabilityTimer = nil
        hmm.stopDeviceMotionUpdates()
        hmm.stopConnectionStatusUpdates()
        captureNotificationTokens.forEach(NotificationCenter.default.removeObserver)
    }

    func start() {
        startRecording(to: outputURL)
    }

    func stop(completion: @escaping () -> Void) {
        captureLock.lock()
        defer { captureLock.unlock() }
        // Prevent double-stop
        guard isRecording else {
            completion()
            return
        }
        isRecording = false
        isFinishing = true
        let interruptionSummary = interruptionTracker.snapshot()

        batchTimer?.cancel()
        batchTimer = nil
        // Keep the preview/readiness stream alive; ingest stops with isRecording.
        // Callbacks waiting for this lock now see isRecording=false.
        flushBatch()
        // Do not stop the session here; keep it running for preview
        // session.stopRunning()
        
        guard let writer = writer else {
            csvFileHandle?.closeFile()
            csvFileHandle = nil
            self.writeCanonicalBundle(interruptions: interruptionSummary)
            self.isFinishing = false
            completion()
            return
        }
        
        let group = DispatchGroup()
        if writer.status == .writing {
            videoInput?.markAsFinished()
            group.enter()
            writer.finishWriting { group.leave() }
        } else {
            writer.cancelWriting()
        }
        if let audioWriter {
            if audioWriter.status == .writing {
                audioInput?.markAsFinished()
                group.enter()
                audioWriter.finishWriting { group.leave() }
            } else {
                audioWriter.cancelWriting()
            }
        }
        group.notify(queue: .global(qos: .utility)) {
            self.captureLock.lock()
            defer { self.captureLock.unlock() }
            if writer.status == .failed {
                self.onError?(writer.error ?? Err("Video writer failed."))
            }
            if let audioWriter = self.audioWriter, audioWriter.status == .failed {
                self.onError?(audioWriter.error ?? Err("Review audio writer failed."))
            }
            self.csvFileHandle?.closeFile()
            self.csvFileHandle = nil
            self.writeCanonicalBundle(interruptions: interruptionSummary)
            self.isFinishing = false
            completion()
        }
    }

    /// Host-clock seconds now, on the same clock as motion and the writer origin.
    nonisolated static func hostTimeNow() -> Double {
        CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock()))
    }

    /// Latest recorded headphone sample time (host clock), if recording.
    var latestMotionHostTime: Double? { captureLock.withLock { lastMotionHostTimeSeconds } }

    var isCaptureInterrupted: Bool { captureLock.withLock { captureInterruptionActive } }

    /// Fraction of the video timeline covered by motion samples with no gap
    /// above the contract threshold. Uses the recorder's own sample accounting.
    func motionCoverage(videoDuration: Double) -> Double? {
        captureLock.withLock {
            guard videoDuration > 0, let first = firstMotionHostTimeSeconds,
                  let last = lastMotionHostTimeSeconds, let origin = videoStartHostTimeSeconds else { return nil }
            let start = max(first, origin)
            let end = min(last, origin + videoDuration)
            guard end > start else { return 0 }
            return max(0, min(1, (end - start - motionGapSecondsTotal) / videoDuration))
        }
    }

    /// Guided recordings keep video running when AirPods drop out. Headphone
    /// updates are re-registered on reconnection; the shared host clock keeps
    /// the timeline continuous and the gap is recorded, never filled.
    func restartMotionUpdatesIfNeeded() {
        captureLock.withLock {
            guard !isFinishing, hmm.isDeviceMotionAvailable else { return }
            let now = Self.hostTimeNow()
            if motionHealth.shouldRestart(at: now) { setupMotion() }
        }
    }

    func reconnectMotion() {
        captureLock.withLock {
            guard !isFinishing, hmm.isDeviceMotionAvailable else { return }
            setupMotion()
        }
    }

    func refreshMotionConnection() {
        captureLock.withLock {
            let now = Self.hostTimeNow()
            publishMotionReadiness(motionHealth.isFresh(at: now))
            guard !isFinishing, hmm.isDeviceMotionAvailable else { return }
            if motionHealth.shouldRestart(at: now) { setupMotion() }
        }
    }

    private func publishMotionReadiness(_ ready: Bool) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.isMotionAvailable != ready else { return }
            self.isMotionAvailable = ready
            if !ready { self.deviceMotionHandler?(nil) }
        }
    }

    func headphoneMotionManagerDidConnect(_ manager: CMHeadphoneMotionManager) {
        reconnectMotion()
    }

    func headphoneMotionManagerDidDisconnect(_ manager: CMHeadphoneMotionManager) {
        captureLock.withLock {
            motionHealth.invalidateSamples()
            publishMotionReadiness(false)
        }
    }

    func markInterrupted() {
        captureLock.lock()
        defer { captureLock.unlock() }
        interruptionTracker.recordMotionInterruption()
    }

    // MARK: Setup
    private func setupSession() throws {
        guard !isSessionConfigured else { return }
        session.beginConfiguration()
        session.sessionPreset = .high
        // Removed: session.masterClock = hostClock

        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front)
        else {
            session.commitConfiguration()
            throw Err("No camera")
        }
        let input = try AVCaptureDeviceInput(device: device)
        guard session.canAddInput(input) else {
            session.commitConfiguration()
            throw Err("Cannot add camera input")
        }
        session.addInput(input)

        let q = DispatchQueue(label: "video.queue")
        videoOutput.setSampleBufferDelegate(self, queue: q)
        videoOutput.alwaysDiscardsLateVideoFrames = false
        guard session.canAddOutput(videoOutput) else {
            session.commitConfiguration()
            throw Err("Cannot add video output")
        }
        session.addOutput(videoOutput)

        do {
            try configureReviewAudioComponents(enabled: captureReviewAudio)
        } catch {
            session.commitConfiguration()
            throw error
        }
        let coordinator = AVCaptureDevice.RotationCoordinator(device: device, previewLayer: nil)
        rotationCoordinator = coordinator
        rotationObservation = coordinator.observe(\.videoRotationAngleForHorizonLevelCapture, options: [.initial, .new]) { [weak self] _, _ in
            guard let self else { return }
            self.captureLock.withLock {
                // Keep the pixel dimensions fixed for the entire recording.
                guard !self.isRecording, !self.isFinishing else { return }
                self.applyCaptureRotation()
            }
        }
        session.commitConfiguration()
        isSessionConfigured = true
    }

    /// Must be called inside an AVCaptureSession configuration transaction.
    private func configureReviewAudioComponents(enabled: Bool) throws {
        session.usesApplicationAudioSession = enabled
        session.automaticallyConfiguresApplicationAudioSession = enabled

        if !enabled {
            audioOutput.setSampleBufferDelegate(nil, queue: nil)
            if session.outputs.contains(where: { $0 === audioOutput }) {
                session.removeOutput(audioOutput)
            }
            if let microphoneInput,
               session.inputs.contains(where: { $0 === microphoneInput }) {
                session.removeInput(microphoneInput)
            }
            microphoneInput = nil
            return
        }

        var addedMicrophoneInput: AVCaptureDeviceInput?
        if microphoneInput == nil {
            guard let microphone = AVCaptureDevice.default(
                .builtInMicrophone,
                for: .audio,
                position: .unspecified
            ) else {
                throw Err("Review audio was enabled, but no microphone is available.")
            }
            let input = try AVCaptureDeviceInput(device: microphone)
            guard session.canAddInput(input) else {
                throw Err("Cannot add microphone input for review audio.")
            }
            session.addInput(input)
            microphoneInput = input
            addedMicrophoneInput = input
        }

        if !session.outputs.contains(where: { $0 === audioOutput }) {
            guard session.canAddOutput(audioOutput) else {
                if let addedMicrophoneInput {
                    session.removeInput(addedMicrophoneInput)
                    microphoneInput = nil
                }
                throw Err("Cannot add review audio output.")
            }
            session.addOutput(audioOutput)
        }
        audioOutput.setSampleBufferDelegate(self, queue: audioCaptureQueue)
    }

    private func installCaptureInterruptionObservers() {
        let center = NotificationCenter.default
        captureNotificationTokens = [
            center.addObserver(
                forName: AVCaptureSession.wasInterruptedNotification,
                object: session,
                queue: nil
            ) { [weak self] _ in
                guard let self else { return }
                self.captureLock.withLock { self.captureInterruptionActive = true }
                guard self.isRecording else { return }
                self.interruptionTracker.avCaptureWasInterrupted()
            },
            center.addObserver(
                forName: AVCaptureSession.interruptionEndedNotification,
                object: session,
                queue: nil
            ) { [weak self] _ in
                guard let self else { return }
                self.captureLock.withLock { self.captureInterruptionActive = false }
                self.interruptionTracker.avCaptureInterruptionEnded()
            },
            center.addObserver(
                forName: AVCaptureSession.runtimeErrorNotification,
                object: session,
                queue: nil
            ) { [weak self] _ in
                guard let self, self.isRecording else { return }
                self.interruptionTracker.avCaptureRuntimeError()
            }
        ]
    }

    private func setupWriter() throws {
        applyCaptureRotation()
        writer = try AVAssetWriter(outputURL: outputURL, fileType: .mov)

        // Video
        let settings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: Self.captureDimensions(rotationAngle: recordingRotationAngle).width,
            AVVideoHeightKey: Self.captureDimensions(rotationAngle: recordingRotationAngle).height
        ]
        videoInput = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        videoInput.expectsMediaDataInRealTime = true
        // Rotation is applied to the pixels before they reach the writer.
        
        guard writer.canAdd(videoInput) else { throw Err("Cannot add video input") }
        writer.add(videoInput)

        audioInput = nil
        audioWriter = nil
        reviewAudioURL = nil
        if captureReviewAudio {
            let audioURL = outputURL.deletingPathExtension().appendingPathExtension("m4a")
            let sidecarWriter = try AVAssetWriter(outputURL: audioURL, fileType: .m4a)
            let audioSettings: [String: Any] = [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 44_100,
                AVNumberOfChannelsKey: 1,
                AVEncoderBitRateKey: 64_000
            ]
            let input = AVAssetWriterInput(mediaType: .audio, outputSettings: audioSettings)
            input.expectsMediaDataInRealTime = true
            guard sidecarWriter.canAdd(input) else {
                throw Err("Cannot add review audio writer input")
            }
            sidecarWriter.add(input)
            audioWriter = sidecarWriter
            audioInput = input
            reviewAudioURL = audioURL
        }

        guard writer.startWriting() else { throw writer.error ?? Err("Cannot start video writer") }
        if let audioWriter, !audioWriter.startWriting() {
            throw audioWriter.error ?? Err("Cannot start audio writer")
        }
        events.removeAll(keepingCapacity: true)
        batchStartSeconds = nil
        let origin = CMClockGetTime(hostClock)
        videoStartHostTimeSeconds = CMTimeGetSeconds(origin)
        firstVideoFrameHostTimeSeconds = nil
        firstMotionHostTimeSeconds = nil
        lastMotionHostTimeSeconds = nil
        motionSampleCount = 0
        previousMotionHostTimeSeconds = nil
        motionSampleGapCount = 0
        largestMotionSampleGapSeconds = nil
        motionGapSecondsTotal = 0
        interruptionTracker.reset()
        writer.startSession(atSourceTime: origin)
        audioWriter?.startSession(atSourceTime: origin)
    }

    private func applyCaptureRotation() {
        guard let coordinator = rotationCoordinator,
              let connection = videoOutput.connection(with: .video) else { return }
        let angle = (coordinator.videoRotationAngleForHorizonLevelCapture / 90).rounded() * 90
        guard connection.isVideoRotationAngleSupported(angle) else { return }
        connection.videoRotationAngle = angle
        recordingRotationAngle = angle
    }

    static func captureDimensions(rotationAngle: CGFloat) -> (width: Int, height: Int) {
        let quarterTurns = Int((rotationAngle / 90).rounded())
        return quarterTurns.isMultiple(of: 2) ? (1920, 1080) : (1080, 1920)
    }

    private func setupMotion() {
        guard hmm.isDeviceMotionAvailable else { return }
        motionStreamID = UUID()
        let streamID = motionStreamID
        motionHealth.didRestart(at: Self.hostTimeNow())
        hmm.stopDeviceMotionUpdates()
        // The system chooses the headphone motion rate; measure it from recorded timestamps.
        motionQueue.maxConcurrentOperationCount = 1
        motionQueue.qualityOfService = .userInteractive
        hmm.startDeviceMotionUpdates(to: motionQueue) { [weak self] m, error in
            guard let s = self else { return }
            s.captureLock.withLock {
                guard s.motionStreamID == streamID else { return }
                guard error == nil, let m else {
                    s.motionHealth.invalidateSamples()
                    s.publishMotionReadiness(false)
                    return
                }
                let now = Self.hostTimeNow()
                guard s.motionHealth.didReceive(sampleTime: m.timestamp, at: now) else { return }
                s.publishMotionReadiness(true)
                if s.isRecording, m.timestamp >= (s.videoStartHostTimeSeconds ?? .infinity) { s.ingest(m) }
                s.deviceMotionHandler?(m)
            }
        }
#if DEBUG
        print("IMU: started deviceMotion updates")
#endif
    }

    private func startBatchTimer() {
        let t = DispatchSource.makeTimerSource(queue: .global(qos: .userInitiated))
        t.schedule(deadline: .now() + .milliseconds(10), repeating: .milliseconds(10))
        t.setEventHandler { [weak self] in self?.flushBatch() }
        t.resume()
        batchTimer = t
    }

    // MARK: IMU ingest & flush
    private func ingest(_ m: CMDeviceMotion) {
        if firstMotionHostTimeSeconds == nil {
            firstMotionHostTimeSeconds = m.timestamp
        }
        lastMotionHostTimeSeconds = m.timestamp
        motionSampleCount += 1
        if let previous = previousMotionHostTimeSeconds {
            let gap = m.timestamp - previous
            if gap > CanonicalCaptureContract.motionGapThresholdSeconds {
                motionSampleGapCount += 1
                motionGapSecondsTotal += gap
                largestMotionSampleGapSeconds = max(largestMotionSampleGapSeconds ?? 0, gap)
            }
        }
        previousMotionHostTimeSeconds = m.timestamp
        if batchStartSeconds == nil { batchStartSeconds = m.timestamp }
        let dt = m.timestamp - (batchStartSeconds ?? m.timestamp)
        let dtNs = UInt32(max(0, min(Double(UInt32.max)/1e9, dt)) * 1e9)
        events.append(MotionEvent(dtNs: dtNs,
                                  ax: Float(m.userAcceleration.x),
                                  ay: Float(m.userAcceleration.y),
                                  az: Float(m.userAcceleration.z),
                                  gx: Float(m.rotationRate.x),
                                  gy: Float(m.rotationRate.y),
                                  gz: Float(m.rotationRate.z),
                                  label: nil))
#if DEBUG
        debugIMUSampleCounter += 1
        if debugIMUSampleCounter % debugPrintIMUSamplesEvery == 0 {
            let ax = m.userAcceleration.x, ay = m.userAcceleration.y, az = m.userAcceleration.z
            let gx = m.rotationRate.x,    gy = m.rotationRate.y,    gz = m.rotationRate.z
            let t  = m.timestamp
            let dts = dt
            print(String(format: "[IMU] t=%.6f dt=%.6f ax=%.4f ay=%.4f az=%.4f gx=%.4f gy=%.4f gz=%.4f", t, dts, ax, ay, az, gx, gy, gz))
        }
#endif
    }

    private func flushBatch() {
        captureLock.lock()
        defer { captureLock.unlock() }
        guard !events.isEmpty, let t0 = batchStartSeconds else { return }
        guard let csvFileHandle = csvFileHandle else { return }
        
        // Write CSV header if file is empty
        if let fileSize = try? csvFileHandle.seekToEnd(), fileSize == 0 {
            if let headerData = "timestamp,dtNs,ax,ay,az,gx,gy,gz\n".data(using: .utf8) {
                csvFileHandle.write(headerData)
            }
        }
        
        var csvLines = ""
        for event in events {
            // Calculate absolute timestamp in seconds
            let eventTimestampSeconds = t0 + Double(event.dtNs) / 1e9
            // Format timestamp as ISO8601 with fractional seconds or as seconds since reference
            // Here using seconds with 9 decimal places for simplicity
            let timestampString = String(format: "%.9f", eventTimestampSeconds)
            let line = "\(timestampString),\(event.dtNs),\(event.ax),\(event.ay),\(event.az),\(event.gx),\(event.gy),\(event.gz)\n"
            csvLines.append(line)
        }
        
#if DEBUG
        print("IMU batch written:", events.count, "samples")
#endif

        if let data = csvLines.data(using: .utf8) {
            csvFileHandle.write(data)
        }

        // Flush bytes to disk so external share targets don't read a partial packet
        if #available(iOS 13.0, *) {
            try? csvFileHandle.synchronize()
        } else {
            csvFileHandle.synchronizeFile()
        }

        events.removeAll(keepingCapacity: true)
        // Reset for the next batch window; next ingest will set a new t0
        batchStartSeconds = nil
    }

    // MARK: Video delegate
    func captureOutput(_ output: AVCaptureOutput, didOutput sb: CMSampleBuffer, from connection: AVCaptureConnection) {
        if output === videoOutput { videoFrameObserver?(sb) }
        captureLock.lock()
        defer { captureLock.unlock() }
        guard isRecording else { return }
        guard let hostBuffer = Self.sampleBufferOnHostClock(sb, sourceClock: session.masterClock, hostClock: hostClock) else {
            onError?(Err("Cannot map capture presentation time to the host clock"))
            return
        }
        let seconds = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(hostBuffer))
        guard seconds.isFinite, seconds >= (videoStartHostTimeSeconds ?? .infinity) else { return }
        if output === audioOutput {
            guard let audioWriter, audioWriter.status == .writing,
                  let audioInput, audioInput.isReadyForMoreMediaData else { return }
            if !audioInput.append(hostBuffer) { onError?(audioWriter.error ?? Err("Audio append failed")) }
            return
        }
        guard let writer, writer.status == .writing,
              let videoInput, videoInput.isReadyForMoreMediaData else { return }
        if videoInput.append(hostBuffer) {
            if firstVideoFrameHostTimeSeconds == nil { firstVideoFrameHostTimeSeconds = seconds }
        } else {
            onError?(writer.error ?? Err("Video append failed"))
        }
    }

    /// Explicitly convert capture-clock PTS/DTS to the same host clock as
    /// CMDeviceMotion and writer.startSession. Physical fidelity still needs markers.
    static func sampleBufferOnHostClock(_ buffer: CMSampleBuffer, sourceClock: CMClock?, hostClock: CMClock) -> CMSampleBuffer? {
        guard let sourceClock else { return nil }
        let pts = CMSampleBufferGetPresentationTimeStamp(buffer)
        let mapped = CMSyncConvertTime(pts, from: sourceClock, to: hostClock)
        guard mapped.isValid, mapped.isNumeric else { return nil }
        if abs(CMTimeGetSeconds(CMTimeSubtract(mapped, pts))) <= 1e-9 { return buffer }
        var count = 0
        guard CMSampleBufferGetSampleTimingInfoArray(buffer, entryCount: 0, arrayToFill: nil, entriesNeededOut: &count) == noErr else { return nil }
        var timings = Array(repeating: CMSampleTimingInfo(), count: count)
        guard CMSampleBufferGetSampleTimingInfoArray(buffer, entryCount: count, arrayToFill: &timings, entriesNeededOut: nil) == noErr else { return nil }
        for index in timings.indices {
            timings[index].presentationTimeStamp = CMSyncConvertTime(timings[index].presentationTimeStamp, from: sourceClock, to: hostClock)
            if timings[index].decodeTimeStamp.isValid {
                timings[index].decodeTimeStamp = CMSyncConvertTime(timings[index].decodeTimeStamp, from: sourceClock, to: hostClock)
            }
        }
        var copy: CMSampleBuffer?
        guard CMSampleBufferCreateCopyWithNewTiming(allocator: kCFAllocatorDefault, sampleBuffer: buffer,
            sampleTimingEntryCount: count, sampleTimingArray: &timings, sampleBufferOut: &copy) == noErr else { return nil }
        return copy
    }

    /// Checks the sustained stream without interrupting an active recording.
    func checkMotionReadiness(completion: @escaping (Bool) -> Void) {
        refreshMotionConnection()
        completion(captureLock.withLock { motionHealth.isFresh(at: Self.hostTimeNow()) })
    }

    private func writeCanonicalBundle(interruptions: CaptureInterruptionSnapshot) {
        do {
            try CanonicalBundleWriter.writeInitialBundle(
                videoURL: outputURL,
                isEating: recordingLabel,
                timing: CaptureTimingSnapshot(
                    videoStartHostTime: videoStartHostTimeSeconds,
                    firstVideoFrameHostTime: firstVideoFrameHostTimeSeconds,
                    firstMotionHostTime: firstMotionHostTimeSeconds,
                    lastMotionHostTime: lastMotionHostTimeSeconds,
                    motionSampleCount: motionSampleCount,
                    motionSampleGapCount: motionSampleGapCount,
                    largestMotionSampleGap: largestMotionSampleGapSeconds,
                    interrupted: interruptions.interrupted,
                    interruptionCount: interruptions.count,
                    reviewAudioEnabled: reviewAudioURL.map {
                        FileManager.default.fileExists(atPath: $0.path)
                    } ?? false,
                    reviewAudioPermission: reviewAudioPermission,
                    reviewAudioPath: reviewAudioURL.flatMap {
                        FileManager.default.fileExists(atPath: $0.path)
                            ? $0.lastPathComponent
                            : nil
                    }
                )
            )
        } catch {
            onError?(error)
        }
    }
}

/// Distinguishes support for motion from actual recent sample delivery. A
/// capability flag can stay true throughout a disconnect/reconnect.
nonisolated struct HeadphoneMotionHealth {
    private(set) var lastSampleTime: Double?
    private(set) var lastArrivalTime: Double?
    private(set) var lastRestartTime: Double?
    static let freshSeconds = 0.5
    static let retrySeconds = 2.5

    func isFresh(at now: Double) -> Bool {
        guard let sample = lastSampleTime, let arrival = lastArrivalTime, now.isFinite else { return false }
        return now - sample >= -0.05 && now - sample <= Self.freshSeconds
            && now - arrival >= 0 && now - arrival <= Self.freshSeconds
    }

    func shouldRestart(at now: Double) -> Bool {
        !isFresh(at: now) && (lastRestartTime.map { now - $0 >= Self.retrySeconds } ?? true)
    }

    mutating func didRestart(at now: Double) {
        lastRestartTime = now
        invalidateSamples()
    }

    @discardableResult
    mutating func didReceive(sampleTime: Double, at now: Double) -> Bool {
        guard sampleTime.isFinite, now.isFinite, now - sampleTime >= -0.05,
              now - sampleTime <= Self.freshSeconds,
              lastSampleTime.map({ sampleTime > $0 }) ?? true else { return false }
        lastSampleTime = sampleTime
        lastArrivalTime = now
        return true
    }

    mutating func invalidateSamples() {
        lastSampleTime = nil
        lastArrivalTime = nil
    }
}
