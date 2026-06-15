//
//  DebugVideoRecorder.swift
//  ChewSenseDebuggingModel
//
//  Records a video-only .mov for field-test review.
//

import Foundation
import AVFoundation

final class DebugVideoRecorder: NSObject, AVCaptureFileOutputRecordingDelegate {
    private let session = AVCaptureSession()
    private let movieOutput = AVCaptureMovieFileOutput()
    private let sessionQueue = DispatchQueue(label: "com.chewsense.debug-video.session", qos: .userInitiated)

    private var isConfigured = false
    /// Called once recording actually starts (or fails to start before the
    /// first frame). Nil after it fires.
    private var startCompletion: ((Error?) -> Void)?
    private var stopCompletion: ((Result<URL, Error>) -> Void)?

    private(set) var outputURL: URL?

    var exposedSession: AVCaptureSession { session }

    func configureSessionOnly() throws {
        try sessionQueue.sync {
            try self.configureIfNeededLocked()
        }
    }

    func startPreview() {
        sessionQueue.async { [weak self] in
            guard let self else { return }
            try? self.configureIfNeededLocked()
            if !self.session.isRunning {
                self.session.startRunning()
            }
        }
    }

    /// Stops the running capture session but leaves the input/output graph
    /// configured so we can resume preview/recording without a full rebuild.
    func stopPreview() {
        sessionQueue.async { [weak self] in
            guard let self else { return }
            guard !self.movieOutput.isRecording else { return }
            if self.session.isRunning {
                self.session.stopRunning()
            }
        }
    }

    func startRecording(to url: URL, completion: ((Error?) -> Void)? = nil) {
        sessionQueue.async { [weak self] in
            guard let self else {
                DispatchQueue.main.async { completion?(DebugVideoRecorderError.cannotStart) }
                return
            }

            do {
                try self.configureIfNeededLocked()
            } catch {
                DispatchQueue.main.async { completion?(error) }
                return
            }

            try? FileManager.default.removeItem(at: url)
            self.outputURL = url

            if !self.session.isRunning {
                self.session.startRunning()
                // startRunning() is documented as blocking, but verify the
                // session is actually up before handing off to the movie output.
                if !self.session.isRunning {
                    print("[DebugVideoRecorder] session failed to start")
                    DispatchQueue.main.async { completion?(DebugVideoRecorderError.cannotStart) }
                    return
                }
            }

            // Store completion — it will be called from the delegate once
            // recording actually starts (didStartRecordingTo) or fails to start
            // (didFinishRecordingTo fires immediately with an error).
            self.startCompletion = completion
            self.movieOutput.startRecording(to: url, recordingDelegate: self)
        }
    }

    func stop(completion: @escaping (Result<URL, Error>) -> Void) {
        sessionQueue.async { [weak self] in
            guard let self else {
                DispatchQueue.main.async { completion(.failure(DebugVideoRecorderError.cannotStart)) }
                return
            }

            guard self.movieOutput.isRecording else {
                // Recording either never started or already finished.
                let url = self.outputURL
                DispatchQueue.main.async {
                    guard let url else {
                        completion(.failure(DebugVideoRecorderError.notRecording))
                        return
                    }
                    let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
                    let size = (attrs?[.size] as? NSNumber)?.intValue ?? 0
                    if size > 0 {
                        completion(.success(url))
                    } else {
                        completion(.failure(DebugVideoRecorderError.notRecording))
                    }
                }
                return
            }

            self.stopCompletion = completion
            self.movieOutput.stopRecording()
        }
    }

    /// Tear down the capture graph entirely. Call only when the recorder is
    /// being deallocated or the app is going to background.
    func releaseSession(completion: (() -> Void)? = nil) {
        sessionQueue.async { [weak self] in
            guard let self else {
                DispatchQueue.main.async { completion?() }
                return
            }
            self.teardownSessionLocked()
            DispatchQueue.main.async { completion?() }
        }
    }

    // MARK: - Private (sessionQueue)

    private func configureIfNeededLocked() throws {
        guard !isConfigured else { return }

        session.beginConfiguration()
        session.sessionPreset = .high

        // On Mac Catalyst, prefer the system's current default video device
        // (usually the built-in FaceTime camera). Using .builtInWideAngleCamera
        // with .unspecified can resolve to a Continuity Camera (iPhone) that
        // isn't nearby, silently producing an unconfigured session.
        // On iOS keep requesting the front-facing sensor explicitly.
        #if targetEnvironment(macCatalyst)
        let camera = AVCaptureDevice.default(for: .video)
        #else
        let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front)
        #endif
        guard let camera else {
            session.commitConfiguration()
            throw DebugVideoRecorderError.noCamera
        }

        let cameraInput = try AVCaptureDeviceInput(device: camera)
        guard session.canAddInput(cameraInput) else {
            session.commitConfiguration()
            throw DebugVideoRecorderError.cannotAddCameraInput
        }
        session.addInput(cameraInput)

        guard session.canAddOutput(movieOutput) else {
            session.commitConfiguration()
            throw DebugVideoRecorderError.cannotAddMovieOutput
        }
        session.addOutput(movieOutput)

        // Allow indefinite recording length and write a moov fragment every
        // second so a partial file is still playable if the session is
        // interrupted. This (combined with stable connection orientation) is
        // what was capping recordings to ~10s before.
        movieOutput.maxRecordedDuration = .positiveInfinity
        movieOutput.maxRecordedFileSize = 0
        movieOutput.movieFragmentInterval = CMTime(seconds: 1, preferredTimescale: 1000)

        // Pin the orientation/rotation once during configuration. Mutating the
        // connection while the session is running causes capture interruptions
        // and AVAssetWriter handler-add failures on Mac Catalyst (the source of
        // the truncated recordings).
        if let connection = movieOutput.connection(with: .video) {
            #if targetEnvironment(macCatalyst)
            if #available(iOS 17.0, *) {
                connection.videoRotationAngle = 0
            } else {
                connection.videoOrientation = .landscapeRight
            }
            #else
            if #available(iOS 17.0, *) {
                connection.videoRotationAngle = 90
            } else {
                connection.videoOrientation = .portrait
            }
            #endif
        }

        session.commitConfiguration()
        isConfigured = true
    }

    private func teardownSessionLocked() {
        if session.isRunning {
            session.stopRunning()
        }

        guard isConfigured else { return }
        session.beginConfiguration()
        for input in session.inputs {
            session.removeInput(input)
        }
        for output in session.outputs {
            session.removeOutput(output)
        }
        session.commitConfiguration()
        isConfigured = false
        outputURL = nil
    }

    // MARK: - AVCaptureFileOutputRecordingDelegate

    /// Called when the movie output has opened the file and is actively
    /// capturing frames. This is the correct place to confirm that recording
    /// started — `movieOutput.startRecording(to:)` merely *requests* a start.
    func fileOutput(
        _ output: AVCaptureFileOutput,
        didStartRecordingTo fileURL: URL,
        from connections: [AVCaptureConnection]
    ) {
        sessionQueue.async { [weak self] in
            guard let self else { return }
            let cb = self.startCompletion
            self.startCompletion = nil
            print("[DebugVideoRecorder] recording started → \(fileURL.lastPathComponent)")
            DispatchQueue.main.async { cb?(nil) }
        }
    }

    func fileOutput(
        _ output: AVCaptureFileOutput,
        didFinishRecordingTo outputFileURL: URL,
        from connections: [AVCaptureConnection],
        error: Error?
    ) {
        // The delegate fires on an arbitrary queue; jump to ours to serialize
        // state mutation and completion delivery.
        sessionQueue.async { [weak self] in
            guard let self else { return }

            // -11810 is "recording stopped successfully" — the file is valid.
            let nsError = error as NSError?
            let isBenignStop = nsError?.code == -11810
            let resolvedError: Error? = isBenignStop ? nil : error

            // If startCompletion is still set, didStartRecordingTo never fired:
            // the movie output failed before capturing a single frame.
            let startCb = self.startCompletion
            self.startCompletion = nil
            let stopCb = self.stopCompletion
            self.stopCompletion = nil

            if let startCb {
                // Early start failure — tell the MotionManager recording never
                // actually began so it can surface the error immediately.
                let err = resolvedError ?? DebugVideoRecorderError.cannotStart
                print("[DebugVideoRecorder] early start failure: \(err)")
                DispatchQueue.main.async {
                    startCb(err)
                    stopCb?(.failure(err))
                }
                return
            }

            DispatchQueue.main.async {
                if let resolvedError {
                    stopCb?(.failure(resolvedError))
                    return
                }

                let attrs = try? FileManager.default.attributesOfItem(atPath: outputFileURL.path)
                let size = (attrs?[.size] as? NSNumber)?.intValue ?? 0
                guard size > 0 else {
                    stopCb?(.failure(DebugVideoRecorderError.emptyRecording))
                    return
                }
                stopCb?(.success(outputFileURL))
            }
        }
    }
}

enum DebugVideoRecorderError: LocalizedError {
    case noCamera
    case cannotAddCameraInput
    case cannotAddMovieOutput
    case cannotStart
    case notRecording
    case emptyRecording

    var errorDescription: String? {
        switch self {
        case .noCamera:
            return "No front camera is available."
        case .cannotAddCameraInput:
            return "The camera could not be added to the recording session."
        case .cannotAddMovieOutput:
            return "The movie recorder could not be added to the capture session."
        case .cannotStart:
            return "The recorder is not ready to start."
        case .notRecording:
            return "The recorder was not recording."
        case .emptyRecording:
            return "The recording finished with an empty file."
        }
    }
}
