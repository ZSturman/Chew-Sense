//
//  VideoEditorViewModel.swift
//  ChewSenseCollect
//
//  Created by Zachary Sturman on 11/13/25.
//

import Foundation
import SwiftUI
import AVFoundation
import AVKit
import UIKit
import Combine



final class VideoEditorViewModel: ObservableObject {
    @Published var player: AVPlayer
    @Published var currentTime: Double = 0
    @Published var duration: Double = 0
    @Published var thumbnails: [ThumbnailFrame] = []
    @Published var markers: [VideoMarker] = []
    @Published var selectedMarkerID: UUID?
    @Published var segments: [Segment] = []

    let framesVisible: Int = 15

    private var timeObserver: Any?
    private let asset: AVURLAsset
    private let imageGenerator: AVAssetImageGenerator
    private var frameDuration: Double
    private let markerStorageKey: String

    private var originalMarkers: [VideoMarker] = []
    private var boundCSVURL: URL?

    init(videoURL: URL) {
        self.asset = AVURLAsset(url: videoURL)
        let item = AVPlayerItem(asset: asset)
        self.player = AVPlayer(playerItem: item)

        self.imageGenerator = AVAssetImageGenerator(asset: asset)
        self.imageGenerator.appliesPreferredTrackTransform = true
        self.imageGenerator.maximumSize = CGSize(width: 160, height: 90)

        self.frameDuration = 1.0 / 30.0
        self.duration = 0
        self.markerStorageKey = "markers_\(videoURL.lastPathComponent)"

        Task {
            await self.loadVideoProperties()
        }

        loadMarkers()
        setupTimeObserver()
    }

    deinit {
        if let obs = timeObserver {
            player.removeTimeObserver(obs)
        }
    }

    // MARK: - Time Observer

    private func setupTimeObserver() {
        let interval = CMTime(seconds: 0.05, preferredTimescale: 600)
        timeObserver = player.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            guard let self = self else { return }
            self.currentTime = CMTimeGetSeconds(time)
        }
    }

    // MARK: - Asset loading (duration, frame rate)

    private func loadVideoProperties() async {
        do {
            let tracks = try await asset.loadTracks(withMediaType: .video)
            if let videoTrack = tracks.first {
                let fps = try await videoTrack.load(.nominalFrameRate)
                let frameDuration = fps > 0 ? 1.0 / fps : 1.0 / 30.0
                await MainActor.run {
                    self.frameDuration = Double(frameDuration)
                }
            }
            let assetDuration = try await asset.load(.duration)
            await MainActor.run {
                self.duration = CMTimeGetSeconds(assetDuration)
                self.updateThumbnails()
            }
        } catch {
            // Keep defaults on failure
        }
    }


    // MARK: - Thumbnails

    func updateThumbnails() {
        let count = framesVisible
        let total = duration

        guard count > 0, total > 0 else {
            DispatchQueue.main.async {
                self.thumbnails = []
            }
            return
        }

        let sampleTimes: [Double]
        if count == 1 {
            sampleTimes = [total / 2.0]
        } else {
            sampleTimes = (0..<count).map { index in
                Double(index) * total / Double(count - 1)
            }
        }

        let generator = imageGenerator

        var newFrames: [ThumbnailFrame?] = Array(repeating: nil, count: count)
        let group = DispatchGroup()

        for (idx, seconds) in sampleTimes.enumerated() {
            let cmTime = CMTime(seconds: seconds, preferredTimescale: 600)
            group.enter()
            generator.generateCGImageAsynchronously(for: cmTime) { cgImage, _, _ in
                let image = cgImage.map { UIImage(cgImage: $0) }
                let frame = ThumbnailFrame(id: idx, time: cmTime, image: image)
                DispatchQueue.main.async {
                    newFrames[idx] = frame
                    group.leave()
                }
            }
        }

        group.notify(queue: .main) {
            self.thumbnails = newFrames.compactMap { $0 }
        }
    }

    // MARK: - Zoom / Scrub / Seek

    func changeZoom(withScale scale: CGFloat) {
        // Zooming is disabled; thumbnails are static.
    }

    func seek(to time: Double) {
        let clamped = min(max(time, 0.0), duration)
        let cm = CMTime(seconds: clamped, preferredTimescale: 600)
        player.seek(to: cm, toleranceBefore: .zero, toleranceAfter: .zero)
        currentTime = clamped
    }

    // MARK: - Markers

    var selectedMarker: VideoMarker? {
        markers.first { $0.id == selectedMarkerID }
    }

    func addMarkerAtCurrentTime() {
        guard duration > 0 else { return }
        let t = currentTime
        // Provisional kind; will be normalized after insert.
        let marker = VideoMarker(id: UUID(), time: t, kind: .startChewing)
        markers.append(marker)
        normalizeMarkerKinds()
        recomputeSegments()
        saveMarkers()
    }

    func updateMarker(_ marker: VideoMarker, to time: Double) {
        guard let idx = markers.firstIndex(where: { $0.id == marker.id }) else { return }
        let clamped = min(max(time, 0.0), duration)
        markers[idx].time = clamped
        normalizeMarkerKinds()
        recomputeSegments()
        saveMarkers()
    }

    func deleteMarker(_ marker: VideoMarker) {
        markers.removeAll { $0.id == marker.id }
        if selectedMarkerID == marker.id {
            selectedMarkerID = nil
        }

        normalizeMarkerKinds()
        recomputeSegments()
        saveMarkers()
    }

    // MARK: - Session control

    func completeEditing() throws {
        //print("[VideoEditor] completeEditing() called; markers=\(markers.count), segments=\(segments.count)")
        guard duration.isFinite, duration > 0 else {
            throw BundleError.invalidVideoDuration
        }
        recomputeSegments()
        try writeCoarseLabelsForBoundSession()
        saveMarkers()
        originalMarkers = markers
    }
    func cancelEdits() {
        markers = originalMarkers
        selectedMarkerID = nil
        recomputeSegments()
        saveMarkers()
    }

    func finishLater() {
        recomputeSegments()
        saveMarkers()
        originalMarkers = markers
    }

    // MARK: - Marker typing / segments

    // Ensure marker kinds alternate (Start, Stop, Start, Stop...) in chronological order.
    private func normalizeMarkerKinds() {
        markers.sort { $0.time < $1.time }
        for idx in markers.indices {
            markers[idx].kind = (idx % 2 == 0) ? .startChewing : .stopChewing
        }
    }

    private func recomputeSegments() {
        let sorted = markers.sorted { $0.time < $1.time }

        var newSegments: [Segment] = []
        var openStart: VideoMarker?

        for marker in sorted {
            switch marker.kind {
            case .startChewing:
                openStart = marker
            case .stopChewing:
                if let start = openStart, marker.time > start.time {
                    newSegments.append(
                        Segment(
                            startTime: start.time,
                            endTime: marker.time,
                            isChewing: true
                        )
                    )
                    openStart = nil
                }
            }
        }

        segments = newSegments
    }

    func chewingState(at time: Double) -> ChewingState {
        guard !markers.isEmpty else {
            return .notChewing
        }

        if let seg = segments.first(where: { time >= $0.startTime && time < $0.endTime }) {
            return seg.isChewing ? .chewing : .notChewing
        }

        return .notChewing
    }

    // Determine what the next marker kind should be if a marker is dropped at a given time.
    func nextMarkerKind(at time: Double) -> MarkerKind {
        // Count how many markers occur strictly before this time.
        let countBefore = markers.filter { $0.time < time }.count
        return (countBefore % 2 == 0) ? .startChewing : .stopChewing
    }
    
    // Convenience based on the current playback time.
    var nextMarkerKindAtCurrentTime: MarkerKind {
        nextMarkerKind(at: currentTime)
    }
    
    var hasUnclosedChewingSegment: Bool {
        // An odd number of markers means the last Start has no matching Stop.
        return markers.count % 2 == 1
    }

    var addMarkerButtonTitle: String {
        switch nextMarkerKindAtCurrentTime {
        case .startChewing:
            return "Start Chewing"
        case .stopChewing:
            return "Stop Chewing"
        }
    }
    
    // MARK: - Coarse annotation persistence

    /// Bind the immutable motion file that identifies this canonical session.
    /// Human decisions are written to `annotations/coarse_labels.json`; the
    /// captured CSV is never modified.
    func bindCSVForLabelling(_ url: URL) {
        boundCSVURL = url
    }

    /// Explicitly persists the current coarse decision set.
    func relabelBoundCSV() {
        try? writeCoarseLabelsForBoundSession()
    }

    /// Compatibility entry point retained for callers that already bind by CSV
    /// URL. Marker timestamps are already video-relative; the manifest origin
    /// is validated so a legacy/mixed-clock session cannot be silently labelled.
    func relabelCSV(at csvURL: URL) throws {
        guard FileManager.default.fileExists(atPath: csvURL.path) else {
            throw CocoaError(.fileNoSuchFile)
        }
        guard duration.isFinite, duration > 0 else {
            throw BundleError.invalidVideoDuration
        }
        _ = try CanonicalBundleWriter.videoClockOrigin(for: csvURL)
        recomputeSegments()
        try CanonicalBundleWriter.writeCoarseLabels(
            intervals: coarseLabelIntervals(),
            forMotionCSV: csvURL
        )
    }

    private func writeCoarseLabelsForBoundSession() throws {
        guard let url = boundCSVURL else {
            return
        }
        try relabelCSV(at: url)
    }

    private func coarseLabelIntervals() -> [[String: Any]] {
        let videoEnd = max(0, duration)
        let normalizedSegments = segments
            .map { segment in
                (
                    start: min(max(0, segment.startTime), videoEnd),
                    end: min(max(0, segment.endTime), videoEnd)
                )
            }
            .filter { $0.end > $0.start }
            .sorted { $0.start < $1.start }

        var intervals: [[String: Any]] = []
        var cursor = 0.0
        for segment in normalizedSegments {
            let start = max(cursor, segment.start)
            if start > cursor {
                intervals.append(coarseInterval(cursor, start, "not_chewing"))
            }
            if segment.end > start {
                intervals.append(coarseInterval(start, segment.end, "chewing"))
                cursor = segment.end
            }
        }
        if cursor < videoEnd {
            intervals.append(coarseInterval(cursor, videoEnd, "not_chewing"))
        }
        if intervals.isEmpty {
            intervals.append(coarseInterval(0, videoEnd, "not_chewing"))
        }
        return intervals
    }

    private func coarseInterval(
        _ start: Double,
        _ end: Double,
        _ label: String
    ) -> [String: Any] {
        [
            "start_ts": start,
            "end_ts": end,
            "label": label,
            "source": "human_coarse"
        ]
    }
    
    // MARK: - Persistence

    private struct MarkerDTO: Codable {
        let id: UUID
        let time: Double
        let kind: MarkerKind
    }

    private func saveMarkers() {
        //print("[VideoEditor] saveMarkers() called; markers=\(markers.count), segments=\(segments.count)")
        let dto = markers.map { MarkerDTO(id: $0.id, time: $0.time, kind: $0.kind) }
        if let data = try? JSONEncoder().encode(dto) {
            UserDefaults.standard.set(data, forKey: markerStorageKey)
        }
    }
    
    private func loadMarkers() {
        if
            let data = UserDefaults.standard.data(forKey: markerStorageKey),
            let dto = try? JSONDecoder().decode([MarkerDTO].self, from: data)
        {
            self.markers = dto.map { VideoMarker(id: $0.id, time: $0.time, kind: $0.kind) }
        } else {
            self.markers = []
        }

        normalizeMarkerKinds()
        originalMarkers = markers

        recomputeSegments()
    }

    // MARK: - Formatting

    var formattedCurrentTime: String {
        formatTime(currentTime)
    }

    var formattedDuration: String {
        formatTime(duration)
    }

    private func formatTime(_ seconds: Double) -> String {
        guard seconds.isFinite && seconds >= 0 else { return "00:00:00" }
        let total = Int(seconds.rounded())
        let h = total / 3600
        let m = (total % 3600) / 60
        let s = total % 60
        return String(format: "%02d:%02d:%02d", h, m, s)
    }
}
