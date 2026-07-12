//
//  ChewStroke.swift
//  ChewSenseLive
//
//  Canonical schema for a single jaw-stroke event detected in the
//  motion stream. Mirrors `pipeline/events.py::ChewStroke` — keep
//  field names in sync.
//

import Foundation

/// Provenance for a chew stroke. Mirrors the Python CHEW_STROKE_SOURCES set.
enum ChewStrokeSource: String, Codable, CaseIterable {
    /// Python/Swift peak-detector output.
    case heuristic
    /// Learned chew-stroke head output (future).
    case model
    /// Seeded from video (mouth-openness peak), pre-review.
    case auto
    /// Auto/heuristic candidate confirmed by a human reviewer.
    case confirmed
    /// Placed by a human reviewer.
    case manual
}

/// A single jaw-stroke event.
///
/// Timestamps are in seconds, relative to session start, matching
/// `motion.csv` and `predictions.csv` in field-test bundles.
struct ChewStroke: Codable, Equatable, Identifiable {
    var id: UUID
    /// Seconds, session-relative.
    var timestamp: Double
    /// Detector confidence in [0, 1].
    var confidence: Double
    var source: ChewStrokeSource

    init(
        id: UUID = UUID(),
        timestamp: Double,
        confidence: Double = 1.0,
        source: ChewStrokeSource = .heuristic
    ) {
        self.id = id
        self.timestamp = timestamp
        self.confidence = confidence
        self.source = source
    }

    enum CodingKeys: String, CodingKey {
        case id
        case timestamp
        case confidence
        case source
    }
}
