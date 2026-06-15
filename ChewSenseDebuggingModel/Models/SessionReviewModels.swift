//
//  SessionReviewModels.swift
//  ChewSenseDebuggingModel
//
//  Models and pure label-resolution logic for reviewed field-test sessions.
//

import Foundation

enum ReviewSegmentKind: String, Codable, CaseIterable, Identifiable {
    case chewing
    case notChewing = "not_chewing"
    case correct
    case falsePositive = "false_positive"
    case falseNegative = "false_negative"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .chewing:
            return "Chewing"
        case .notChewing:
            return "Not Chewing"
        case .correct:
            return "Correct"
        case .falsePositive:
            return "False Positive"
        case .falseNegative:
            return "False Negative"
        }
    }

    static var correctionKinds: [ReviewSegmentKind] {
        [.chewing, .notChewing]
    }

    var isBinaryCorrection: Bool {
        self == .chewing || self == .notChewing
    }
}

struct ReviewSegment: Identifiable, Codable, Equatable {
    var id: UUID
    var startTime: Double
    var endTime: Double
    var kind: ReviewSegmentKind
    var note: String

    init(
        id: UUID = UUID(),
        startTime: Double,
        endTime: Double,
        kind: ReviewSegmentKind,
        note: String = ""
    ) {
        self.id = id
        self.startTime = startTime
        self.endTime = endTime
        self.kind = kind
        self.note = note
    }

    func clamped(to duration: Double) -> ReviewSegment {
        let safeDuration = max(duration, 0)
        let start = min(max(startTime, 0), safeDuration)
        let end = min(max(endTime, start), safeDuration)
        return ReviewSegment(id: id, startTime: start, endTime: end, kind: kind, note: note)
    }

    func contains(_ time: Double) -> Bool {
        time >= startTime && time < endTime
    }
}

struct PredictionSpan: Identifiable, Codable, Equatable {
    var id: UUID
    var startTime: Double
    var endTime: Double
    var isChewing: Bool
    var probability: Double?
    var smoothedProbability: Double?

    init(
        id: UUID = UUID(),
        startTime: Double,
        endTime: Double,
        isChewing: Bool,
        probability: Double? = nil,
        smoothedProbability: Double? = nil
    ) {
        self.id = id
        self.startTime = startTime
        self.endTime = endTime
        self.isChewing = isChewing
        self.probability = probability
        self.smoothedProbability = smoothedProbability
    }

    func contains(_ time: Double) -> Bool {
        time >= startTime && time < endTime
    }
}

struct ResolvedReviewSpan: Identifiable, Equatable {
    let id: UUID
    let sourceSegmentID: UUID
    let startTime: Double
    let endTime: Double
    let isChewing: Bool
    let sourceKind: ReviewSegmentKind

    init(
        sourceSegmentID: UUID,
        startTime: Double,
        endTime: Double,
        isChewing: Bool,
        sourceKind: ReviewSegmentKind
    ) {
        self.id = UUID()
        self.sourceSegmentID = sourceSegmentID
        self.startTime = startTime
        self.endTime = endTime
        self.isChewing = isChewing
        self.sourceKind = sourceKind
    }
}

struct ReviewRecordingConfiguration: Equatable {
    var alpha: Double? = nil
    var highThreshold: Double? = nil
    var lowThreshold: Double? = nil
    var minStartWindows: Int? = nil
    var minEndWindows: Int? = nil
    var testScenario: String? = nil
    var platform: String? = nil
    var modelID: String? = nil
    var datasetHash: String? = nil
    var datasetStatus: String? = nil
    var modelPackageHash: String? = nil
    var activitySlug: String? = nil
    var activityLabel: String? = nil
    var expectedLabelClass: String? = nil
    var goldLockCandidate: Bool? = nil

    var exportDictionary: [String: Any] {
        var dict: [String: Any] = [:]
        if let alpha { dict["alpha"] = alpha }
        if let highThreshold { dict["high_threshold"] = highThreshold }
        if let lowThreshold { dict["low_threshold"] = lowThreshold }
        if let minStartWindows { dict["min_start_windows"] = minStartWindows }
        if let minEndWindows { dict["min_end_windows"] = minEndWindows }
        if let testScenario, !testScenario.isEmpty { dict["test_scenario"] = testScenario }
        if let activitySlug, !activitySlug.isEmpty { dict["activity"] = activitySlug }
        if let activityLabel, !activityLabel.isEmpty { dict["activity_label"] = activityLabel }
        if let expectedLabelClass, !expectedLabelClass.isEmpty { dict["expected_label_class"] = expectedLabelClass }
        if let goldLockCandidate { dict["gold_lock_candidate"] = goldLockCandidate }
        return dict
    }

    var reviewMetadataDictionary: [String: String] {
        var dict: [String: String] = [:]
        if let alpha { dict["alpha"] = String(format: "%.4f", alpha) }
        if let highThreshold { dict["high_threshold"] = String(format: "%.4f", highThreshold) }
        if let lowThreshold { dict["low_threshold"] = String(format: "%.4f", lowThreshold) }
        if let minStartWindows { dict["min_start_windows"] = "\(minStartWindows)" }
        if let minEndWindows { dict["min_end_windows"] = "\(minEndWindows)" }
        if let testScenario, !testScenario.isEmpty { dict["test_scenario"] = testScenario }
        if let platform, !platform.isEmpty { dict["platform"] = platform }
        if let modelID, !modelID.isEmpty { dict["model_id"] = modelID }
        if let datasetHash, !datasetHash.isEmpty { dict["dataset_hash"] = datasetHash }
        if let datasetStatus, !datasetStatus.isEmpty { dict["dataset_status"] = datasetStatus }
        if let modelPackageHash, !modelPackageHash.isEmpty { dict["model_package_sha256"] = modelPackageHash }
        if let activitySlug, !activitySlug.isEmpty { dict["activity"] = activitySlug }
        if let activityLabel, !activityLabel.isEmpty { dict["activity_label"] = activityLabel }
        if let expectedLabelClass, !expectedLabelClass.isEmpty { dict["expected_label_class"] = expectedLabelClass }
        if let goldLockCandidate { dict["gold_lock_candidate"] = goldLockCandidate ? "true" : "false" }
        return dict
    }

    var hasValues: Bool {
        !reviewMetadataDictionary.isEmpty
    }
}

enum ReviewLabelResolver {
    static let coverageEpsilon = 0.05

    static func coverageSpans(from predictions: [PredictionSpan], duration: Double) -> [PredictionSpan] {
        let sorted = predictions
            .filter { $0.endTime > $0.startTime }
            .sorted {
                if abs($0.endTime - $1.endTime) > coverageEpsilon {
                    return $0.endTime < $1.endTime
                }
                return $0.startTime < $1.startTime
            }

        let predictionEnd = sorted.map(\.endTime).max() ?? 0
        let safeDuration = max(duration, predictionEnd)
        guard safeDuration > coverageEpsilon else { return [] }
        guard !sorted.isEmpty else {
            return [PredictionSpan(startTime: 0, endTime: safeDuration, isChewing: false)]
        }

        var result: [PredictionSpan] = []
        var cursor = 0.0

        for span in sorted {
            let end = min(max(span.endTime, cursor), safeDuration)
            guard end > cursor + coverageEpsilon else { continue }
            appendCoverageSpan(
                PredictionSpan(
                    startTime: cursor,
                    endTime: end,
                    isChewing: span.isChewing,
                    probability: span.probability,
                    smoothedProbability: span.smoothedProbability
                ),
                to: &result
            )
            cursor = end
        }

        if cursor < safeDuration {
            let lastState = result.last?.isChewing ?? false
            appendCoverageSpan(
                PredictionSpan(startTime: cursor, endTime: safeDuration, isChewing: lastState),
                to: &result
            )
        }

        return result
    }

    static func predictedChewing(at time: Double, predictions: [PredictionSpan], duration: Double? = nil) -> Bool {
        let coverage = coverageSpans(
            from: predictions,
            duration: duration ?? max(time, predictions.map(\.endTime).max() ?? 0)
        )

        for (index, span) in coverage.enumerated() {
            let isLast = index == coverage.count - 1
            if span.contains(time) || (isLast && abs(time - span.endTime) < coverageEpsilon) {
                return span.isChewing
            }
        }

        return coverage.last?.isChewing ?? false
    }

    static func label(for kind: ReviewSegmentKind, at time: Double, predictions: [PredictionSpan], duration: Double? = nil) -> Bool {
        switch kind {
        case .chewing:
            return true
        case .notChewing:
            return false
        case .correct:
            return predictedChewing(at: time, predictions: predictions, duration: duration)
        case .falsePositive:
            return false
        case .falseNegative:
            return true
        }
    }

    static func resolvedLabel(
        at time: Double,
        predictions: [PredictionSpan],
        reviewSegments: [ReviewSegment],
        duration: Double
    ) -> Bool {
        if let segment = reviewSegments.last(where: { $0.contains(time) }) {
            return label(for: segment.kind, at: time, predictions: predictions, duration: duration)
        }

        return predictedChewing(at: time, predictions: predictions, duration: duration)
    }

    static func normalizedCorrectionSegments(
        from segments: [ReviewSegment],
        predictions: [PredictionSpan],
        duration: Double
    ) -> [ReviewSegment] {
        var normalized: [ReviewSegment] = []
        for segment in segments.sorted(by: { $0.startTime < $1.startTime }) {
            let binarySegment: ReviewSegment?
            switch segment.kind {
            case .chewing, .notChewing:
                binarySegment = segment
            case .correct:
                binarySegment = nil
            case .falsePositive:
                binarySegment = ReviewSegment(
                    id: segment.id,
                    startTime: segment.startTime,
                    endTime: segment.endTime,
                    kind: .notChewing,
                    note: segment.note
                )
            case .falseNegative:
                binarySegment = ReviewSegment(
                    id: segment.id,
                    startTime: segment.startTime,
                    endTime: segment.endTime,
                    kind: .chewing,
                    note: segment.note
                )
            }

            guard let binarySegment else { continue }
            normalized = replacingCorrections(normalized, with: binarySegment, duration: duration)
        }

        return normalized
    }

    static func replacingCorrections(
        _ segments: [ReviewSegment],
        with newSegment: ReviewSegment,
        duration: Double
    ) -> [ReviewSegment] {
        guard newSegment.kind.isBinaryCorrection else { return segments }

        let replacement = newSegment.clamped(to: duration)
        guard replacement.endTime - replacement.startTime > coverageEpsilon else {
            return coalescedCorrectionSegments(segments, preferredID: newSegment.id)
        }

        var next: [ReviewSegment] = []
        for segment in segments where segment.kind.isBinaryCorrection {
            let clamped = segment.clamped(to: duration)
            guard clamped.endTime - clamped.startTime > coverageEpsilon else { continue }

            if clamped.endTime <= replacement.startTime + coverageEpsilon ||
                clamped.startTime >= replacement.endTime - coverageEpsilon {
                next.append(clamped)
                continue
            }

            if clamped.startTime < replacement.startTime - coverageEpsilon {
                next.append(ReviewSegment(
                    id: clamped.id,
                    startTime: clamped.startTime,
                    endTime: replacement.startTime,
                    kind: clamped.kind,
                    note: clamped.note
                ))
            }

            if clamped.endTime > replacement.endTime + coverageEpsilon {
                next.append(ReviewSegment(
                    id: clamped.id,
                    startTime: replacement.endTime,
                    endTime: clamped.endTime,
                    kind: clamped.kind,
                    note: clamped.note
                ))
            }
        }

        next.append(replacement)
        return coalescedCorrectionSegments(next, preferredID: replacement.id)
    }

    private static func appendCoverageSpan(_ span: PredictionSpan, to result: inout [PredictionSpan]) {
        guard span.endTime > span.startTime else { return }
        if var last = result.last, last.isChewing == span.isChewing {
            last.endTime = max(last.endTime, span.endTime)
            last.probability = span.probability ?? last.probability
            last.smoothedProbability = span.smoothedProbability ?? last.smoothedProbability
            result[result.count - 1] = last
        } else {
            result.append(span)
        }
    }

    private static func coalescedCorrectionSegments(
        _ segments: [ReviewSegment],
        preferredID: UUID
    ) -> [ReviewSegment] {
        let sorted = segments
            .filter { $0.kind.isBinaryCorrection }
            .filter { $0.endTime - $0.startTime > coverageEpsilon }
            .sorted { $0.startTime < $1.startTime }

        var result: [ReviewSegment] = []
        for segment in sorted {
            if var last = result.last,
               last.kind == segment.kind,
               segment.startTime <= last.endTime + coverageEpsilon {
                last.id = (last.id == preferredID || segment.id == preferredID) ? preferredID : last.id
                last.endTime = max(last.endTime, segment.endTime)
                if last.note.isEmpty {
                    last.note = segment.note
                }
                result[result.count - 1] = last
            } else {
                result.append(segment)
            }
        }
        return result
    }
}
