import Foundation

struct CaptureTimingSnapshot {
    var videoStartHostTime: Double?
    var firstVideoFrameHostTime: Double?
    var firstMotionHostTime: Double?
    var lastMotionHostTime: Double?
    var motionSampleCount: Int
}

enum CanonicalBundleWriter {
    private static let bundleSchemaVersion = "1.0.0"
    private static let annotationSchemaVersion = "1.0.0"

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
            : "legacy_first_motion_fallback"
        let confidence = timing.videoStartHostTime != nil ? "high" : "low"
        let motionDuration: Double? = {
            let first = timing.firstMotionHostTime
            let last = timing.lastMotionHostTime
            guard let first, let last else { return nil }
            return max(0, last - first)
        }()
        let sensorRate: Double? = {
            guard let duration = motionDuration, duration > 0 else { return nil }
            return Double(max(0, timing.motionSampleCount - 1)) / duration
        }()
        let appVersion = Bundle.main.object(
            forInfoDictionaryKey: "CFBundleShortVersionString"
        ) as? String ?? "unknown"

        let manifest: [String: Any] = [
            "schema_version": bundleSchemaVersion,
            "session_id": folder.lastPathComponent,
            "wearer_id": "local-user",
            "source_app": "collect",
            "source_app_version": appVersion,
            "activity": isEating == true ? "eating" : isEating == false ? "not_eating" : "unknown",
            "status": isEating == false ? "coarse_labeled" : "collected",
            "created_at": now,
            "updated_at": now,
            "video": [
                "path": videoURL.lastPathComponent,
                "media_type": "video"
            ],
            "motion": [
                "path": motionURL.lastPathComponent,
                "media_type": "motion_csv"
            ],
            "clock_sync": compact([
                "time_domain": "host_monotonic",
                "video_start_host_time_s": timing.videoStartHostTime,
                "first_video_frame_host_time_s": timing.firstVideoFrameHostTime,
                "first_motion_host_time_s": timing.firstMotionHostTime,
                "motion_to_video_offset_s": videoOrigin,
                "estimated_drift_ppm": 0.0,
                "method": method,
                "confidence": confidence
            ]),
            "capture": compact([
                "fps": 30.0,
                "sensor_rate_hz": sensorRate,
                "duration_s": motionDuration,
                "interrupted": false
            ]),
            "quality": [
                "video_present": FileManager.default.fileExists(atPath: videoURL.path),
                "motion_present": FileManager.default.fileExists(atPath: motionURL.path),
                "motion_samples": timing.motionSampleCount,
                "notes": timing.videoStartHostTime == nil
                    ? ["Shared host-clock origin was unavailable; verify alignment."]
                    : []
            ],
            "active_model_ids": [:],
            "annotation_revision": 0
        ]
        try writeJSON(manifest, to: folder.appendingPathComponent("session.json"))
        try refreshCoarseLabels(csvURL: motionURL)
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
        let manifestURL = csvURL.deletingLastPathComponent()
            .appendingPathComponent("session.json")
        let manifest = try? readJSON(manifestURL)
        let clockSync = manifest?["clock_sync"] as? [String: Any]
        let measuredOrigin = clockSync?["motion_to_video_offset_s"] as? Double
        guard let origin = measuredOrigin ?? samples.first?.time else { return }

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
                "source": "human"
            ])
            start = index
        }

        let folder = csvURL.deletingLastPathComponent()
        let annotations = folder.appendingPathComponent(
            "annotations",
            isDirectory: true
        )
        try FileManager.default.createDirectory(
            at: annotations,
            withIntermediateDirectories: true
        )
        try writeJSON([
            "schema_version": annotationSchemaVersion,
            "session_id": folder.lastPathComponent,
            "intervals": intervals
        ], to: annotations.appendingPathComponent("coarse_labels.json"))

        if var manifest = try? readJSON(manifestURL) {
            manifest["status"] = "coarse_labeled"
            manifest["updated_at"] = ISO8601DateFormatter().string(from: Date())
            try writeJSON(manifest, to: manifestURL)
        }
    }

    private static func compact(_ dictionary: [String: Any?]) -> [String: Any] {
        dictionary.reduce(into: [:]) { result, item in
            if let value = item.value { result[item.key] = value }
        }
    }

    private static func readJSON(_ url: URL) throws -> [String: Any] {
        let data = try Data(contentsOf: url)
        return try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
    }

    private static func writeJSON(_ value: Any, to url: URL) throws {
        let data = try JSONSerialization.data(
            withJSONObject: value,
            options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        )
        try data.write(to: url, options: .atomic)
    }
}
