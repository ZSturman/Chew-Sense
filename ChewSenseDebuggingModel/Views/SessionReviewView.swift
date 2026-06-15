//
//  SessionReviewView.swift
//  ChewSenseDebuggingModel
//
//  Post-session video review and label export UI.
//

import SwiftUI
import AVFoundation
import AVKit
import UIKit

struct SessionReviewView: View {
    @StateObject private var viewModel: SessionReviewViewModel
    private let onSessionUpdated: (() -> Void)?

    @State private var showShareSheet = false
    @State private var shareItems: [Any] = []
    @State private var showNoCorrectionExportAlert = false
    @State private var exportError: String?
    @State private var exportSuccessMessage: String?
    @State private var showInspector = true
    @State private var showFolderPicker = false
    @State private var lastSavedFolderPath: String? = SessionExportDestinationStore.lastDisplayPath
    @State private var pendingNoCorrectionsAction: (() -> Void)?

    init(sessionURL: URL, onSessionUpdated: (() -> Void)? = nil) {
        _viewModel = StateObject(wrappedValue: SessionReviewViewModel(sessionURL: sessionURL))
        self.onSessionUpdated = onSessionUpdated
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                videoSection
                playbackControls
                timelineSection
                validationWorkspace
            }
            .padding(20)
        }
        .navigationTitle(viewModel.sessionURL.lastPathComponent)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Menu {
                    Button {
                        saveReviewedSessionToFolder()
                    } label: {
                        Label("Save Reviewed Session\u{2026}", systemImage: "square.and.arrow.down")
                    }
                    .disabled(!viewModel.canExport)

                    Button {
                        saveReviewedSessionToLastFolder()
                    } label: {
                        if let path = lastSavedFolderPath {
                            Label("Save Again to \(URL(fileURLWithPath: path).lastPathComponent)", systemImage: "arrow.down.doc.fill")
                        } else {
                            Label("Save Again to Last Folder", systemImage: "arrow.down.doc.fill")
                        }
                    }
                    .disabled(!viewModel.canExport || !SessionExportDestinationStore.hasRememberedFolder)

                    Divider()

                    Button {
                        shareReviewedSession()
                    } label: {
                        Label("Share\u{2026}", systemImage: "square.and.arrow.up")
                    }
                    .disabled(!viewModel.canExport)

                    Button {
                        showInspector.toggle()
                    } label: {
                        Label(
                            showInspector ? "Hide Configuration" : "Show Configuration",
                            systemImage: "slider.horizontal.3"
                        )
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        .inspector(isPresented: $showInspector) {
            RecordingConfigurationInspector(configuration: viewModel.recordingConfiguration)
        }
        .alert("Export without corrections?", isPresented: $showNoCorrectionExportAlert) {
            Button("Cancel", role: .cancel) {
                pendingNoCorrectionsAction = nil
            }
            Button("Export Predictions Only") {
                let action = pendingNoCorrectionsAction ?? { shareReviewedSession(confirmingNoCorrections: true) }
                pendingNoCorrectionsAction = nil
                action()
            }
        } message: {
            Text("No corrections were added. The exported labels will use the model prediction for the full session.")
        }
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(activityItems: shareItems)
        }
        .sheet(isPresented: $showFolderPicker) {
            ExportFolderPicker { url in
                showFolderPicker = false
                guard let url else { return }
                performSave(toPickedFolder: url)
            }
        }
        .onDisappear {
            viewModel.flushPendingSaves()
            viewModel.player.pause()
            onSessionUpdated?()
        }
    }

    private var videoSection: some View {
        ZStack {
            if viewModel.videoURL != nil {
                ReviewVideoPlayerView(player: viewModel.player)
            } else {
                Color.black
                Text("No review video found")
                    .foregroundColor(.white.opacity(0.8))
            }
        }
        .aspectRatio(16.0 / 9.0, contentMode: .fit)
        .frame(maxWidth: .infinity)
        .frame(maxHeight: 560)
        .background(Color.black)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var playbackControls: some View {
        HStack(spacing: 12) {
            Button(action: togglePlayPause) {
                Image(systemName: viewModel.player.timeControlStatus == .playing ? "pause.fill" : "play.fill")
                    .font(.title2)
                    .frame(width: 34, height: 34)
            }
            .buttonStyle(.bordered)

            Text(viewModel.formattedCurrentTime)
                .font(.callout.monospacedDigit())

            Slider(
                value: Binding(
                    get: { viewModel.currentTime },
                    set: { viewModel.seek(to: $0) }
                ),
                in: 0...max(viewModel.timelineDuration, 0.001)
            )

            Text(viewModel.formattedDuration)
                .font(.callout.monospacedDigit())
        }
    }

    private var timelineSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Timeline")
                    .font(.headline)
                Spacer()
                Label(viewModel.currentPredictionTitle, systemImage: viewModel.currentPredictionIsChewing ? "circle.fill" : "circle")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(viewModel.currentPredictionIsChewing ? .orange : .blue)
            }

            Text(viewModel.currentPredictionDetail)
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)

            ReviewTimelineView(viewModel: viewModel)
                .frame(minHeight: 210, idealHeight: 240, maxHeight: 280)
                .background(Color.gray.opacity(0.06))
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }

    private var validationWorkspace: some View {
        VStack(alignment: .leading, spacing: 14) {
            correctionControls

            if let selected = viewModel.selectedSegment {
                selectedSegmentControls(selected)
            }

            if let message = viewModel.statusMessage ?? exportError {
                Text(message)
                    .font(.footnote)
                    .foregroundColor(.red)
            }

            if let exportSuccessMessage {
                Text(exportSuccessMessage)
                    .font(.footnote)
                    .foregroundColor(.green)
            }

            if viewModel.hasActiveCorrection {
                Text("End the current correction before exporting.")
                    .font(.footnote)
                    .foregroundColor(.red)
            }

            VStack(alignment: .leading, spacing: 8) {
                Text("Session Notes")
                    .font(.headline)
                TextEditor(text: $viewModel.notes)
                    .frame(minHeight: 100)
                    .padding(8)
                    .background(Color.gray.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .onChange(of: viewModel.notes) { _, _ in
                        viewModel.scheduleNotesSave()
                    }
            }

            HStack(spacing: 12) {
                Button {
                    saveReviewedSessionToFolder()
                } label: {
                    Label("Save Reviewed Session\u{2026}", systemImage: "square.and.arrow.down")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(!viewModel.canExport)

                Button {
                    shareReviewedSession()
                } label: {
                    Label("Share\u{2026}", systemImage: "square.and.arrow.up")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(!viewModel.canExport)
            }

            if SessionExportDestinationStore.hasRememberedFolder {
                Button {
                    saveReviewedSessionToLastFolder()
                } label: {
                    if let path = lastSavedFolderPath {
                        Label("Save Again to \(URL(fileURLWithPath: path).lastPathComponent)",
                              systemImage: "arrow.down.doc.fill")
                            .frame(maxWidth: .infinity)
                    } else {
                        Label("Save Again to Last Folder", systemImage: "arrow.down.doc.fill")
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.bordered)
                .disabled(!viewModel.canExport)
            }
        }
    }

    private var correctionControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Validation")
                    .font(.headline)
                Spacer()
                Text(viewModel.hasActiveCorrection ? "Recording correction" : "At \(viewModel.formattedCurrentTime)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            HStack(spacing: 8) {
                correctionButton(for: .chewing)
                correctionButton(for: .notChewing)
            }
            .buttonStyle(.borderedProminent)
        }
    }

    private func correctionButton(for kind: ReviewSegmentKind) -> some View {
        let isActive = viewModel.activeCorrectionSegment?.kind == kind
        return Button {
            viewModel.beginCorrection(kind: kind)
        } label: {
            Label(isActive ? "End \(kind.title)" : kind.title, systemImage: kind.systemImage)
                .frame(maxWidth: .infinity)
        }
        .tint(kind.color)
    }

    private func selectedSegmentControls(_ segment: ReviewSegment) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Label(segment.kind.title, systemImage: segment.kind.systemImage)
                    .font(.caption.weight(.semibold))
                    .foregroundColor(segment.kind.color)

                Text("\(format(segment.startTime))-\(format(segment.endTime))")
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.secondary)

                Spacer()

                Button(role: .destructive) {
                    viewModel.deleteSelectedSegment()
                } label: {
                    Label("Delete", systemImage: "trash")
                        .labelStyle(.iconOnly)
                }
                .buttonStyle(.bordered)
            }

            HStack(spacing: 8) {
                BoundaryButton(title: "Start -", systemImage: "backward.frame") {
                    viewModel.updateSegment(id: segment.id, startTime: segment.startTime - 0.10, endTime: segment.endTime)
                }
                BoundaryButton(title: "Start +", systemImage: "forward.frame") {
                    viewModel.updateSegment(id: segment.id, startTime: segment.startTime + 0.10, endTime: segment.endTime)
                }
                BoundaryButton(title: "End -", systemImage: "backward.end") {
                    viewModel.updateSegment(id: segment.id, startTime: segment.startTime, endTime: segment.endTime - 0.10)
                }
                BoundaryButton(title: "End +", systemImage: "forward.end") {
                    viewModel.updateSegment(id: segment.id, startTime: segment.startTime, endTime: segment.endTime + 0.10)
                }
            }
            .disabled(segment.id == viewModel.activeCorrectionSegmentID)
        }
        .padding(8)
        .background(segment.kind.color.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func shareReviewedSession(confirmingNoCorrections: Bool = false) {
        exportError = nil
        exportSuccessMessage = nil

        if !confirmingNoCorrections && !viewModel.hasCorrections {
            pendingNoCorrectionsAction = { shareReviewedSession(confirmingNoCorrections: true) }
            showNoCorrectionExportAlert = true
            return
        }

        do {
            let url = try viewModel.prepareExport()
            onSessionUpdated?()
            shareItems = [url]
            showShareSheet = true
        } catch {
            exportError = error.localizedDescription
        }
    }

    private func saveReviewedSessionToFolder(confirmingNoCorrections: Bool = false) {
        exportError = nil
        exportSuccessMessage = nil

        if !confirmingNoCorrections && !viewModel.hasCorrections {
            pendingNoCorrectionsAction = { saveReviewedSessionToFolder(confirmingNoCorrections: true) }
            showNoCorrectionExportAlert = true
            return
        }

        do {
            _ = try viewModel.prepareExport()
            onSessionUpdated?()
            showFolderPicker = true
        } catch {
            exportError = error.localizedDescription
        }
    }

    private func saveReviewedSessionToLastFolder(confirmingNoCorrections: Bool = false) {
        exportError = nil
        exportSuccessMessage = nil

        if !confirmingNoCorrections && !viewModel.hasCorrections {
            pendingNoCorrectionsAction = { saveReviewedSessionToLastFolder(confirmingNoCorrections: true) }
            showNoCorrectionExportAlert = true
            return
        }

        do {
            _ = try viewModel.prepareExport()
            onSessionUpdated?()
        } catch {
            exportError = error.localizedDescription
            return
        }

        let resolvedFolder: (url: URL, accessGranted: Bool)?
        do {
            resolvedFolder = try SessionExportDestinationStore.resolveLastFolder()
        } catch {
            resolvedFolder = nil
        }
        guard let resolved = resolvedFolder else {
            exportError = "Last destination is no longer available. Use Save\u{2026} to pick a new folder."
            SessionExportDestinationStore.clear()
            lastSavedFolderPath = nil
            return
        }
        let folderURL = resolved.url
        let accessGranted = resolved.accessGranted
        defer {
            if accessGranted {
                folderURL.stopAccessingSecurityScopedResource()
            }
        }

        do {
            let copied = try SessionExportCopier.copySessionBundle(
                at: viewModel.sessionURL,
                into: folderURL
            )
            lastSavedFolderPath = folderURL.path
            exportSuccessMessage = "Saved to \(copied.path)"
        } catch {
            exportError = "Save failed: \(error.localizedDescription)"
        }
    }

    private func performSave(toPickedFolder folderURL: URL) {
        exportError = nil
        let granted = folderURL.startAccessingSecurityScopedResource()
        defer {
            if granted {
                folderURL.stopAccessingSecurityScopedResource()
            }
        }

        do {
            let copied = try SessionExportCopier.copySessionBundle(
                at: viewModel.sessionURL,
                into: folderURL
            )
            SessionExportDestinationStore.remember(folderURL)
            lastSavedFolderPath = folderURL.path
            exportSuccessMessage = "Saved to \(copied.path)"
        } catch {
            exportError = "Save failed: \(error.localizedDescription)"
        }
    }

    private func togglePlayPause() {
        if viewModel.player.timeControlStatus == .playing {
            viewModel.player.pause()
        } else {
            viewModel.player.play()
        }
    }

    private func format(_ seconds: Double) -> String {
        let total = Int(max(0, seconds).rounded())
        return String(format: "%02d:%02d", total / 60, total % 60)
    }
}

private struct BoundaryButton: View {
    let title: String
    let systemImage: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Label(title, systemImage: systemImage)
                .labelStyle(.iconOnly)
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .help(title)
    }
}

struct ReviewVideoPlayerView: UIViewControllerRepresentable {
    let player: AVPlayer

    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let controller = AVPlayerViewController()
        controller.player = player
        controller.showsPlaybackControls = false
        controller.videoGravity = .resizeAspect
        return controller
    }

    func updateUIViewController(_ uiViewController: AVPlayerViewController, context: Context) {
        uiViewController.player = player
        uiViewController.videoGravity = .resizeAspect
    }
}

struct ReviewTimelineView: View {
    @ObservedObject var viewModel: SessionReviewViewModel
    @State private var segmentDragStart: ReviewSegment?

    var body: some View {
        GeometryReader { geo in
            let labelWidth: CGFloat = min(96, max(78, geo.size.width * 0.18))
            let laneWidth = max(geo.size.width - labelWidth - 12, 1)
            let predictionHeight: CGFloat = 34
            let reviewHeight: CGFloat = 38
            let filmHeight = max(geo.size.height - predictionHeight - reviewHeight - 34, 46)
            let predictionY: CGFloat = 10
            let reviewY = predictionY + predictionHeight + 8
            let filmY = reviewY + reviewHeight + 10
            let playheadX = labelWidth + xFor(time: viewModel.currentTime, width: laneWidth)

            ZStack(alignment: .topLeading) {
                laneLabel("Predicted", y: predictionY, height: predictionHeight)
                laneLabel("Corrected", y: reviewY, height: reviewHeight)

                laneBackground(width: laneWidth, height: predictionHeight)
                    .offset(x: labelWidth, y: predictionY)
                laneBackground(width: laneWidth, height: reviewHeight)
                    .offset(x: labelWidth, y: reviewY)

                if viewModel.predictionSpans.isEmpty {
                    emptyLaneText("Not Chewing", width: laneWidth, height: predictionHeight)
                        .offset(x: labelWidth, y: predictionY)
                } else {
                    ForEach(viewModel.predictionSpans) { span in
                        predictionSpan(span, width: laneWidth, height: predictionHeight)
                            .offset(x: labelWidth, y: predictionY)
                    }
                }

                if viewModel.resolvedReviewSpans.isEmpty {
                    emptyLaneText("No corrections", width: laneWidth, height: reviewHeight)
                        .offset(x: labelWidth, y: reviewY)
                } else {
                    ForEach(viewModel.resolvedReviewSpans) { span in
                        correctedSpan(span, width: laneWidth, height: reviewHeight)
                            .offset(x: labelWidth, y: reviewY)
                    }
                }

                ForEach(viewModel.reviewSegments) { segment in
                    reviewSegmentInteraction(segment, width: laneWidth, height: reviewHeight)
                        .offset(x: labelWidth, y: reviewY)
                }

                filmstrip(width: laneWidth, height: filmHeight)
                    .offset(x: labelWidth, y: filmY)

                Rectangle()
                    .fill(Color.primary)
                    .frame(width: 2, height: geo.size.height - 8)
                    .position(x: playheadX, y: (geo.size.height - 8) / 2 + 4)
                    .shadow(radius: 1)

                Text(viewModel.formattedCurrentTime)
                    .font(.caption2.monospacedDigit().weight(.semibold))
                    .padding(.horizontal, 5)
                    .padding(.vertical, 2)
                    .background(Color.primary)
                    .foregroundStyle(Color(.systemBackground))
                    .clipShape(Capsule())
                    .position(x: min(max(playheadX, labelWidth + 22), geo.size.width - 24), y: geo.size.height - 12)
            }
            .contentShape(Rectangle())
            .onTapGesture {
                viewModel.selectedSegmentID = nil
            }
        }
    }

    private func laneLabel(_ title: String, y: CGFloat, height: CGFloat) -> some View {
        Text(title)
            .font(.caption.weight(.semibold))
            .foregroundStyle(.secondary)
            .frame(width: 88, height: height, alignment: .trailing)
            .offset(x: 0, y: y)
    }

    private func laneBackground(width: CGFloat, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 6)
            .fill(Color.gray.opacity(0.10))
            .frame(width: width, height: height)
    }

    private func emptyLaneText(_ text: String, width: CGFloat, height: CGFloat) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.secondary)
            .frame(width: width, height: height)
    }

    private func predictionSpan(_ span: PredictionSpan, width: CGFloat, height: CGFloat) -> some View {
        let startX = xFor(time: span.startTime, width: width)
        let endX = xFor(time: span.endTime, width: width)
        let spanWidth = max(endX - startX, 2)

        return ZStack {
            RoundedRectangle(cornerRadius: 4)
                .fill(span.isChewing ? Color.orange.opacity(0.68) : Color.blue.opacity(0.32))
            if spanWidth > 76 {
                Text(span.isChewing ? "Chewing" : "Not Chewing")
                    .font(.caption2.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .foregroundColor(.primary.opacity(0.75))
                    .padding(.horizontal, 4)
            }
        }
        .frame(width: spanWidth, height: height - 4)
        .position(x: startX + spanWidth / 2, y: height / 2)
    }

    private func correctedSpan(_ span: ResolvedReviewSpan, width: CGFloat, height: CGFloat) -> some View {
        let startX = xFor(time: span.startTime, width: width)
        let endX = xFor(time: span.endTime, width: width)
        let spanWidth = max(endX - startX, 2)
        let isSelected = viewModel.selectedSegmentID == span.sourceSegmentID

        return ZStack {
            RoundedRectangle(cornerRadius: 4)
                .fill(span.isChewing ? Color.orange.opacity(isSelected ? 0.78 : 0.58) : Color.blue.opacity(isSelected ? 0.68 : 0.48))
                .overlay {
                    if span.sourceKind == .correct {
                        RoundedRectangle(cornerRadius: 4)
                            .stroke(Color.green.opacity(0.85), lineWidth: 2)
                    }
                }

            if spanWidth > 76 {
                Text(span.isChewing ? "Chewing" : "Not Chewing")
                    .font(.caption2.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .foregroundColor(.white)
                    .padding(.horizontal, 4)
            }
        }
        .frame(width: spanWidth, height: height - 6)
        .position(x: startX + spanWidth / 2, y: height / 2)
    }

    private func reviewSegmentInteraction(_ segment: ReviewSegment, width: CGFloat, height: CGFloat) -> some View {
        let times = viewModel.displayTimes(for: segment)
        let startX = xFor(time: times.startTime, width: width)
        let endX = xFor(time: times.endTime, width: width)
        let spanWidth = max(endX - startX, 12)
        let isSelected = viewModel.selectedSegmentID == segment.id

        return ZStack {
            RoundedRectangle(cornerRadius: 4)
                .fill(Color.clear)
                .overlay {
                    if isSelected {
                        RoundedRectangle(cornerRadius: 4)
                            .stroke(Color.primary.opacity(0.85), lineWidth: 2)
                    }
                }
                .frame(width: spanWidth, height: height - 6)
                .position(x: startX + spanWidth / 2, y: height / 2)
                .contentShape(Rectangle())
                .gesture(
                    DragGesture()
                        .onChanged { value in
                            guard segment.id != viewModel.activeCorrectionSegmentID else { return }
                            if segmentDragStart == nil {
                                segmentDragStart = segment
                            }
                            guard let original = segmentDragStart else { return }
                            let delta = Double(value.translation.width / width) * max(viewModel.timelineDuration, 0.0001)
                            viewModel.updateSegment(
                                id: segment.id,
                                startTime: original.startTime + delta,
                                endTime: original.endTime + delta
                            )
                        }
                        .onEnded { _ in
                            segmentDragStart = nil
                        }
                )
                .onTapGesture {
                    viewModel.selectedSegmentID = segment.id
                }

            if isSelected && segment.id != viewModel.activeCorrectionSegmentID {
                handle(x: startX, height: height)
                    .gesture(edgeGesture(segment: segment, edge: .start, width: width))
                handle(x: endX, height: height)
                    .gesture(edgeGesture(segment: segment, edge: .end, width: width))
            }
        }
    }

    private func filmstrip(width: CGFloat, height: CGFloat) -> some View {
        ZStack(alignment: .leading) {
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.black.opacity(0.88))

            HStack(spacing: 0) {
                ForEach(viewModel.thumbnails) { thumb in
                    ZStack {
                        if let image = thumb.image {
                            Image(uiImage: image)
                                .resizable()
                                .scaledToFill()
                        } else {
                            Color.black
                        }
                    }
                    .frame(
                        width: width / CGFloat(max(viewModel.framesVisible, 1)),
                        height: height
                    )
                    .clipped()
                }
            }
        }
        .frame(width: width, height: height)
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .contentShape(Rectangle())
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { value in
                    viewModel.seek(to: time(for: value.location.x, width: width))
                }
        )
    }

    private enum Edge {
        case start
        case end
    }

    private func handle(x: CGFloat, height: CGFloat) -> some View {
        Capsule()
            .fill(Color.white)
            .overlay(Capsule().stroke(Color.black.opacity(0.25), lineWidth: 1))
            .frame(width: 10, height: height)
            .position(x: x, y: height / 2)
    }

    private func edgeGesture(segment: ReviewSegment, edge: Edge, width: CGFloat) -> some Gesture {
        DragGesture()
            .onChanged { value in
                let delta = Double(value.translation.width / width) * max(viewModel.timelineDuration, 0.0001)
                switch edge {
                case .start:
                    viewModel.updateSegment(id: segment.id, startTime: segment.startTime + delta, endTime: segment.endTime)
                case .end:
                    viewModel.updateSegment(id: segment.id, startTime: segment.startTime, endTime: segment.endTime + delta)
                }
            }
    }

    private func normalizedPosition(for time: Double) -> Double {
        let total = max(viewModel.timelineDuration, 0.0001)
        return min(max(time, 0), total) / total
    }

    private func xFor(time: Double, width: CGFloat) -> CGFloat {
        CGFloat(normalizedPosition(for: time)) * width
    }

    private func time(for x: CGFloat, width: CGFloat) -> Double {
        let fraction = min(max(Double(x / max(width, 1)), 0), 1)
        return fraction * max(viewModel.timelineDuration, 0)
    }
}

extension ReviewSegmentKind {
    var color: Color {
        switch self {
        case .chewing:
            return .orange
        case .notChewing:
            return .blue
        case .correct:
            return .green
        case .falsePositive:
            return .red
        case .falseNegative:
            return .orange
        }
    }

    var systemImage: String {
        switch self {
        case .chewing:
            return "circle.fill"
        case .notChewing:
            return "circle"
        case .correct:
            return "checkmark.circle.fill"
        case .falsePositive:
            return "xmark.circle.fill"
        case .falseNegative:
            return "exclamationmark.circle.fill"
        }
    }
}
