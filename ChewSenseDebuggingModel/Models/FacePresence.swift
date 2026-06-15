//
//  FacePresence.swift
//  ChewSenseDebuggingModel
//
//  Per-bundle face-presence track produced by the lab-only visual
//  auto-labeler in `02-DataPipeline/pipeline/visual_labeler.py` (or by
//  the heuristic fallback in `pipeline/mouth_shape.py`). Persisted as
//  `face_presence.json` next to the session video and consumed by the
//  unified review UI to grey-out spans where no face is visible — those
//  spans cannot be visually auto-labeled, so the reviewer knows they
//  must hand-label them and the pipeline knows to mask them from
//  training/eval.
//
//  Schema (matches Python writer):
//
//      {
//        "frame_rate": 30.0,
//        "intervals": [
//          { "start": 0.0,   "end": 5.2,  "present": true  },
//          { "start": 5.2,   "end": 7.4,  "present": false },
//          ...
//        ]
//      }
//

import Foundation

struct FacePresenceInterval: Codable, Equatable, Identifiable {
    var id: UUID
    var startTime: Double
    var endTime: Double
    var present: Bool

    init(
        id: UUID = UUID(),
        startTime: Double,
        endTime: Double,
        present: Bool
    ) {
        self.id = id
        self.startTime = startTime
        self.endTime = endTime
        self.present = present
    }

    enum CodingKeys: String, CodingKey {
        case startTime = "start"
        case endTime = "end"
        case present
    }

    // The Python writer omits `id` — synthesise on decode.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.id = UUID()
        self.startTime = try c.decode(Double.self, forKey: .startTime)
        self.endTime = try c.decode(Double.self, forKey: .endTime)
        self.present = try c.decode(Bool.self, forKey: .present)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(startTime, forKey: .startTime)
        try c.encode(endTime, forKey: .endTime)
        try c.encode(present, forKey: .present)
    }

    func contains(_ time: Double) -> Bool {
        time >= startTime && time < endTime
    }
}

struct FacePresenceTrack: Codable, Equatable {
    var frameRate: Double
    var intervals: [FacePresenceInterval]

    init(frameRate: Double = 30.0, intervals: [FacePresenceInterval] = []) {
        self.frameRate = frameRate
        self.intervals = intervals
    }

    enum CodingKeys: String, CodingKey {
        case frameRate = "frame_rate"
        case intervals
    }

    /// Returns true if no face is visible at `time` (or the track is silent
    /// about that moment, which we treat as "unknown / face missing" so
    /// edits there are conservative).
    func isFaceMissing(at time: Double) -> Bool {
        guard let interval = intervals.first(where: { $0.contains(time) }) else {
            // No interval covers this time — be conservative.
            return false
        }
        return !interval.present
    }

    /// Spans where the face is *missing*. Used by the timeline to render
    /// greyed bands.
    var missingSpans: [FacePresenceInterval] {
        intervals.filter { !$0.present }
    }

    static func load(from sessionURL: URL) -> FacePresenceTrack? {
        let url = sessionURL.appendingPathComponent("face_presence.json")
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(FacePresenceTrack.self, from: data)
    }
}
