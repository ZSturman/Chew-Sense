import CryptoKit
import Foundation

struct CaptureInterruptionSnapshot: Equatable {
    let interrupted: Bool
    let count: Int
}

/// Thread-safe accounting shared by both capture apps. AVCapture can emit a
/// runtime error while an interruption notification is already active; that
/// pair represents one interruption rather than two.
final class CaptureInterruptionTracker {
    private let lock = NSLock()
    private var interruptionCount = 0
    private var avCaptureInterruptionActive = false

    func reset() {
        lock.withLock {
            interruptionCount = 0
            avCaptureInterruptionActive = false
        }
    }

    func recordMotionInterruption() {
        lock.withLock {
            interruptionCount += 1
        }
    }

    func avCaptureWasInterrupted() {
        lock.withLock {
            guard !avCaptureInterruptionActive else { return }
            avCaptureInterruptionActive = true
            interruptionCount += 1
        }
    }

    func avCaptureInterruptionEnded() {
        lock.withLock {
            avCaptureInterruptionActive = false
        }
    }

    func avCaptureRuntimeError() {
        lock.withLock {
            if !avCaptureInterruptionActive {
                interruptionCount += 1
            }
            // Runtime errors do not reliably receive a matching interruption-
            // ended notification. Treat a later error as a new occurrence.
            avCaptureInterruptionActive = false
        }
    }

    func snapshot() -> CaptureInterruptionSnapshot {
        lock.withLock {
            CaptureInterruptionSnapshot(
                interrupted: interruptionCount > 0,
                count: interruptionCount
            )
        }
    }
}

/// Shared schema-v2 construction used by Collect and Live evaluation capture.
/// App-specific controllers own recording; this type owns the serialized
/// session contract so clocks, media hashes, gaps, and interruptions cannot
/// drift between targets.
enum CanonicalCaptureContract {
    static let schemaVersion = "2.0.0"
    /// Matches the 50 Hz temporal materializer: four missing resample steps are
    /// a capture gap in both Collect and Live.
    static let motionGapThresholdSeconds = 0.08

    static func manifest(
        sessionID: String,
        sourceApp: String,
        sourceAppVersion: String,
        activity: String,
        status: String,
        createdAt: String,
        video: [String: Any],
        motion: [String: Any],
        audio: [String: Any],
        clockSync: [String: Any],
        capture: [String: Any],
        quality: [String: Any],
        activeModelIDs: [String: String] = [:],
        legacyReviewRequired: Bool = false
    ) -> [String: Any] {
        [
            "schema_version": schemaVersion,
            "session_id": sessionID,
            "wearer_id": "local-user",
            "source_app": sourceApp,
            "source_app_version": sourceAppVersion,
            "activity": activity,
            "status": status,
            "created_at": createdAt,
            "updated_at": ISO8601DateFormatter().string(from: Date()),
            "video": video,
            "motion": motion,
            "audio": audio,
            "clock_sync": clockSync,
            "capture": capture,
            "quality": quality,
            "active_model_ids": activeModelIDs,
            "annotation_revision": 0,
            "legacy_review_required": legacyReviewRequired
        ]
    }

    static func clockSync(
        videoOriginHostTime: Double,
        videoStartHostTime: Double?,
        firstVideoFrameHostTime: Double? = nil,
        firstMotionHostTime: Double?,
        method: String,
        confidence: String,
        estimatedDriftPPM: Double = 0,
        driftStatus: String = "not_estimated"
    ) -> [String: Any] {
        compact([
            "time_domain": "host_monotonic",
            "video_clock_origin_host_time_s": videoOriginHostTime,
            "video_start_host_time_s": videoStartHostTime,
            "first_video_frame_host_time_s": firstVideoFrameHostTime,
            "first_motion_host_time_s": firstMotionHostTime,
            "motion_to_video_offset_s": firstMotionHostTime.map {
                $0 - videoOriginHostTime
            },
            "estimated_drift_ppm": estimatedDriftPPM,
            "drift_status": driftStatus,
            "method": method,
            "confidence": confidence
        ])
    }

    static func capture(
        fps: Double,
        sensorRateHz: Double?,
        durationSeconds: Double?,
        interrupted: Bool,
        interruptionCount: Int,
        motionSampleGapCount: Int,
        largestMotionSampleGapSeconds: Double?
    ) -> [String: Any] {
        compact([
            "fps": fps,
            "sensor_rate_hz": sensorRateHz,
            "duration_s": durationSeconds,
            "interrupted": interrupted,
            "interruption_count": interruptionCount,
            "motion_sample_gap_count": motionSampleGapCount,
            "largest_motion_sample_gap_s": largestMotionSampleGapSeconds
        ])
    }

    static func mediaReference(
        url: URL,
        mediaType: String,
        includeHash: Bool
    ) throws -> [String: Any] {
        var value: [String: Any] = [
            "path": url.lastPathComponent,
            "media_type": mediaType
        ]
        if includeHash, FileManager.default.fileExists(atPath: url.path) {
            value["sha256"] = try sha256(of: url)
        }
        return value
    }

    static func sha256(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1_048_576), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    static func writeJSON(_ value: Any, to url: URL) throws {
        let data = try JSONSerialization.data(
            withJSONObject: value,
            options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        )
        try data.write(to: url, options: .atomic)
    }

    static func compact(_ dictionary: [String: Any?]) -> [String: Any] {
        dictionary.reduce(into: [:]) { result, item in
            if let value = item.value {
                result[item.key] = value
            }
        }
    }
}
