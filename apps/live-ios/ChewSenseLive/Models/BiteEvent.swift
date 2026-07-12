//
//  BiteEvent.swift
//  ChewSenseLive
//
//  Canonical schema for a discrete bite. Mirrors
//  `pipeline/events.py::BiteEvent` — keep field names in sync.
//

import Foundation

/// A discrete bite: a contiguous chewing episode plus its strokes.
struct BiteEvent: Codable, Equatable, Identifiable {
    var id: UUID
    /// Bite onset, seconds (session-relative).
    var startTime: Double
    /// Bite end, seconds (session-relative).
    var endTime: Double
    /// Average chewing probability across the bite, in [0, 1].
    var confidence: Double
    /// Number of chew strokes inside the bite.
    var chewCount: Int
    /// Per-stroke timestamps and confidences.
    var chewStrokes: [ChewStroke]

    init(
        id: UUID = UUID(),
        startTime: Double,
        endTime: Double,
        confidence: Double = 0.0,
        chewCount: Int = 0,
        chewStrokes: [ChewStroke] = []
    ) {
        self.id = id
        self.startTime = startTime
        self.endTime = endTime
        self.confidence = confidence
        self.chewCount = chewCount
        self.chewStrokes = chewStrokes
    }

    var duration: Double { max(0, endTime - startTime) }

    /// Match Python `BiteEvent.to_dict()` field names so JSON written by
    /// either side round-trips through a single schema.
    enum CodingKeys: String, CodingKey {
        case id
        case startTime = "start_time"
        case endTime = "end_time"
        case confidence
        case chewCount = "chew_count"
        case chewStrokes = "chew_strokes"
    }
}
