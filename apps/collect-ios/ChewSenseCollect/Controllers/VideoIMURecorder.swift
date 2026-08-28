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


/// Records a .mov with a video track + a timed-metadata track containing
/// 10 ms packets of AirPods IMU samples, keyed to the host clock.
final class VideoIMURecorder: NSObject,
    AVCaptureVideoDataOutputSampleBufferDelegate,
    AVCaptureAudioDataOutputSampleBufferDelegate {
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
    
    @objc dynamic private(set) var isMotionAvailable: Bool = false
    private(set) var isRecording: Bool = false
    
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
        guard !isRecording else {
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
        // Reuse the already-configured capture session; just point the writer at a new URL
        self.outputURL = url
        do {
            // Prepare CSV sidecar file
            let csvURL = url.deletingPathExtension().appendingPathExtension("csv")
            self.csvURL = csvURL
            FileManager.default.createFile(atPath: csvURL.path, contents: nil, attributes: nil)
            csvFileHandle = try FileHandle(forWritingTo: csvURL)

            // (Re)create the asset writer for the new URL
            try setupWriter()

            // Start motion capture batching
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
    private let interruptionTracker = CaptureInterruptionTracker()
    private var captureNotificationTokens: [NSObjectProtocol] = []

    private var captureReviewAudio: Bool
    private var reviewAudioPermission: String
    private var isSessionConfigured = false

    private var csvFileHandle: FileHandle?
    private var csvURL: URL?

    private let videoWritingQueue = DispatchQueue(label: "video.writing.queue")
    
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
        // Start timer to poll isDeviceMotionAvailable every 0.5s
        let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .userInitiated))
        timer.schedule(deadline: .now(), repeating: .milliseconds(500))
        timer.setEventHandler { [weak self] in
            guard let self = self else { return }
            let available = self.hmm.isDeviceMotionAvailable
            if self.isMotionAvailable != available {
                DispatchQueue.main.async {
                    self.isMotionAvailable = available
                }
            }
        }
        timer.resume()
        motionAvailabilityTimer = timer
    }
    
    deinit {
        motionAvailabilityTimer?.cancel()
        motionAvailabilityTimer = nil
        captureNotificationTokens.forEach(NotificationCenter.default.removeObserver)
    }

    func start() {
        do {
            try setupSession()
            
            let csvURL = outputURL.deletingPathExtension().appendingPathExtension("csv")
            self.csvURL = csvURL
            FileManager.default.createFile(atPath: csvURL.path, contents: nil, attributes: nil)
            csvFileHandle = try FileHandle(forWritingTo: csvURL)
            
            try setupWriter()
            setupMotion()
            startBatchTimer()
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                self?.session.startRunning()
            }
            isRecording = true
        } catch { onError?(error) }
    }

    func stop(completion: @escaping () -> Void) {
        // Prevent double-stop
        guard isRecording else {
            completion()
            return
        }
        isRecording = false
        let interruptionSummary = interruptionTracker.snapshot()

        // Flush any remaining IMU events before stopping timers and motion updates.
        flushBatch()

        batchTimer?.cancel()
        hmm.stopDeviceMotionUpdates()
        // Do not stop the session here; keep it running for preview
        // session.stopRunning()
        
        guard let writer = writer else {
            csvFileHandle?.closeFile()
            csvFileHandle = nil
            self.writeCanonicalBundle(interruptions: interruptionSummary)
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
            self.csvFileHandle?.closeFile()
            self.csvFileHandle = nil
            self.writeCanonicalBundle(interruptions: interruptionSummary)
            completion()
        }
    }

    func markInterrupted() {
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
        if #available(iOS 17.0, *) {
            // Set video output rotation to 90 degrees for portrait orientation
            videoOutput.connection(with: .video)?.videoRotationAngle = 90
        } else {
            videoOutput.connection(with: .video)?.videoOrientation = .portrait
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
                guard let self, self.isRecording else { return }
                self.interruptionTracker.avCaptureWasInterrupted()
            },
            center.addObserver(
                forName: AVCaptureSession.interruptionEndedNotification,
                object: session,
                queue: nil
            ) { [weak self] _ in
                self?.interruptionTracker.avCaptureInterruptionEnded()
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
        try? FileManager.default.removeItem(at: outputURL)
        writer = try AVAssetWriter(outputURL: outputURL, fileType: .mov)

        // Video
        let settings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: 1080,
            AVVideoHeightKey: 1920
        ]
        videoInput = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        videoInput.expectsMediaDataInRealTime = true
        // Video orientation is now matched by the preview layer for iOS 17+ and no input transform is necessary
        
        guard writer.canAdd(videoInput) else { throw Err("Cannot add video input") }
        writer.add(videoInput)

        audioInput = nil
        audioWriter = nil
        reviewAudioURL = nil
        if captureReviewAudio {
            let audioURL = outputURL.deletingPathExtension().appendingPathExtension("m4a")
            try? FileManager.default.removeItem(at: audioURL)
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

        writer.startWriting()
        audioWriter?.startWriting()
        let origin = CMClockGetTime(hostClock)
        videoStartHostTimeSeconds = CMTimeGetSeconds(origin)
        firstVideoFrameHostTimeSeconds = nil
        firstMotionHostTimeSeconds = nil
        lastMotionHostTimeSeconds = nil
        motionSampleCount = 0
        previousMotionHostTimeSeconds = nil
        motionSampleGapCount = 0
        largestMotionSampleGapSeconds = nil
        interruptionTracker.reset()
        writer.startSession(atSourceTime: origin)
        audioWriter?.startSession(atSourceTime: origin)
    }

    private func setupMotion() {
        guard hmm.isDeviceMotionAvailable else {
            onError?(Err("Headphone motion not available. Connect AirPods and try again."))
            return
        }
        // CMHeadphoneMotionManager does not expose deviceMotionUpdateInterval; sample rate is managed by the system (~100 Hz).
        motionQueue.qualityOfService = .userInteractive
        hmm.startDeviceMotionUpdates(to: motionQueue) { [weak self] m, _ in
            guard let s = self, let m = m else { return }
            s.ingest(m)
            s.deviceMotionHandler?(m)
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
        if output === audioOutput {
            guard let audioWriter, audioWriter.status == .writing,
                  let audioInput, audioInput.isReadyForMoreMediaData else { return }
            audioInput.append(sb)
            return
        }
        guard let writer = writer,
              let videoInput = videoInput,
              writer.status == .writing else { return }
        videoWritingQueue.async {
            if self.firstVideoFrameHostTimeSeconds == nil {
                let presentationTime = CMSampleBufferGetPresentationTimeStamp(sb)
                let seconds = CMTimeGetSeconds(presentationTime)
                if seconds.isFinite {
                    self.firstVideoFrameHostTimeSeconds = seconds
                }
            }
            if videoInput.isReadyForMoreMediaData {
                videoInput.append(sb)
            }
        }
    }

    /// Attempts to start device motion and calls the completion with true if a real sample is received within 1.5s.
    func trialDeviceMotionCheck(completion: @escaping (Bool) -> Void) {
        // Prevent motion check while recording
        if isRecording {
            completion(false)
            return
        }
        
        guard hmm.isDeviceMotionAvailable else {
            completion(false)
            return
        }
        var didReceiveMotion = false
        let checkQueue = OperationQueue()
        checkQueue.qualityOfService = .userInitiated
        hmm.startDeviceMotionUpdates(to: checkQueue) { m, _ in
            if m != nil && !didReceiveMotion {
                didReceiveMotion = true
                self.hmm.stopDeviceMotionUpdates()
                completion(true)
                self.deviceMotionHandler?(m)
            }
        }
        // Timeout after 1.5 seconds
        DispatchQueue.global().asyncAfter(deadline: .now() + 1.5) {
            if !didReceiveMotion {
                self.hmm.stopDeviceMotionUpdates()
                completion(false)
                self.deviceMotionHandler?(nil)
            }
        }
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
