import AVFoundation
import Foundation

struct CaptureTimingSnapshot {
    var videoStartHostTime: Double?
    var firstVideoFrameHostTime: Double?
    var firstMotionHostTime: Double?
    var lastMotionHostTime: Double?
    var motionSampleCount: Int
    var motionSampleGapCount: Int
    var largestMotionSampleGap: Double?
    var interrupted: Bool
    var interruptionCount: Int
    var reviewAudioEnabled: Bool
    var reviewAudioPermission: String
    var reviewAudioPath: String?
}

enum CanonicalBundleWriter {
    private static let bundleSchemaVersion = CanonicalCaptureContract.schemaVersion
    private static let annotationSchemaVersion = CanonicalCaptureContract.schemaVersion

    static func writeInitialBundle(
        videoURL: URL,
        isEating: Bool?,
        timing: CaptureTimingSnapshot
    ) throws {
        let folder = videoURL.deletingLastPathComponent()
        let motionURL = videoURL.deletingPathExtension().appendingPathExtension("csv")
        let now = ISO8601DateFormatter().string(from: Date())
        let videoOrigin = timing.videoStartHostTime
            ?? timing.firstVideoFrameHostTime
            ?? timing.firstMotionHostTime
            ?? 0
        let method = timing.videoStartHostTime != nil
            ? "shared_host_clock"
            : "capture_clock_fallback"
        let confidence = timing.videoStartHostTime != nil ? "high" : "low"
        let motionDuration: Double? = {
            let first = timing.firstMotionHostTime
            let last = timing.lastMotionHostTime
            guard let first, let last else { return nil }
            return max(0, last - first)
        }()
        let motionExtentFromVideoOrigin: Double? = timing.lastMotionHostTime.map {
            max(0, $0 - videoOrigin)
        }
        let sensorRate: Double? = {
            guard let duration = motionDuration, duration > 0 else { return nil }
            return Double(max(0, timing.motionSampleCount - 1)) / duration
        }()
        let videoDuration: Double? = {
            let seconds = CMTimeGetSeconds(AVURLAsset(url: videoURL).duration)
            return seconds.isFinite && seconds > 0
                ? seconds
                : motionExtentFromVideoOrigin
        }()
        let audioURL = timing.reviewAudioPath.map { folder.appendingPathComponent($0) }
        let audioHash = try audioURL.map { try CanonicalCaptureContract.sha256(of: $0) }
        let appVersion = Bundle.main.object(
            forInfoDictionaryKey: "CFBundleShortVersionString"
        ) as? String ?? "unknown"

        let manifest = CanonicalCaptureContract.manifest(
            sessionID: folder.lastPathComponent,
            sourceApp: "collect",
            sourceAppVersion: appVersion,
            activity: isEating == true ? "eating" : isEating == false ? "not_eating" : "unknown",
            status: isEating == false ? "coarse_labeled" : "collected",
            createdAt: now,
            video: try CanonicalCaptureContract.mediaReference(
                url: videoURL,
                mediaType: "video",
                includeHash: true
            ),
            motion: try CanonicalCaptureContract.mediaReference(
                url: motionURL,
                mediaType: "motion_csv",
                includeHash: true
            ),
            audio: CanonicalCaptureContract.compact([
                "path": timing.reviewAudioPath,
                "media_type": timing.reviewAudioEnabled ? "audio_m4a" : nil,
                "purpose": timing.reviewAudioEnabled ? "vad_and_human_review_only" : nil,
                "shipping_model_eligible": false,
                "safe_to_delete_after_finalization": true,
                "sha256": audioHash,
                "enabled": timing.reviewAudioEnabled,
                "permission": timing.reviewAudioPermission
            ]),
            clockSync: CanonicalCaptureContract.clockSync(
                videoOriginHostTime: videoOrigin,
                videoStartHostTime: timing.videoStartHostTime,
                firstVideoFrameHostTime: timing.firstVideoFrameHostTime,
                firstMotionHostTime: timing.firstMotionHostTime,
                method: method,
                confidence: confidence
            ),
            capture: CanonicalCaptureContract.capture(
                fps: 30.0,
                sensorRateHz: sensorRate,
                durationSeconds: videoDuration,
                interrupted: timing.interrupted,
                interruptionCount: timing.interruptionCount,
                motionSampleGapCount: timing.motionSampleGapCount,
                largestMotionSampleGapSeconds: timing.largestMotionSampleGap
            ),
            quality: [
                "video_present": FileManager.default.fileExists(atPath: videoURL.path),
                "motion_present": FileManager.default.fileExists(atPath: motionURL.path),
                "review_audio_present": timing.reviewAudioPath != nil,
                "motion_samples": timing.motionSampleCount,
                "notes": timing.videoStartHostTime == nil
                    ? ["Writer start was unavailable; first frame supplied the video-clock origin. Audit before review."]
                    : []
            ]
        )
        try CanonicalCaptureContract.writeJSON(
            manifest,
            to: folder.appendingPathComponent("session.json")
        )
        if isEating == false {
            // The capture choice is an explicit human all-negative decision.
            // Keep the acquired motion file immutable and materialize that
            // decision directly in video-relative time.
            try writeCoarseLabels(
                intervals: [[
                    "start_ts": 0.0,
                    "end_ts": videoDuration ?? 0.0,
                    "label": "not_chewing",
                    "source": "human_coarse"
                ]],
                forMotionCSV: motionURL
            )
        } else {
            // Compatibility path for an explicitly pre-labelled CSV. New
            // positive/unknown captures have no label column and remain
            // `collected` until the wearer completes coarse review.
            try refreshCoarseLabels(csvURL: motionURL)
        }
    }

    static func refreshCoarseLabels(csvURL: URL) throws {
        guard FileManager.default.fileExists(atPath: csvURL.path) else { return }
        let text = try String(contentsOf: csvURL, encoding: .utf8)
        let rows = text.split(separator: "\n", omittingEmptySubsequences: true)
        guard let headerRow = rows.first else { return }
        let headers = headerRow.split(
            separator: ",",
            omittingEmptySubsequences: false
        ).map { String($0).lowercased() }
        guard let timestampIndex = headers.firstIndex(of: "timestamp"),
              let labelIndex = headers.firstIndex(of: "label") else {
            return
        }

        var samples: [(time: Double, chewing: Bool)] = []
        for row in rows.dropFirst() {
            let columns = row.split(
                separator: ",",
                omittingEmptySubsequences: false
            ).map(String.init)
            guard columns.indices.contains(timestampIndex),
                  columns.indices.contains(labelIndex),
                  let timestamp = Double(columns[timestampIndex]) else { continue }
            let value = columns[labelIndex].trimmingCharacters(
                in: .whitespacesAndNewlines
            ).lowercased()
            samples.append((
                timestamp,
                ["1", "true", "yes", "chewing", "chew"].contains(value)
            ))
        }
        guard !samples.isEmpty else { return }
        let origin = try videoClockOrigin(for: csvURL)

        var intervals: [[String: Any]] = []
        var start = 0
        for index in 1...samples.count {
            if index < samples.count,
               samples[index].chewing == samples[start].chewing {
                continue
            }
            intervals.append([
                "start_ts": max(0, samples[start].time - origin),
                "end_ts": max(0, samples[index - 1].time - origin),
                "label": samples[start].chewing ? "chewing" : "not_chewing",
                "source": "human_coarse"
            ])
            start = index
        }

        try writeCoarseLabels(intervals: intervals, forMotionCSV: csvURL)
    }

    /// Writes human coarse decisions without changing the captured motion
    /// bytes. Interval timestamps are video-relative by contract.
    static func writeCoarseLabels(
        intervals: [[String: Any]],
        forMotionCSV csvURL: URL
    ) throws {
        _ = try videoClockOrigin(for: csvURL)
        let folder = csvURL.deletingLastPathComponent()
        let manifestURL = folder.appendingPathComponent("session.json")
        let annotations = folder.appendingPathComponent(
            "annotations",
            isDirectory: true
        )
        try FileManager.default.createDirectory(
            at: annotations,
            withIntermediateDirectories: true
        )
        try CanonicalCaptureContract.writeJSON([
            "schema_version": annotationSchemaVersion,
            "artifact_type": "coarse_labels",
            "session_id": folder.lastPathComponent,
            "intervals": intervals
        ], to: annotations.appendingPathComponent("coarse_labels.json"))

        if var manifest = try? readJSON(manifestURL) {
            manifest["status"] = "coarse_reviewed"
            manifest["updated_at"] = ISO8601DateFormatter().string(from: Date())
            try CanonicalCaptureContract.writeJSON(manifest, to: manifestURL)
        }
    }

    /// Chewing spans in the saved coarse labels, and whether every interval
    /// still comes from the guided instructions (none edited by the wearer).
    static func savedChewingSpans(in folder: URL) -> (spans: [ClosedRange<Double>], instructed: Bool)? {
        let url = folder.appendingPathComponent("annotations/coarse_labels.json")
        guard let intervals = (try? readJSON(url))?["intervals"] as? [[String: Any]] else { return nil }
        let spans = intervals.filter { $0["label"] as? String == "chewing" }.compactMap { interval -> ClosedRange<Double>? in
            guard let start = interval["start_ts"] as? Double, let end = interval["end_ts"] as? Double,
                  start.isFinite, end > start else { return nil }
            return start...end
        }
        let instructed = !intervals.isEmpty && intervals.allSatisfy { $0["basis"] as? String == "guided_instruction" }
        return (spans, instructed)
    }

    /// Returns video-relative time zero for absolute host-clock motion rows.
    /// Version 2 never infers this value from the first motion sample because a
    /// delayed AirPods sample would shift every human coarse label.
    static func videoClockOrigin(for csvURL: URL) throws -> Double {
        let manifestURL = csvURL.deletingLastPathComponent().appendingPathComponent("session.json")
        let manifest = try readJSON(manifestURL)
        let schemaVersion = manifest["schema_version"] as? String ?? ""
        guard schemaVersion == bundleSchemaVersion else {
            throw BundleError.unsupportedSchema(schemaVersion.isEmpty ? "missing" : schemaVersion)
        }
        guard let clockSync = manifest["clock_sync"] as? [String: Any],
              let origin = clockSync["video_clock_origin_host_time_s"] as? Double,
              origin.isFinite else {
            throw BundleError.missingVideoClockOrigin
        }
        return origin
    }

    private static func readJSON(_ url: URL) throws -> [String: Any] {
        let data = try Data(contentsOf: url)
        return try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
    }

}

enum BundleError: LocalizedError {
    case unsupportedSchema(String)
    case missingVideoClockOrigin
    case invalidVideoDuration

    var errorDescription: String? {
        switch self {
        case .unsupportedSchema(let version):
            return "Session schema \(version) is not writable as 2.0.0. Run the explicit migration first."
        case .missingVideoClockOrigin:
            return "session.json is missing clock_sync.video_clock_origin_host_time_s; coarse labels were not changed."
        case .invalidVideoDuration:
            return "The video duration is unavailable; coarse labels were not saved."
        }
    }
}
