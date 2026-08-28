//
//  SessionLibraryViewModel.swift
//  ChewSenseLive
//
//  Read-only summaries for Stage 04 field-test bundles.
//

import Foundation
import Combine

private struct LegacyReviewSegmentSummary {
    let endTime: Double
}

struct ReviewSessionSummary: Identifiable, Hashable {
    let id: URL
    let sessionURL: URL
    let title: String
    let startedAt: Date?
    let duration: Double
    let reviewStatus: String
    let predictionCount: Int
    let correctionCount: Int
    let hasVideo: Bool
    let hasCorrections: Bool
    let hasReviewedMotion: Bool
    let hasNotes: Bool
    let exportedAt: Date?
    let activitySlug: String?
    let activityLabel: String?
    let goldLockCandidate: Bool

    var subtitle: String {
        if let startedAt {
            return startedAt.formatted(date: .abbreviated, time: .shortened)
        }
        return sessionURL.lastPathComponent
    }

    var formattedDuration: String {
        Self.formatDuration(duration)
    }

    var predictionStatus: String {
        predictionCount > 0 ? "\(predictionCount) predictions" : "No predictions"
    }

    var correctionStatus: String {
        if hasReviewedMotion {
            return "Reviewed"
        }
        if hasCorrections {
            return "\(correctionCount) corrections"
        }
        return "Unreviewed"
    }

    private static func formatDuration(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds > 0 else { return "--:--" }
        let total = Int(seconds.rounded())
        let minutes = total / 60
        let secs = total % 60
        return String(format: "%02d:%02d", minutes, secs)
    }
}

final class SessionLibraryViewModel: ObservableObject {
    @Published private(set) var sessions: [ReviewSessionSummary] = []
    @Published private(set) var statusMessage: String?

    let rootURL: URL

    init(rootURL: URL? = nil) {
        if let rootURL {
            self.rootURL = rootURL
        } else {
            let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
            self.rootURL = docs.appendingPathComponent("FieldTests", isDirectory: true)
        }
        refresh()
    }

    func refresh() {
        let fileManager = FileManager.default
        guard fileManager.fileExists(atPath: rootURL.path) else {
            sessions = []
            statusMessage = nil
            return
        }

        let folders = (try? fileManager.contentsOfDirectory(
            at: rootURL,
            includingPropertiesForKeys: [.creationDateKey, .contentModificationDateKey],
            options: [.skipsHiddenFiles]
        )) ?? []

        let summaries = folders.compactMap { url -> ReviewSessionSummary? in
            var isDirectory: ObjCBool = false
            guard fileManager.fileExists(atPath: url.path, isDirectory: &isDirectory),
                  isDirectory.boolValue,
                  isFieldTestBundle(url) else {
                return nil
            }
            return makeSummary(for: url)
        }

        sessions = summaries.sorted { lhs, rhs in
            let left = lhs.startedAt ?? fileDate(lhs.sessionURL) ?? .distantPast
            let right = rhs.startedAt ?? fileDate(rhs.sessionURL) ?? .distantPast
            return left > right
        }
        statusMessage = nil
    }

    func summary(for url: URL) -> ReviewSessionSummary? {
        sessions.first { $0.sessionURL == url }
    }

    /// Permanently deletes a session bundle from disk and refreshes the list.
    @discardableResult
    func delete(_ summary: ReviewSessionSummary) -> Bool {
        do {
            try FileManager.default.removeItem(at: summary.sessionURL)
            refresh()
            return true
        } catch {
            statusMessage = "Could not delete session: \(error.localizedDescription)"
            return false
        }
    }

    private func isFieldTestBundle(_ url: URL) -> Bool {
        let fileManager = FileManager.default
        let hasMotion = fileManager.fileExists(atPath: url.appendingPathComponent("motion.csv").path)
        let hasReviewedMotion = fileManager.fileExists(atPath: url.appendingPathComponent("labeled_motion.csv").path)
        return hasMotion || hasReviewedMotion
    }

    private func makeSummary(for url: URL) -> ReviewSessionSummary {
        let metadata = readJSONObject(url.appendingPathComponent("session_metadata.json"))
        let reviewMetadata = readJSONObject(url.appendingPathComponent("review_metadata.json"))
        let sessionID = metadata["session_id"] as? String
        let startedAt = parseDate(metadata["started_at"] as? String) ?? fileDate(url)
        let title = sessionID?.isEmpty == false ? sessionID! : url.lastPathComponent
        let predictionSpans = readPredictionSpans(url.appendingPathComponent("predictions.csv"))
        let reviewSegments = readReviewSegments(url.appendingPathComponent("review_segments.json"))
        let duration = max(
            readMotionDuration(url.appendingPathComponent("motion.csv")),
            predictionSpans.map(\.endTime).max() ?? 0,
            reviewSegments.map(\.endTime).max() ?? 0
        )
        let files = (try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)) ?? []
        let hasVideo = files.contains { ["mov", "mp4", "m4v"].contains($0.pathExtension.lowercased()) }
        let hasReviewedMotion = FileManager.default.fileExists(atPath: url.appendingPathComponent("labeled_motion.csv").path)
        let notes = (try? String(contentsOf: url.appendingPathComponent("session_notes.txt"), encoding: .utf8)) ?? ""
        let exportedAt = parseDate(reviewMetadata["exportedAt"] as? String)
        let reviewStatus = hasReviewedMotion || !reviewSegments.isEmpty
            ? "legacy annotations present"
            : "captured"

        return ReviewSessionSummary(
            id: url,
            sessionURL: url,
            title: title,
            startedAt: startedAt,
            duration: duration,
            reviewStatus: reviewStatus,
            predictionCount: predictionSpans.count,
            correctionCount: reviewSegments.count,
            hasVideo: hasVideo,
            hasCorrections: !reviewSegments.isEmpty,
            hasReviewedMotion: hasReviewedMotion,
            hasNotes: !notes.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            exportedAt: exportedAt,
            activitySlug: (metadata["activity"] as? String).flatMap { $0.isEmpty ? nil : $0 },
            activityLabel: (metadata["activity_label"] as? String).flatMap { $0.isEmpty ? nil : $0 },
            goldLockCandidate: (metadata["gold_lock_candidate"] as? Bool) ?? false
        )
    }

    private func readJSONObject(_ url: URL) -> [String: Any] {
        guard let data = try? Data(contentsOf: url),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return [:]
        }
        return object
    }

    private func readPredictionSpans(_ url: URL) -> [PredictionSpan] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            return readPredictionSpansFromMotion(url.deletingLastPathComponent().appendingPathComponent("motion.csv"))
        }
        let table = SessionCSV.parse(text)
        guard let startIndex = table.header.firstIndex(of: "window_start"),
              let endIndex = table.header.firstIndex(of: "window_end"),
              let stateIndex = table.header.firstIndex(of: "state") else {
            return readPredictionSpansFromMotion(url.deletingLastPathComponent().appendingPathComponent("motion.csv"))
        }
        let base = table.rows.compactMap { row -> Double? in
            guard startIndex < row.count else { return nil }
            return Double(row[startIndex])
        }.first ?? 0

        let spans = table.rows.compactMap { row -> PredictionSpan? in
            guard max(startIndex, endIndex, stateIndex) < row.count,
                  let start = Double(row[startIndex]),
                  let end = Double(row[endIndex]) else {
                return nil
            }
            return PredictionSpan(
                startTime: max(0, start - base),
                endTime: max(0, end - base),
                isChewing: ["chewing", "true", "1"].contains(
                    row[stateIndex]
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                        .lowercased()
                )
            )
        }

        if spans.isEmpty {
            return readPredictionSpansFromMotion(url.deletingLastPathComponent().appendingPathComponent("motion.csv"))
        }
        return spans
    }

    /// Fallback path: synthesize display-only spans from the derived
    /// `predicted_eating` telemetry column. This is never ground truth.
    private func readPredictionSpansFromMotion(_ motionURL: URL) -> [PredictionSpan] {
        guard let text = try? String(contentsOf: motionURL, encoding: .utf8) else { return [] }
        let table = SessionCSV.parse(text)
        guard let timestampIndex = table.header.firstIndex(of: "timestamp"),
              let labelIndex = table.header.firstIndex(of: "predicted_eating") else {
            return []
        }
        let base = table.rows.compactMap { row -> Double? in
            guard timestampIndex < row.count else { return nil }
            return Double(row[timestampIndex].trimmingCharacters(in: .whitespacesAndNewlines))
        }.first ?? 0

        var spans: [PredictionSpan] = []
        var runStart: Double?
        var runLabel: Bool?
        var lastTime: Double = 0
        for row in table.rows {
            guard timestampIndex < row.count, labelIndex < row.count,
                  let timestamp = Double(row[timestampIndex].trimmingCharacters(in: .whitespacesAndNewlines)) else {
                continue
            }
            let relative = max(0, timestamp - base)
            lastTime = relative
            let isChewing: Bool
            switch row[labelIndex].trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
            case "true", "1", "chewing", "yes":
                isChewing = true
            default:
                isChewing = false
            }
            if runLabel == nil {
                runLabel = isChewing
                runStart = relative
                continue
            }
            if isChewing != runLabel {
                if let start = runStart, let label = runLabel {
                    spans.append(PredictionSpan(startTime: start, endTime: relative, isChewing: label))
                }
                runStart = relative
                runLabel = isChewing
            }
        }
        if let start = runStart, let label = runLabel, lastTime > start {
            spans.append(PredictionSpan(startTime: start, endTime: lastTime, isChewing: label))
        }
        return spans
    }

    /// Legacy review files remain visible as migration warnings, but Live no
    /// longer decodes or edits their label semantics.
    private func readReviewSegments(_ url: URL) -> [LegacyReviewSegmentSummary] {
        guard let data = try? Data(contentsOf: url),
              let rows = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            return []
        }
        return rows.compactMap { row in
            guard let end = row["endTime"] as? Double ?? row["end_time"] as? Double else {
                return nil
            }
            return LegacyReviewSegmentSummary(endTime: end)
        }
    }

    private func readMotionDuration(_ url: URL) -> Double {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return 0 }
        let table = SessionCSV.parse(text)
        guard let timestampIndex = table.header.firstIndex(of: "timestamp") else { return 0 }
        let timestamps = table.rows.compactMap { row -> Double? in
            guard timestampIndex < row.count else { return nil }
            return Double(row[timestampIndex].trimmingCharacters(in: .whitespacesAndNewlines))
        }
        guard let first = timestamps.first, let last = timestamps.last else { return 0 }
        return max(0, last - first)
    }

    private func parseDate(_ value: String?) -> Date? {
        guard let value, !value.isEmpty else { return nil }
        return ISO8601DateFormatter().date(from: value)
    }

    private func fileDate(_ url: URL) -> Date? {
        (try? url.resourceValues(forKeys: [.creationDateKey]).creationDate)
            ?? (try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate)
    }
}
