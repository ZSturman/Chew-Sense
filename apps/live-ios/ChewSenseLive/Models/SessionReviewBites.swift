//
//  SessionReviewBites.swift
//  ChewSenseLive
//
//  Review-time models for per-bite boundary and per-chew-stroke ground
//  truth. Kept separate from `SessionReviewModels.swift` so the existing
//  chewing / not_chewing / correct / FP / FN review flow and its label
//  resolver are untouched.
//
//  These objects are written into a session bundle as
//  `bite_review.json` next to the existing review artifacts, and are
//  consumed by `pipeline/bite_metrics.py` as ground truth.
//

import Foundation

/// Provenance for a reviewer-managed bite boundary.
enum ReviewBiteSource: String, Codable, CaseIterable {
    /// Auto-derived from the runtime `BiteSegmenter` (i.e. the model's
    /// own bite proposal — the reviewer corrects these).
    case auto
    /// Reviewer-confirmed an `auto` proposal as-is.
    case confirmed
    /// Placed or edited manually by the reviewer.
    case manual
}

/// Reviewer-managed bite boundary. Time units: seconds, session-relative.
struct ReviewBiteBoundary: Identifiable, Codable, Equatable {
    var id: UUID
    var startTime: Double
    var endTime: Double
    var source: ReviewBiteSource
    var note: String

    init(
        id: UUID = UUID(),
        startTime: Double,
        endTime: Double,
        source: ReviewBiteSource = .manual,
        note: String = ""
    ) {
        self.id = id
        self.startTime = startTime
        self.endTime = endTime
        self.source = source
        self.note = note
    }

    var duration: Double { max(0, endTime - startTime) }

    func clamped(to duration: Double) -> ReviewBiteBoundary {
        let safeDuration = max(duration, 0)
        let start = min(max(startTime, 0), safeDuration)
        let end = min(max(endTime, start), safeDuration)
        return ReviewBiteBoundary(
            id: id,
            startTime: start,
            endTime: end,
            source: source,
            note: note
        )
    }

    func contains(_ time: Double) -> Bool {
        time >= startTime && time < endTime
    }

    enum CodingKeys: String, CodingKey {
        case id
        case startTime = "start_time"
        case endTime = "end_time"
        case source
        case note
    }
}

/// Reviewer-managed single chew-stroke timestamp.
///
/// A `ReviewStroke` carries the same provenance vocabulary as
/// `pipeline.events.ChewStroke`, but its `source` defaults to `.auto`
/// when seeded from the runtime detector or video, and is bumped to
/// `.confirmed` when the reviewer keeps it as-is, or `.manual` when
/// the reviewer adds a new one by hand.
struct ReviewStroke: Identifiable, Codable, Equatable {
    var id: UUID
    var timestamp: Double
    var source: ChewStrokeSource
    var note: String

    init(
        id: UUID = UUID(),
        timestamp: Double,
        source: ChewStrokeSource = .auto,
        note: String = ""
    ) {
        self.id = id
        self.timestamp = timestamp
        self.source = source
        self.note = note
    }

    enum CodingKeys: String, CodingKey {
        case id
        case timestamp
        case source
        case note
    }
}

/// Top-level review document for the bites/strokes tab.
///
/// Persisted as `bite_review.json` inside the session bundle.
struct BiteReviewDocument: Codable, Equatable {
    /// Reviewer-managed bite boundaries (ground truth).
    var bites: [ReviewBiteBoundary]
    /// Reviewer-managed chew-stroke timestamps (ground truth).
    var strokes: [ReviewStroke]
    /// Free-form note for the whole session.
    var sessionNote: String

    init(
        bites: [ReviewBiteBoundary] = [],
        strokes: [ReviewStroke] = [],
        sessionNote: String = ""
    ) {
        self.bites = bites
        self.strokes = strokes
        self.sessionNote = sessionNote
    }

    enum CodingKeys: String, CodingKey {
        case bites
        case strokes
        case sessionNote = "session_note"
    }

    /// Strokes that fall within the (start, end) of any bite boundary.
    func strokes(in bite: ReviewBiteBoundary) -> [ReviewStroke] {
        strokes.filter { bite.contains($0.timestamp) }
    }

    /// Per-bite chew counts derived from the strokes array. A stroke
    /// outside every bite is ignored — it's still in the document but
    /// won't be attributed to any bite's count.
    var chewCountsPerBite: [UUID: Int] {
        var counts: [UUID: Int] = [:]
        for bite in bites {
            counts[bite.id] = strokes.filter { bite.contains($0.timestamp) }.count
        }
        return counts
    }
}
