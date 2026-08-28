//
//  ChewingBout.swift
//  ChewSenseLive
//
//  Diagnostic schema for a contiguous chewing bout. A bout is not a bite;
//  only a separately verified food-entry event can establish bite grouping.
//

import Foundation

struct ChewingBout: Codable, Equatable, Identifiable {
    var id: UUID
    /// Bout onset in the host-monotonic motion clock.
    var startTime: Double
    /// Bout end in the host-monotonic motion clock.
    var endTime: Double
    /// Eating-head confidence associated with the bout.
    var confidence: Double
    /// Number of decoded chew events inside the bout.
    var chewCount: Int
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

    enum CodingKeys: String, CodingKey {
        case id
        case startTime = "start_time"
        case endTime = "end_time"
        case confidence
        case chewCount = "chew_count"
        case chewStrokes = "chew_strokes"
    }
}
