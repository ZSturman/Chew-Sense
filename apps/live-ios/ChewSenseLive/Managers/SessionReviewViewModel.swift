//
//  SessionReviewViewModel.swift
//  ChewSenseLive
//
//  Loads a field-test bundle, manages review labels, and exports reviewed data.
//

import Foundation
import AVFoundation
import AVKit
import UIKit
import Combine

struct ReviewThumbnailFrame: Identifiable {
    let id: Int
    let time: CMTime
    let image: UIImage?
}

struct ReviewCSVTable {
    var header: [String]
    var rows: [[String]]
}

enum ReviewCSV {
    nonisolated static func parse(_ text: String) -> ReviewCSVTable {
        let normalized = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        let lines = normalized
            .split(separator: "\n", omittingEmptySubsequences: false)
            .map(String.init)
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

        guard let first = lines.first else {
            return ReviewCSVTable(header: [], rows: [])
        }

        return ReviewCSVTable(
            header: parseLine(first),
            rows: lines.dropFirst().map(parseLine)
        )
    }

    nonisolated static func serialize(header: [String], rows: [[String]]) -> String {
        ([header] + rows)
            .map { row in row.map(escape).joined(separator: ",") }
            .joined(separator: "\n") + "\n"
    }

    nonisolated static func parseLine(_ line: String) -> [String] {
        var result: [String] = []
        var field = ""
        var inQuotes = false
        var iterator = line.makeIterator()

        while let character = iterator.next() {
            if character == "\"" {
                if inQuotes, let next = iterator.next() {
                    if next == "\"" {
                        field.append("\"")
                    } else {
                        inQuotes = false
                        if next == "," {
                            result.append(field)
                            field = ""
                        } else {
                            field.append(next)
                        }
                    }
                } else {
                    inQuotes.toggle()
                }
            } else if character == ",", !inQuotes {
                result.append(field)
                field = ""
            } else {
                field.append(character)
            }
        }

        result.append(field)
        return result
    }

    nonisolated static func escape(_ value: String) -> String {
        if value.contains(",") || value.contains("\"") || value.contains("\n") {
            return "\"" + value.replacingOccurrences(of: "\"", with: "\"\"") + "\""
        }
        return value
    }
}

private struct SessionReviewMetadata: Codable {
    var unmarkedTimeAcceptedAsCorrect: Bool?
    var updatedAt: String
    var exportedAt: String?
    var recordingConfiguration: [String: String]?
    var modelID: String?
    var datasetHash: String?
}

enum SessionReviewExportError: LocalizedError {
    case unconfirmedUnmarkedTime
    case openCorrection
    case missingMotionCSV
    case invalidMotionCSV

    var errorDescription: String? {
        switch self {
        case .unconfirmedUnmarkedTime:
            return "Confirm that unmarked time should be treated as correct before sharing."
        case .openCorrection:
            return "End the current correction before sharing."
        case .missingMotionCSV:
            return "motion.csv is missing from the session bundle."
        case .invalidMotionCSV:
            return "motion.csv does not contain the required timestamp and motion columns."
        }
    }
}

final class SessionReviewViewModel: ObservableObject {
    @Published var player: AVPlayer
    @Published var currentTime: Double = 0
    @Published var videoDuration: Double = 0
    @Published var thumbnails: [ReviewThumbnailFrame] = []
    @Published var predictionSpans: [PredictionSpan] = []
    @Published var reviewSegments: [ReviewSegment] = []
    @Published var selectedSegmentID: UUID?
    @Published var activeCorrectionSegmentID: UUID?
    @Published var notes = ""
    @Published var statusMessage: String?
    @Published var recordingConfiguration: ReviewRecordingConfiguration?

    // Unified bite/stroke review state. The reviewer only ever marks
    // strokes by hand; bites are auto-derived from stroke clusters using
    // `RuntimeConfig.minGapSec` / `minBiteDurationSec`. Auto-derived
    // bites that overlap chewing time count as implicit chewing GT.
    @Published var strokes: [ReviewStroke] = []
    @Published var sessionNote: String = ""
    @Published var facePresence: FacePresenceTrack?

    let sessionURL: URL
    let videoURL: URL?
    let framesVisible = 15

    private var timeObserver: Any?
    private let asset: AVURLAsset?
    private let imageGenerator: AVAssetImageGenerator?
    private var motionBaseTimestamp: Double?
    private var motionEndRelativeTime: Double = 0
    private var rawPredictionSpans: [PredictionSpan] = []
    private var predictionRowsWereLoaded = false
    private let minimumSegmentLength = 0.05
    private var pendingReviewSave: DispatchWorkItem?
    private var pendingNotesSave: DispatchWorkItem?

    init(sessionURL: URL) {
        self.sessionURL = sessionURL
        let files = (try? FileManager.default.contentsOfDirectory(at: sessionURL, includingPropertiesForKeys: nil)) ?? []
        let movie = files.first { ["mov", "mp4", "m4v"].contains($0.pathExtension.lowercased()) }
        self.videoURL = movie

        if let movie {
            let asset = AVURLAsset(url: movie)
            self.asset = asset
            self.imageGenerator = AVAssetImageGenerator(asset: asset)
            self.imageGenerator?.appliesPreferredTrackTransform = true
            self.imageGenerator?.maximumSize = CGSize(width: 160, height: 90)
            self.player = AVPlayer(playerItem: AVPlayerItem(asset: asset))
        } else {
            self.asset = nil
            self.imageGenerator = nil
            self.player = AVPlayer()
        }

        loadMotionTimebase()
        loadRecordingConfiguration()
        loadPredictions()
        loadReviewSegments()
        loadReviewMetadata()
        loadNotes()
        loadBiteReviewDocument()
        loadFacePresence()
        setupTimeObserver()

        Task {
            await loadVideoProperties()
        }
    }

    deinit {
        pendingReviewSave?.cancel()
        pendingNotesSave?.cancel()
        player.pause()
        player.replaceCurrentItem(with: nil)
        if let timeObserver {
            player.removeTimeObserver(timeObserver)
        }
    }

    func flushPendingSaves() {
        pendingReviewSave?.cancel()
        pendingNotesSave?.cancel()
        saveReviewArtifacts()
        saveNotes()
    }

    var selectedSegment: ReviewSegment? {
        reviewSegments.first { $0.id == selectedSegmentID }
    }

    var activeCorrectionSegment: ReviewSegment? {
        guard let activeCorrectionSegmentID else { return nil }
        return reviewSegments.first { $0.id == activeCorrectionSegmentID }
    }

    var hasActiveCorrection: Bool {
        activeCorrectionSegmentID != nil
    }

    var timelineDuration: Double {
        max(videoDuration, motionEndRelativeTime, predictionSpans.map(\.endTime).max() ?? 0)
    }

    var canExport: Bool {
        !hasActiveCorrection
    }

    var hasCorrections: Bool {
        !finalizedReviewSegments.isEmpty || hasManualOrConfirmedStrokes
    }

    /// Strokes the reviewer placed or explicitly confirmed (i.e. excluding
    /// pure auto/heuristic seeds that haven't been touched). Used to
    /// decide whether the export carries real human signal.
    var hasManualOrConfirmedStrokes: Bool {
        strokes.contains { $0.source == .manual || $0.source == .confirmed }
    }

    /// Bites derived from the stroke list using the same min-gap /
    /// min-duration rules `pipeline.events.aggregate_bites` enforces.
    /// Recomputed on every access so the reviewer sees live feedback as
    /// they add/remove strokes; saved to `bite_review.json` at export time.
    var derivedBites: [ReviewBiteBoundary] {
        Self.deriveBites(
            from: strokes,
            duration: timelineDuration,
            minGapSec: RuntimeConfig.minGapSec,
            minBiteDurationSec: RuntimeConfig.minBiteDurationSec
        )
    }

    static func deriveBites(
        from strokes: [ReviewStroke],
        duration: Double,
        minGapSec: Double,
        minBiteDurationSec: Double
    ) -> [ReviewBiteBoundary] {
        let sorted = strokes.sorted { $0.timestamp < $1.timestamp }
        guard !sorted.isEmpty else { return [] }

        // Pad each stroke by half the min-gap so isolated strokes still
        // produce a non-zero-length bite proposal.
        let halfGap = max(minGapSec / 2.0, 0.05)
        var clusters: [(start: Double, end: Double, count: Int)] = []
        for stroke in sorted {
            let s = max(0, stroke.timestamp - halfGap)
            let e = stroke.timestamp + halfGap
            if var last = clusters.last, s - last.end <= minGapSec {
                last.end = max(last.end, e)
                last.count += 1
                clusters[clusters.count - 1] = last
            } else {
                clusters.append((start: s, end: e, count: 1))
            }
        }

        let safeDuration = max(duration, 0)
        return clusters.compactMap { cluster in
            let end = safeDuration > 0 ? min(cluster.end, safeDuration) : cluster.end
            let start = max(0, cluster.start)
            let dur = end - start
            // A single stroke is a valid bite proposal even below the
            // min-duration floor — the reviewer can delete it if spurious.
            guard end > start, cluster.count > 1 || dur >= 0.05 else { return nil }
            // Drop multi-stroke clusters that fall under the min-bite floor.
            if cluster.count > 1, dur < minBiteDurationSec { return nil }
            return ReviewBiteBoundary(
                startTime: start,
                endTime: end,
                source: .auto,
                note: ""
            )
        }
    }

    var currentPredictionIsChewing: Bool {
        ReviewLabelResolver.predictedChewing(at: currentTime, predictions: predictionSpans, duration: timelineDuration)
    }

    var currentPredictionTitle: String {
        currentPredictionIsChewing ? "Chewing" : "Not Chewing"
    }

    var currentPredictionDetail: String {
        if !predictionRowsWereLoaded {
            return "No model rows found; using Not Chewing for the full session"
        }

        guard let span = predictionSpan(at: currentTime) else {
            return "Not Chewing"
        }

        var details = [String(format: "%@-%@", formatTime(span.startTime), formatTime(span.endTime))]
        if let probability = span.probability {
            details.append(String(format: "prob %.2f", probability))
        }
        if let smoothedProbability = span.smoothedProbability {
            details.append(String(format: "smooth %.2f", smoothedProbability))
        }
        return details.joined(separator: "  ")
    }

    var currentCorrectionKind: ReviewSegmentKind {
        currentPredictionIsChewing ? .notChewing : .chewing
    }

    var correctionButtonTitle: String {
        if let activeCorrectionSegment {
            return "End \(activeCorrectionSegment.kind.title)"
        }
        return currentCorrectionKind.title
    }

    var correctionButtonSystemImage: String {
        hasActiveCorrection ? "stop.circle.fill" : "plus.circle.fill"
    }

    var formattedCurrentTime: String {
        formatTime(currentTime)
    }

    var formattedDuration: String {
        formatTime(timelineDuration)
    }

    var faceMissingAtPlayhead: Bool {
        facePresence?.isFaceMissing(at: currentTime) ?? false
    }

    var strokeCountDescriptor: String {
        let manual = strokes.filter { $0.source == .manual }.count
        let confirmed = strokes.filter { $0.source == .confirmed }.count
        let auto = strokes.count - manual - confirmed
        return "\(strokes.count) strokes (\(manual) manual, \(confirmed) confirmed, \(auto) seed)"
    }

    var resolvedReviewSpans: [ResolvedReviewSpan] {
        finalizedReviewSegments
            .sorted { $0.startTime < $1.startTime }
            .map { segment in
                ResolvedReviewSpan(
                    sourceSegmentID: segment.id,
                    startTime: segment.startTime,
                    endTime: segment.endTime,
                    isChewing: segment.kind == .chewing,
                    sourceKind: segment.kind
                )
            }
    }

    func seek(to time: Double) {
        let clamped = min(max(time, 0), max(timelineDuration, 0))
        player.seek(to: CMTime(seconds: clamped, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
        currentTime = clamped
    }

    func addSegment(kind: ReviewSegmentKind) {
        let duration = max(timelineDuration, 0)
        guard duration > 0 else { return }

        let defaultLength = min(2.0, duration)
        let start = min(max(currentTime, 0), max(duration - defaultLength, 0))
        let segment = ReviewSegment(
            startTime: start,
            endTime: min(start + defaultLength, duration),
            kind: kind
        )

        applyCorrectionSegment(segment)
        selectedSegmentID = segment.id
        scheduleReviewArtifactSave()
    }

    func addSegment(kind: ReviewSegmentKind, startTime: Double, endTime: Double) {
        let duration = max(timelineDuration, 0)
        guard duration > 0 else { return }

        let bounds = normalizedBounds(startTime: startTime, endTime: endTime)
        let segment = ReviewSegment(
            startTime: bounds.startTime,
            endTime: bounds.endTime,
            kind: kind
        )

        applyCorrectionSegment(segment)
        selectedSegmentID = segment.id
        scheduleReviewArtifactSave()
    }

    func beginCorrection(kind: ReviewSegmentKind) {
        guard kind.isBinaryCorrection else { return }
        guard activeCorrectionSegmentID == nil else {
            let activeKind = activeCorrectionSegment?.kind
            finishActiveCorrection(at: currentTime)
            if activeKind != kind {
                startCorrection(at: currentTime, kind: kind)
            }
            return
        }
        startCorrection(at: currentTime, kind: kind)
    }

    func toggleCorrection() {
        if activeCorrectionSegmentID != nil {
            finishActiveCorrection(at: currentTime)
        } else {
            startCorrection(at: currentTime, kind: currentCorrectionKind)
        }
    }

    func displayTimes(for segment: ReviewSegment) -> (startTime: Double, endTime: Double) {
        guard segment.id == activeCorrectionSegmentID else {
            return (segment.startTime, segment.endTime)
        }

        let start = min(segment.startTime, currentTime)
        let end = max(segment.startTime, currentTime)
        if end - start >= minimumSegmentLength {
            return (start, end)
        }

        let duration = max(timelineDuration, 0)
        return normalizedBounds(startTime: start, endTime: min(start + minimumSegmentLength, duration))
    }

    func updateSegment(id: UUID, startTime: Double, endTime: Double) {
        guard let index = reviewSegments.firstIndex(where: { $0.id == id }) else { return }
        let bounds = normalizedBounds(startTime: startTime, endTime: endTime)

        var updated = reviewSegments[index]
        updated.startTime = bounds.startTime
        updated.endTime = bounds.endTime
        reviewSegments.remove(at: index)
        applyCorrectionSegment(updated)
        selectedSegmentID = updated.id
        scheduleReviewArtifactSave()
    }

    func moveSegment(id: UUID, delta: Double) {
        guard let segment = reviewSegments.first(where: { $0.id == id }) else { return }
        let length = segment.endTime - segment.startTime
        let duration = max(timelineDuration, 0)
        let start = min(max(segment.startTime + delta, 0), max(duration - length, 0))
        updateSegment(id: id, startTime: start, endTime: start + length)
    }

    func deleteSelectedSegment() {
        guard let selectedSegmentID else { return }
        reviewSegments.removeAll { $0.id == selectedSegmentID }
        if activeCorrectionSegmentID == selectedSegmentID {
            activeCorrectionSegmentID = nil
        }
        self.selectedSegmentID = nil
        scheduleReviewArtifactSave()
    }

    // MARK: - Stroke editing (unified review)

    /// Mark a chew stroke at the current playhead. If a nearby auto/heuristic
    /// seed already exists, promote it to `.confirmed` instead of adding a
    /// duplicate; otherwise insert a new `.manual` stroke.
    ///
    /// No-ops inside face-missing intervals, which by definition can't be
    /// visually verified — the reviewer must scrub to a face-visible moment.
    func markStrokeAtPlayhead() {
        let t = currentTime
        if let face = facePresence, face.isFaceMissing(at: t) {
            statusMessage = "Face not in frame at this time \u{2014} scrub to a face-visible moment first"
            return
        }
        let tolerance = 0.05
        if let idx = strokes.firstIndex(where: { abs($0.timestamp - t) < tolerance }) {
            var s = strokes[idx]
            switch s.source {
            case .auto, .heuristic, .model:
                s.source = .confirmed
                strokes[idx] = s
                statusMessage = String(format: "Confirmed stroke @ %.2fs", t)
            case .confirmed, .manual:
                // Toggle: a second tap on the same stroke removes it.
                strokes.remove(at: idx)
                statusMessage = String(format: "Removed stroke @ %.2fs", t)
            }
        } else {
            strokes.append(ReviewStroke(timestamp: t, source: .manual))
            strokes.sort { $0.timestamp < $1.timestamp }
            statusMessage = String(format: "Marked stroke @ %.2fs", t)
        }
        scheduleReviewArtifactSave()
    }

    func deleteStroke(_ stroke: ReviewStroke) {
        strokes.removeAll { $0.id == stroke.id }
        scheduleReviewArtifactSave()
    }

    func confirmStroke(_ stroke: ReviewStroke) {
        guard let idx = strokes.firstIndex(where: { $0.id == stroke.id }) else { return }
        var s = strokes[idx]
        if s.source == .auto || s.source == .heuristic || s.source == .model {
            s.source = .confirmed
            strokes[idx] = s
            scheduleReviewArtifactSave()
        }
    }

    func saveNotes() {
        pendingNotesSave?.cancel()
        try? notes.write(
            to: sessionURL.appendingPathComponent("session_notes.txt"),
            atomically: true,
            encoding: .utf8
        )
    }

    func scheduleNotesSave() {
        pendingNotesSave?.cancel()
        let workItem = DispatchWorkItem { [weak self] in
            self?.saveNotes()
        }
        pendingNotesSave = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: workItem)
    }

    @discardableResult
    func prepareExport() throws -> URL {
        guard !hasActiveCorrection else {
            throw SessionReviewExportError.openCorrection
        }

        saveNotes()
        try ensureConfigJSONExists()
        try writeLabeledMotionCSV()
        try writeReviewFeedbackCSV()
        try writeReviewArtifacts(exported: true)
        return sessionURL
    }

    private var finalizedReviewSegments: [ReviewSegment] {
        reviewSegments
            .filter { $0.id != activeCorrectionSegmentID }
            .filter { $0.kind.isBinaryCorrection }
            .filter { $0.endTime - $0.startTime >= minimumSegmentLength }
    }

    private func startCorrection(at time: Double, kind: ReviewSegmentKind) {
        let duration = max(timelineDuration, 0)
        guard duration > 0, kind.isBinaryCorrection else { return }

        let start = min(max(time, 0), duration)
        let segment = ReviewSegment(startTime: start, endTime: start, kind: kind)
        reviewSegments.append(segment)
        activeCorrectionSegmentID = segment.id
        selectedSegmentID = segment.id
        statusMessage = nil
    }

    private func finishActiveCorrection(at time: Double) {
        guard let activeCorrectionSegmentID,
              let index = reviewSegments.firstIndex(where: { $0.id == activeCorrectionSegmentID }) else {
            self.activeCorrectionSegmentID = nil
            return
        }

        let segment = reviewSegments[index]
        let bounds = normalizedBounds(startTime: segment.startTime, endTime: time)
        var updated = reviewSegments[index]
        updated.startTime = bounds.startTime
        updated.endTime = bounds.endTime
        reviewSegments.remove(at: index)
        self.activeCorrectionSegmentID = nil
        applyCorrectionSegment(updated)
        selectedSegmentID = updated.id
        scheduleReviewArtifactSave()
    }

    private func predictionSpan(at time: Double) -> PredictionSpan? {
        predictionSpans.first { $0.contains(time) }
    }

    private func normalizedBounds(startTime: Double, endTime: Double) -> (startTime: Double, endTime: Double) {
        let duration = max(timelineDuration, 0)
        guard duration > 0 else { return (0, 0) }

        var start = min(max(min(startTime, endTime), 0), duration)
        var end = min(max(max(startTime, endTime), 0), duration)

        if end - start < minimumSegmentLength {
            if start + minimumSegmentLength <= duration {
                end = start + minimumSegmentLength
            } else {
                start = max(0, duration - minimumSegmentLength)
                end = duration
            }
        }

        return (start, end)
    }

    private func applyCorrectionSegment(_ segment: ReviewSegment) {
        reviewSegments = ReviewLabelResolver.replacingCorrections(
            reviewSegments,
            with: segment,
            duration: timelineDuration
        )
    }

    private func setupTimeObserver() {
        let interval = CMTime(seconds: 0.05, preferredTimescale: 600)
        timeObserver = player.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            self?.currentTime = CMTimeGetSeconds(time)
        }
    }

    private func loadVideoProperties() async {
        guard let asset else { return }

        do {
            let duration = try await asset.load(.duration)
            await MainActor.run {
                self.videoDuration = CMTimeGetSeconds(duration)
                self.refreshPredictionCoverage()
                self.reviewSegments = ReviewLabelResolver.normalizedCorrectionSegments(
                    from: self.reviewSegments,
                    predictions: self.predictionSpans,
                    duration: self.timelineDuration
                )
                self.updateThumbnails()
                // Nudge the player to render the first frame instead of
                // showing a black surface until the user hits play.
                self.player.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero)
            }
        } catch {
            await MainActor.run {
                self.statusMessage = "Unable to load video duration."
            }
        }
    }

    private func updateThumbnails() {
        guard let imageGenerator, timelineDuration > 0 else {
            thumbnails = []
            return
        }

        let count = framesVisible
        let sampleTimes = (0..<count).map { index -> Double in
            count == 1 ? timelineDuration / 2 : Double(index) * timelineDuration / Double(count - 1)
        }

        var nextFrames: [ReviewThumbnailFrame?] = Array(repeating: nil, count: count)
        let group = DispatchGroup()

        for (index, seconds) in sampleTimes.enumerated() {
            let time = CMTime(seconds: seconds, preferredTimescale: 600)
            group.enter()
            imageGenerator.generateCGImageAsynchronously(for: time) { cgImage, _, _ in
                let image = cgImage.map { UIImage(cgImage: $0) }
                DispatchQueue.main.async {
                    nextFrames[index] = ReviewThumbnailFrame(id: index, time: time, image: image)
                    group.leave()
                }
            }
        }

        group.notify(queue: .main) {
            self.thumbnails = nextFrames.compactMap { $0 }
        }
    }

    private func loadMotionTimebase() {
        let motionURL = sessionURL.appendingPathComponent("motion.csv")
        guard let text = try? String(contentsOf: motionURL, encoding: .utf8) else { return }
        let table = ReviewCSV.parse(text)
        guard let timestampIndex = table.header.firstIndex(of: "timestamp") else { return }

        let timestamps = table.rows.compactMap { row -> Double? in
            guard timestampIndex < row.count else { return nil }
            return Double(row[timestampIndex].trimmingCharacters(in: .whitespacesAndNewlines))
        }

        guard let first = timestamps.first else { return }
        motionBaseTimestamp = first
        if let last = timestamps.last {
            motionEndRelativeTime = max(0, last - first)
        }
    }

    private func loadPredictions() {
        let predictionsURL = sessionURL.appendingPathComponent("predictions.csv")
        guard let text = try? String(contentsOf: predictionsURL, encoding: .utf8) else {
            loadPredictionsFromMotionLabels()
            return
        }
        let table = ReviewCSV.parse(text)

        guard let startIndex = table.header.firstIndex(of: "window_start"),
              let endIndex = table.header.firstIndex(of: "window_end") else {
            loadPredictionsFromMotionLabels()
            return
        }

        let stateIndex = table.header.firstIndex(of: "state")
        let probabilityIndex = table.header.firstIndex(of: "probability")
        let smoothedIndex = table.header.firstIndex(of: "smoothed")

        let starts = table.rows.compactMap { row -> Double? in
            guard startIndex < row.count else { return nil }
            return Double(row[startIndex])
        }
        let base = predictionTimeBase(from: starts)

        let rawSpans = table.rows.compactMap { row -> PredictionSpan? in
            guard max(startIndex, endIndex) < row.count,
                  let start = Double(row[startIndex]),
                  let end = Double(row[endIndex]) else {
                return nil
            }

            let state = stateIndex.flatMap { index -> String? in
                guard index < row.count else { return nil }
                return row[index]
            }
            let probability = probabilityIndex.flatMap { index -> Double? in
                guard index < row.count else { return nil }
                return Double(row[index])
            }
            let smoothed = smoothedIndex.flatMap { index -> Double? in
                guard index < row.count else { return nil }
                return Double(row[index])
            }
            let isChewing = parsePredictionState(state) ?? inferPredictionState(
                probability: probability,
                smoothed: smoothed
            )

            return PredictionSpan(
                startTime: max(0, start - base),
                endTime: max(0, end - base),
                isChewing: isChewing,
                probability: probability,
                smoothedProbability: smoothed
            )
        }

        if rawSpans.isEmpty {
            loadPredictionsFromMotionLabels()
            return
        }

        rawPredictionSpans = rawSpans
        predictionRowsWereLoaded = true
        refreshPredictionCoverage()
    }

    /// Fallback path used when `predictions.csv` is missing or empty. Uses
    /// the per-row `label` column we now write into `motion.csv` at session
    /// finalize time to reconstruct prediction spans for the playback view.
    private func loadPredictionsFromMotionLabels() {
        let motionURL = sessionURL.appendingPathComponent("motion.csv")
        guard let text = try? String(contentsOf: motionURL, encoding: .utf8) else {
            rawPredictionSpans = []
            predictionRowsWereLoaded = false
            refreshPredictionCoverage()
            return
        }

        let table = ReviewCSV.parse(text)
        guard let timestampIndex = table.header.firstIndex(of: "timestamp"),
              let labelIndex = table.header.firstIndex(of: "label") else {
            rawPredictionSpans = []
            predictionRowsWereLoaded = false
            refreshPredictionCoverage()
            return
        }

        let base = motionBaseTimestamp ?? table.rows.compactMap { row -> Double? in
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

            let labelString = row[labelIndex]
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .lowercased()
            let isChewing: Bool
            switch labelString {
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
                    spans.append(PredictionSpan(
                        startTime: start,
                        endTime: relative,
                        isChewing: label
                    ))
                }
                runStart = relative
                runLabel = isChewing
            }
        }

        if let start = runStart, let label = runLabel, lastTime > start {
            spans.append(PredictionSpan(
                startTime: start,
                endTime: lastTime,
                isChewing: label
            ))
        }

        rawPredictionSpans = spans
        predictionRowsWereLoaded = !spans.isEmpty
        refreshPredictionCoverage()
    }

    private func refreshPredictionCoverage() {
        let duration = max(videoDuration, motionEndRelativeTime, rawPredictionSpans.map(\.endTime).max() ?? 0)
        predictionSpans = ReviewLabelResolver.coverageSpans(from: rawPredictionSpans, duration: duration)
    }

    private func predictionTimeBase(from starts: [Double]) -> Double {
        guard let firstStart = starts.first else { return motionBaseTimestamp ?? 0 }

        if let motionBaseTimestamp {
            let alignmentTolerance = max(10, motionEndRelativeTime + 10)
            return abs(firstStart - motionBaseTimestamp) <= alignmentTolerance ? motionBaseTimestamp : 0
        }

        if firstStart > max(videoDuration + 60, 600) {
            return firstStart
        }
        return 0
    }

    private func parsePredictionState(_ value: String?) -> Bool? {
        guard let value else { return nil }
        let normalized = value
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
            .replacingOccurrences(of: "-", with: "_")
            .replacingOccurrences(of: " ", with: "_")

        switch normalized {
        case "chewing", "chew", "true", "1", "yes", "active", "detected":
            return true
        case "idle", "not_chewing", "non_chewing", "no_chew", "false", "0", "no", "inactive", "none":
            return false
        default:
            return nil
        }
    }

    private func inferPredictionState(probability: Double?, smoothed: Double?) -> Bool {
        let value = smoothed ?? probability ?? 0
        let threshold = recordingConfiguration?.highThreshold ?? 0.5
        return value >= threshold
    }

    private func coalesce(_ spans: [PredictionSpan]) -> [PredictionSpan] {
        let sorted = spans
            .filter { $0.endTime > $0.startTime }
            .sorted { $0.startTime < $1.startTime }

        var result: [PredictionSpan] = []
        for span in sorted {
            if var last = result.last,
               last.isChewing == span.isChewing,
               span.startTime <= last.endTime + 0.15 {
                last.endTime = max(last.endTime, span.endTime)
                last.probability = span.probability ?? last.probability
                last.smoothedProbability = span.smoothedProbability ?? last.smoothedProbability
                result[result.count - 1] = last
            } else {
                result.append(span)
            }
        }
        return result
    }

    private func loadRecordingConfiguration() {
        let configDict = jsonDictionary(at: sessionURL.appendingPathComponent("config.json"))
        let metadataDict = jsonDictionary(at: sessionURL.appendingPathComponent("session_metadata.json"))
        let runtimeDict = metadataDict["runtime_config"] as? [String: Any]

        var configuration = ReviewRecordingConfiguration(
            alpha: doubleValue(configDict["alpha"]) ?? doubleValue(runtimeDict?["alpha"]),
            highThreshold: doubleValue(configDict["high_threshold"]) ?? doubleValue(runtimeDict?["high_threshold"]),
            lowThreshold: doubleValue(configDict["low_threshold"]) ?? doubleValue(runtimeDict?["low_threshold"]),
            minStartWindows: intValue(configDict["min_start_windows"]) ?? intValue(runtimeDict?["min_start_windows"]),
            minEndWindows: intValue(configDict["min_end_windows"]) ?? intValue(runtimeDict?["min_end_windows"]),
            testScenario: stringValue(configDict["test_scenario"]) ?? stringValue(metadataDict["test_scenario"]),
            platform: stringValue(metadataDict["platform"]),
            modelID: stringValue(metadataDict["model_id"]),
            datasetHash: stringValue(metadataDict["dataset_hash"]),
            datasetStatus: stringValue(metadataDict["dataset_status"]),
            modelPackageHash: stringValue(metadataDict["model_package_sha256"]),
            activitySlug: stringValue(metadataDict["activity"]) ?? stringValue(configDict["activity"]),
            activityLabel: stringValue(metadataDict["activity_label"]) ?? stringValue(configDict["activity_label"]),
            expectedLabelClass: stringValue(metadataDict["expected_label_class"]) ?? stringValue(configDict["expected_label_class"]),
            goldLockCandidate: boolValue(metadataDict["gold_lock_candidate"]) ?? boolValue(configDict["gold_lock_candidate"])
        )

        if !configuration.hasValues {
            configuration = ReviewRecordingConfiguration()
        }
        recordingConfiguration = configuration.hasValues ? configuration : nil
    }

    private func jsonDictionary(at url: URL) -> [String: Any] {
        guard let data = try? Data(contentsOf: url),
              let object = try? JSONSerialization.jsonObject(with: data),
              let dictionary = object as? [String: Any] else {
            return [:]
        }
        return dictionary
    }

    private func doubleValue(_ value: Any?) -> Double? {
        switch value {
        case let value as Double:
            return value
        case let value as Int:
            return Double(value)
        case let value as NSNumber:
            return value.doubleValue
        case let value as String:
            return Double(value)
        default:
            return nil
        }
    }

    private func intValue(_ value: Any?) -> Int? {
        switch value {
        case let value as Int:
            return value
        case let value as Double:
            return Int(value)
        case let value as NSNumber:
            return value.intValue
        case let value as String:
            return Int(value)
        default:
            return nil
        }
    }

    private func stringValue(_ value: Any?) -> String? {
        switch value {
        case let value as String:
            return value
        case let value as NSNumber:
            return value.stringValue
        default:
            return nil
        }
    }

    private func boolValue(_ value: Any?) -> Bool? {
        switch value {
        case let value as Bool:
            return value
        case let value as NSNumber:
            return value.boolValue
        case let value as String:
            switch value.lowercased() {
            case "true", "yes", "1": return true
            case "false", "no", "0": return false
            default: return nil
            }
        default:
            return nil
        }
    }

    private func loadReviewSegments() {
        let jsonURL = sessionURL.appendingPathComponent("review_segments.json")
        if let data = try? Data(contentsOf: jsonURL),
           let decoded = try? JSONDecoder().decode([ReviewSegment].self, from: data) {
            reviewSegments = ReviewLabelResolver.normalizedCorrectionSegments(
                from: decoded,
                predictions: predictionSpans,
                duration: timelineDuration
            )
        }
    }

    private func loadReviewMetadata() {
        let metadataURL = sessionURL.appendingPathComponent("review_metadata.json")
        guard let data = try? Data(contentsOf: metadataURL),
              (try? JSONDecoder().decode(SessionReviewMetadata.self, from: data)) != nil else {
            return
        }
    }

    private func loadNotes() {
        let notesURL = sessionURL.appendingPathComponent("session_notes.txt")
        notes = (try? String(contentsOf: notesURL, encoding: .utf8)) ?? ""
    }

    // MARK: - Bite review (unified) persistence

    /// Load `bite_review.json` if it exists, otherwise seed strokes from
    /// the runtime detector's `chew_events.csv` and the visual labeler's
    /// `stroke_seeds.json`. Bite boundaries are *not* loaded as state \u2014
    /// they are derived from strokes via `derivedBites`.
    private func loadBiteReviewDocument() {
        let url = sessionURL.appendingPathComponent("bite_review.json")
        if let data = try? Data(contentsOf: url),
           let doc = try? JSONDecoder().decode(BiteReviewDocument.self, from: data) {
            self.strokes = doc.strokes
            self.sessionNote = doc.sessionNote
            return
        }
        seedStrokesFromArtifacts()
    }

    private func seedStrokesFromArtifacts() {
        var seeded: [ReviewStroke] = []

        // Runtime motion-detector strokes.
        let chewCSV = sessionURL.appendingPathComponent("chew_events.csv")
        if let text = try? String(contentsOf: chewCSV) {
            let lines = text.split(whereSeparator: \.isNewline)
            for line in lines.dropFirst() {
                let parts = line.split(separator: ",")
                guard let t = parts.first.flatMap({ Double($0) }) else { continue }
                seeded.append(ReviewStroke(timestamp: t, source: .auto))
            }
        }

        // Visual-labeler stroke seeds.
        let seedsURL = sessionURL.appendingPathComponent("stroke_seeds.json")
        if let data = try? Data(contentsOf: seedsURL),
           let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let arr = payload["strokes"] as? [[String: Any]] {
            for item in arr {
                guard let t = item["timestamp"] as? Double else { continue }
                if seeded.contains(where: { abs($0.timestamp - t) < 0.05 }) { continue }
                seeded.append(ReviewStroke(timestamp: t, source: .auto))
            }
        }

        seeded.sort { $0.timestamp < $1.timestamp }
        self.strokes = seeded
    }

    private func loadFacePresence() {
        self.facePresence = FacePresenceTrack.load(from: sessionURL)
    }

    /// Write the unified `bite_review.json` document. Bites are derived
    /// from strokes at write time so the persisted file is always
    /// self-consistent for `pipeline/bite_metrics.py`.
    private func writeBiteReviewJSON() throws {
        let doc = BiteReviewDocument(
            bites: derivedBites,
            strokes: strokes.sorted { $0.timestamp < $1.timestamp },
            sessionNote: sessionNote
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(doc)
        try data.write(to: sessionURL.appendingPathComponent("bite_review.json"), options: .atomic)
    }

    private func writeReviewArtifacts(exported: Bool = false) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let sortedSegments = finalizedReviewSegments.sorted { $0.startTime < $1.startTime }
        let data = try encoder.encode(sortedSegments)
        try data.write(to: sessionURL.appendingPathComponent("review_segments.json"), options: .atomic)

        let csvRows = sortedSegments.map { segment in
            [
                String(format: "%.6f", segment.startTime),
                String(format: "%.6f", segment.endTime),
                segment.kind.rawValue,
                segment.note
            ]
        }
        let csv = ReviewCSV.serialize(header: ["start_time", "end_time", "label", "note"], rows: csvRows)
        try csv.write(
            to: sessionURL.appendingPathComponent("review_segments.csv"),
            atomically: true,
            encoding: .utf8
        )

        var exportedAt: String?
        if exported {
            exportedAt = ISO8601DateFormatter().string(from: Date())
        } else if let data = try? Data(contentsOf: sessionURL.appendingPathComponent("review_metadata.json")),
                  let existing = try? JSONDecoder().decode(SessionReviewMetadata.self, from: data) {
            exportedAt = existing.exportedAt
        }

        let metadata = SessionReviewMetadata(
            unmarkedTimeAcceptedAsCorrect: true,
            updatedAt: ISO8601DateFormatter().string(from: Date()),
            exportedAt: exportedAt,
            recordingConfiguration: recordingConfiguration?.reviewMetadataDictionary,
            modelID: recordingConfiguration?.modelID,
            datasetHash: recordingConfiguration?.datasetHash
        )
        let metadataData = try encoder.encode(metadata)
        try metadataData.write(to: sessionURL.appendingPathComponent("review_metadata.json"), options: .atomic)

        // Unified bite/stroke ground truth — derived from strokes.
        try writeBiteReviewJSON()
    }

    private func ensureConfigJSONExists() throws {
        let configURL = sessionURL.appendingPathComponent("config.json")
        guard !FileManager.default.fileExists(atPath: configURL.path),
              let recordingConfiguration,
              !recordingConfiguration.exportDictionary.isEmpty else {
            return
        }

        let data = try JSONSerialization.data(
            withJSONObject: recordingConfiguration.exportDictionary,
            options: [.prettyPrinted, .sortedKeys]
        )
        try data.write(to: configURL, options: .atomic)
    }

    private func saveReviewArtifacts(exported: Bool = false) {
        pendingReviewSave?.cancel()
        do {
            try writeReviewArtifacts(exported: exported)
            statusMessage = nil
        } catch {
            statusMessage = "Unable to save review: \(error.localizedDescription)"
        }
    }

    private func scheduleReviewArtifactSave() {
        pendingReviewSave?.cancel()
        let workItem = DispatchWorkItem { [weak self] in
            self?.saveReviewArtifacts()
        }
        pendingReviewSave = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: workItem)
    }

    private func writeLabeledMotionCSV() throws {
        let motionURL = sessionURL.appendingPathComponent("motion.csv")
        guard let text = try? String(contentsOf: motionURL, encoding: .utf8) else {
            throw SessionReviewExportError.missingMotionCSV
        }

        var table = ReviewCSV.parse(text)
        let requiredColumns = ["timestamp", "ax", "ay", "az", "gx", "gy", "gz"]

        // Recovery for sessions captured before the FileHandle header-offset
        // bug was fixed: those motion.csv files have no header (the first row
        // overwrote the header bytes). If the first line parses as numeric
        // motion data with the expected 7 columns, synthesize the canonical
        // header in-memory so export can proceed.
        if !requiredColumns.allSatisfy({ table.header.contains($0) }),
           table.header.count == requiredColumns.count,
           Double(table.header.first ?? "") != nil {
            table.rows.insert(table.header, at: 0)
            table.header = requiredColumns
        }

        guard let timestampIndex = table.header.firstIndex(of: "timestamp"),
              requiredColumns.allSatisfy({ table.header.contains($0) }) else {
            throw SessionReviewExportError.invalidMotionCSV
        }

        let base = motionBaseTimestamp ?? table.rows.compactMap { row -> Double? in
            guard timestampIndex < row.count else { return nil }
            return Double(row[timestampIndex])
        }.first ?? 0

        let labelIndex: Int
        if let existing = table.header.firstIndex(of: "label") {
            labelIndex = existing
        } else {
            table.header.append("label")
            labelIndex = table.header.count - 1
        }

        for rowIndex in table.rows.indices {
            while table.rows[rowIndex].count <= labelIndex {
                table.rows[rowIndex].append("")
            }

            guard timestampIndex < table.rows[rowIndex].count,
                  let timestamp = Double(table.rows[rowIndex][timestampIndex]) else {
                continue
            }

            let relativeTime = max(0, timestamp - base)
            let label = ReviewLabelResolver.resolvedLabel(
                at: relativeTime,
                predictions: predictionSpans,
                reviewSegments: finalizedReviewSegments,
                duration: timelineDuration
            )
            table.rows[rowIndex][labelIndex] = label ? "true" : "false"
        }

        let labeled = ReviewCSV.serialize(header: table.header, rows: table.rows)
        try labeled.write(
            to: sessionURL.appendingPathComponent("labeled_motion.csv"),
            atomically: true,
            encoding: .utf8
        )
    }

    private func writeReviewFeedbackCSV() throws {
        let header = ["timestamp", "event_type", "predicted_state", "corrected_state", "window_start", "window_end", "note"]
        var rows: [[String]] = []
        let now = ISO8601DateFormatter().string(from: Date())
        let base = motionBaseTimestamp ?? 0

        for segment in finalizedReviewSegments.sorted(by: { $0.startTime < $1.startTime }) {
            for part in split(segment: segment) {
                let predicted = ReviewLabelResolver.predictedChewing(
                    at: part.startTime,
                    predictions: predictionSpans,
                    duration: timelineDuration
                )
                let corrected = ReviewLabelResolver.label(
                    for: part.kind,
                    at: part.startTime,
                    predictions: predictionSpans,
                    duration: timelineDuration
                )
                let eventType = compatibilityEventType(predicted: predicted, corrected: corrected)

                rows.append([
                    now,
                    eventType,
                    predicted ? "chewing" : "idle",
                    corrected ? "chewing" : "idle",
                    String(format: "%.6f", base + part.startTime),
                    String(format: "%.6f", base + part.endTime),
                    segment.note
                ])
            }
        }

        let csv = ReviewCSV.serialize(header: header, rows: rows)
        try csv.write(
            to: sessionURL.appendingPathComponent("review_feedback.csv"),
            atomically: true,
            encoding: .utf8
        )
    }

    private func split(segment: ReviewSegment) -> [ReviewSegment] {
        let cuts = predictionSpans
            .flatMap { [$0.startTime, $0.endTime] }
            .filter { $0 > segment.startTime && $0 < segment.endTime }
            .sorted()

        guard !cuts.isEmpty else { return [segment] }

        var parts: [ReviewSegment] = []
        var start = segment.startTime
        for cut in cuts {
            if cut > start {
                parts.append(ReviewSegment(id: segment.id, startTime: start, endTime: cut, kind: segment.kind, note: segment.note))
            }
            start = cut
        }
        if segment.endTime > start {
            parts.append(ReviewSegment(id: segment.id, startTime: start, endTime: segment.endTime, kind: segment.kind, note: segment.note))
        }
        return parts
    }

    private func compatibilityEventType(predicted: Bool, corrected: Bool) -> String {
        if predicted == corrected {
            return FeedbackEventType.correct.rawValue
        }
        return predicted ? FeedbackEventType.falsePositive.rawValue : FeedbackEventType.falseNegative.rawValue
    }

    private func formatTime(_ seconds: Double) -> String {
        guard seconds.isFinite && seconds >= 0 else { return "00:00" }
        let total = Int(seconds.rounded())
        let minutes = total / 60
        let secs = total % 60
        return String(format: "%02d:%02d", minutes, secs)
    }
}
