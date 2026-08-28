//
//  LiveCanonicalSessionBundle.swift
//  ChewSenseLive
//
//  Canonical schema-v2 metadata for Live evaluation recordings. Motion rows
//  remain in the host-monotonic domain and session.json freezes the video
//  origin needed to map them to video-relative time.
//

import Foundation

struct LiveCaptureSnapshot {
    let sessionID: String
    let createdAt: String
    let activity: String
    let videoFileName: String
    let motionFileName: String
    let videoClockOriginHostTime: Double
    let usedConfirmedVideoOrigin: Bool
    let firstMotionHostTime: Double?
    let lastMotionHostTime: Double?
    let motionSampleCount: Int
    let motionSampleGapCount: Int
    let largestMotionSampleGapSec: Double
    let interrupted: Bool
    let interruptionCount: Int
    let finalized: Bool
    let modelID: String
    let modelMode: String
}

enum LiveCanonicalSessionWriter {
    static let schemaVersion = CanonicalCaptureContract.schemaVersion

    static func write(
        snapshot: LiveCaptureSnapshot,
        sessionURL: URL,
        sourceAppVersion: String
    ) throws {
        let videoURL = sessionURL.appendingPathComponent(snapshot.videoFileName)
        let motionURL = sessionURL.appendingPathComponent(snapshot.motionFileName)
        let firstMotion = snapshot.firstMotionHostTime
        let lastMotion = snapshot.lastMotionHostTime
        let motionDuration: Double? = {
            guard let firstMotion, let lastMotion else { return nil }
            return max(0, lastMotion - firstMotion)
        }()
        let sessionDuration: Double? = lastMotion.map {
            max(0, $0 - snapshot.videoClockOriginHostTime)
        }
        let sensorRate: Double? = {
            guard let motionDuration, motionDuration > 0 else { return nil }
            return Double(max(0, snapshot.motionSampleCount - 1)) / motionDuration
        }()
        let videoPresent = FileManager.default.fileExists(atPath: videoURL.path)
        let motionPresent = FileManager.default.fileExists(atPath: motionURL.path)

        let video = try CanonicalCaptureContract.mediaReference(
            url: videoURL,
            mediaType: "video",
            includeHash: snapshot.finalized
        )
        let motion = try CanonicalCaptureContract.mediaReference(
            url: motionURL,
            mediaType: "motion_csv",
            includeHash: snapshot.finalized
        )
        let clockSync = CanonicalCaptureContract.clockSync(
            videoOriginHostTime: snapshot.videoClockOriginHostTime,
            videoStartHostTime: snapshot.usedConfirmedVideoOrigin
                ? snapshot.videoClockOriginHostTime : nil,
            firstMotionHostTime: firstMotion,
            method: snapshot.usedConfirmedVideoOrigin
                ? "shared_host_clock" : "capture_clock_fallback",
            confidence: snapshot.usedConfirmedVideoOrigin ? "high" : "low"
        )
        let capture = CanonicalCaptureContract.capture(
            fps: 30.0,
            sensorRateHz: sensorRate,
            durationSeconds: sessionDuration,
            interrupted: snapshot.interrupted,
            interruptionCount: snapshot.interruptionCount,
            motionSampleGapCount: snapshot.motionSampleGapCount,
            largestMotionSampleGapSeconds: snapshot.largestMotionSampleGapSec
        )
        var notes: [String] = []
        if !snapshot.usedConfirmedVideoOrigin {
            notes.append("Video start callback was unavailable; audit the fallback clock origin before review.")
        }
        if snapshot.interrupted {
            notes.append("One or more headphone-motion interruptions occurred during capture.")
        }

        let payload = CanonicalCaptureContract.manifest(
            sessionID: snapshot.sessionID,
            sourceApp: "live",
            sourceAppVersion: sourceAppVersion,
            activity: snapshot.activity,
            status: "collected",
            createdAt: snapshot.createdAt,
            video: video,
            motion: motion,
            audio: [
                "enabled": false,
                "permission": "not_requested"
            ],
            clockSync: clockSync,
            capture: capture,
            quality: [
                "video_present": videoPresent,
                "motion_present": motionPresent,
                "motion_samples": snapshot.motionSampleCount,
                "notes": notes
            ],
            activeModelIDs: [
                "motion": snapshot.modelID,
                "motion_mode": snapshot.modelMode
            ]
        )
        try CanonicalCaptureContract.writeJSON(
            payload,
            to: sessionURL.appendingPathComponent("session.json")
        )
    }

    /// Synchronous by contract: callers may publish the completed session URL
    /// immediately after this method returns.
    static func writeMealMetrics(_ payload: [String: Any], to sessionURL: URL) throws {
        try CanonicalCaptureContract.writeJSON(
            payload,
            to: sessionURL.appendingPathComponent("meal_metrics.json")
        )
    }
}
