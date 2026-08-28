//
//  MainContentView.swift
//  ChewSenseLive
//
//  Main UI for chewing detection debugging/testing.
//  Refactored from monolithic ContentView.swift to use modular components.
//

import SwiftUI
import UniformTypeIdentifiers

struct MainContentView: View {
    @StateObject private var motionManager = MotionManager()
    @StateObject private var config = DetectionConfiguration()
    @StateObject private var sessionLibrary = SessionLibraryViewModel()

    @State private var sessionNotes = ""
    @State private var selectedSessionURL: URL?
    @State private var isComposingNew = false
    @State private var showConfigurationInspector = false
    @State private var sessionPendingDeletion: ReviewSessionSummary?

    #if os(iOS)
    // On iPhone (compact size class) NavigationSplitView ignores columnVisibility
    // and never shows the detail column on its own. We switch to NavigationStack
    // for compact and keep NavigationSplitView for iPad / Mac Catalyst.
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    #endif

    // MARK: - Body

    var body: some View {
        #if os(iOS)
        if horizontalSizeClass == .compact {
            phoneBody
        } else {
            splitBody
        }
        #else
        splitBody
        #endif
    }

    // MARK: - iPhone (NavigationStack) layout

    #if os(iOS)
    @ViewBuilder
    private var phoneBody: some View {
        NavigationStack {
            phoneSidebar
                .navigationTitle("Sessions")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Button {
                            sessionLibrary.refresh()
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                    ToolbarItem(placement: .secondaryAction) {
                        NavigationLink {
                            MetricsView(metrics: motionManager.mealMetrics)
                        } label: {
                            Label("Live Metrics", systemImage: "chart.bar.xaxis")
                        }
                    }
                }
                // Push RecordingComposerView when isComposingNew becomes true.
                .navigationDestination(isPresented: $isComposingNew) {
                    RecordingComposerView(
                        motionManager: motionManager,
                        config: config,
                        sessionNotes: $sessionNotes,
                        onCancel: {
                            Task { @MainActor in
                                isComposingNew = false
                            }
                        }
                    )
                }
                // Live owns capture and inference only. Human annotation is
                // performed in the desktop CycleReviewer.
                .navigationDestination(item: $selectedSessionURL) { url in
                    LiveSessionDetailView(
                        sessionURL: url,
                        summary: sessionLibrary.summary(for: url),
                        motionManager: motionManager
                    )
                }
        }
        .inspector(isPresented: $showConfigurationInspector) {
            DetectionConfigurationInspector(
                config: config,
                isEditable: !motionManager.isRunning
                    && !motionManager.isStartingSession
                    && !motionManager.isFinalizingSession
            )
        }
        .onAppear {
            sessionLibrary.refresh()
        }
        .onChange(of: motionManager.latestCompletedSessionURL) { _, newValue in
            guard let newValue else { return }
            Task { @MainActor in
                sessionLibrary.refresh()
                isComposingNew = false
                selectedSessionURL = newValue
            }
        }
        .confirmationDialog(
            "Delete this recording?",
            isPresented: Binding(
                get: { sessionPendingDeletion != nil },
                set: { if !$0 { sessionPendingDeletion = nil } }
            ),
            titleVisibility: .visible,
            presenting: sessionPendingDeletion
        ) { summary in
            Button("Delete", role: .destructive) {
                performDelete(summary)
            }
            Button("Cancel", role: .cancel) {
                sessionPendingDeletion = nil
            }
        } message: { summary in
            Text("\(summary.title) will be permanently removed from this device.")
        }
    }

    /// Root content for the iPhone NavigationStack. Uses plain `Button` rows
    /// (instead of `List(selection:)`) so tapping a session updates
    /// `selectedSessionURL` and triggers the `navigationDestination(item:)` push.
    private var phoneSidebar: some View {
        VStack(spacing: 0) {
            capturePanel
                .padding()

            Divider()

            List {
                if sessionLibrary.sessions.isEmpty {
                    ContentUnavailableView(
                        "No Sessions",
                        systemImage: "film.stack",
                        description: Text("Tap New to record your first session.")
                    )
                    .listRowSeparator(.hidden)
                } else {
                    Section("Recorded Sessions") {
                        ForEach(sessionLibrary.sessions) { session in
                            Button {
                                selectedSessionURL = session.sessionURL
                            } label: {
                                SessionSidebarRow(session: session)
                            }
                            .buttonStyle(.plain)
                            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                Button(role: .destructive) {
                                    sessionPendingDeletion = session
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            }
                            .contextMenu {
                                Button(role: .destructive) {
                                    sessionPendingDeletion = session
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    #endif

    // MARK: - iPad / Mac (NavigationSplitView) layout

    private var splitBody: some View {
        NavigationSplitView {
            sidebar
                .navigationTitle("Sessions")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Button {
                            sessionLibrary.refresh()
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                        .help("Refresh Sessions")
                    }
                    ToolbarItem(placement: .secondaryAction) {
                        NavigationLink {
                            MetricsView(metrics: motionManager.mealMetrics)
                        } label: {
                            Label("Live Metrics", systemImage: "chart.bar.xaxis")
                        }
                    }
                }
        } detail: {
            detail
        }
        .inspector(isPresented: $showConfigurationInspector) {
            DetectionConfigurationInspector(
                config: config,
                isEditable: !motionManager.isRunning
                    && !motionManager.isStartingSession
                    && !motionManager.isFinalizingSession
            )
        }
        .onAppear {
            sessionLibrary.refresh()
            if selectedSessionURL == nil && !isComposingNew {
                selectedSessionURL = sessionLibrary.sessions.first?.sessionURL
            }
        }
        .onChange(of: motionManager.latestCompletedSessionURL) { _, newValue in
            guard let newValue else { return }
            Task { @MainActor in
                sessionLibrary.refresh()
                isComposingNew = false
                selectedSessionURL = newValue
            }
        }
        .confirmationDialog(
            "Delete this recording?",
            isPresented: Binding(
                get: { sessionPendingDeletion != nil },
                set: { if !$0 { sessionPendingDeletion = nil } }
            ),
            titleVisibility: .visible,
            presenting: sessionPendingDeletion
        ) { summary in
            Button("Delete", role: .destructive) {
                performDelete(summary)
            }
            Button("Cancel", role: .cancel) {
                sessionPendingDeletion = nil
            }
        } message: { summary in
            Text("\(summary.title) will be permanently removed from this device.")
        }
    }

    // MARK: - Shared sub-views (iPad / Mac split layout)

    private var sidebar: some View {
        VStack(spacing: 0) {
            capturePanel
                .padding()

            Divider()

            List(selection: $selectedSessionURL) {
                if sessionLibrary.sessions.isEmpty {
                    ContentUnavailableView(
                        "No Sessions",
                        systemImage: "film.stack",
                        description: Text("Tap New to record your first session.")
                    )
                    .listRowSeparator(.hidden)
                } else {
                    Section("Recorded Sessions") {
                        ForEach(sessionLibrary.sessions) { session in
                            SessionSidebarRow(session: session)
                                .tag(session.sessionURL)
                                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                    Button(role: .destructive) {
                                        sessionPendingDeletion = session
                                    } label: {
                                        Label("Delete", systemImage: "trash")
                                    }
                                }
                                .contextMenu {
                                    Button(role: .destructive) {
                                        sessionPendingDeletion = session
                                    } label: {
                                        Label("Delete", systemImage: "trash")
                                    }
                                }
                        }
                    }
                }
            }
        }
    }

    private var capturePanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Capture")
                        .font(.headline)
                    Text(motionManager.statusText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                #if os(iOS)
                RoutePickerView()
                    .frame(width: 34, height: 34)
                    .accessibilityLabel("Select AirPods")
                #endif
                Button {
                    showConfigurationInspector.toggle()
                } label: {
                    Image(systemName: "slider.horizontal.3")
                }
                .buttonStyle(.bordered)
                .help("Recording Configuration")
            }

            Button(action: startNewComposition) {
                Label("New", systemImage: "plus.circle.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.green)
            .disabled(
                motionManager.isRunning
                    || motionManager.isStartingSession
                    || motionManager.isFinalizingSession
            )
            .accessibilityIdentifier("newFieldTestButton")

            LiveGuidanceView(stage: .prepare)

            if CollectionRecommendations.current.hasRecommendations {
                Divider()
                nextToRecordPanel
            }
        }
    }

    private var nextToRecordPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("Next to Record")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Text(CollectionRecommendations.current.sourceSplit)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
            }

            ForEach(CollectionRecommendations.current.recommendations) { recommendation in
                Button {
                    startNewComposition(with: recommendation)
                } label: {
                    NextRecommendationRow(recommendation: recommendation)
                }
                .buttonStyle(.plain)
                .disabled(
                    motionManager.isRunning
                        || motionManager.isStartingSession
                        || motionManager.isFinalizingSession
                )
            }
        }
    }

    @ViewBuilder
    private var detail: some View {
        if isComposingNew || motionManager.isRunning
            || motionManager.isStartingSession || motionManager.isFinalizingSession {
            RecordingComposerView(
                motionManager: motionManager,
                config: config,
                sessionNotes: $sessionNotes,
                onCancel: {
                    Task { @MainActor in
                        isComposingNew = false
                    }
                }
            )
        } else if let selectedSessionURL {
            LiveSessionDetailView(
                sessionURL: selectedSessionURL,
                summary: sessionLibrary.summary(for: selectedSessionURL),
                motionManager: motionManager
            )
            .id(selectedSessionURL)
        } else {
            ContentUnavailableView(
                "Select a Session",
                systemImage: "waveform.path.ecg.rectangle",
                description: Text("Choose a recorded field-test bundle from the sidebar, or tap New to record.")
            )
        }
    }

    // MARK: - Actions

    private func startNewComposition() {
        sessionNotes = ""
        selectedSessionURL = nil
        showConfigurationInspector = false
        isComposingNew = true
    }

    private func startNewComposition(with recommendation: CollectionRecommendation) {
        sessionNotes = ""
        selectedSessionURL = nil
        showConfigurationInspector = false
        config.applyRecommendation(recommendation)
        isComposingNew = true
    }

    private func performDelete(_ summary: ReviewSessionSummary) {
        let wasSelected = selectedSessionURL == summary.sessionURL
        if sessionLibrary.delete(summary), wasSelected {
            selectedSessionURL = sessionLibrary.sessions.first?.sessionURL
        }
        sessionPendingDeletion = nil
    }
}

private struct NextRecommendationRow: View {
    let recommendation: CollectionRecommendation

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: recommendation.activityPreset?.systemImage ?? "target")
                .foregroundStyle(color)
                .frame(width: 20)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(recommendation.activityLabel)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(1)
                    Spacer(minLength: 4)
                    Text(recommendation.priorityLabel)
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(color)
                    Text(recommendation.minutesLabel)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                }

                Text(recommendation.reason)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
            }
        }
        .padding(8)
        .background(Color.gray.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private var color: Color {
        switch recommendation.expectedLabelClass {
        case "positive":
            return .orange
        case "negative":
            return .blue
        case "mixed":
            return .purple
        default:
            return .gray
        }
    }
}

private struct LiveRecordingDetailView: View {
    @ObservedObject var motionManager: MotionManager
    @ObservedObject var config: DetectionConfiguration

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Live Recording")
                            .font(.title2.weight(.semibold))
                        Text(motionManager.statusText)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()

                    Label(motionManager.isChewing ? "Chewing" : "Not Chewing", systemImage: motionManager.isChewing ? "circle.fill" : "circle")
                        .font(.callout.weight(.semibold))
                        .foregroundStyle(motionManager.isChewing ? .orange : .blue)
                }

                LiveGuidanceView(stage: .record)

                #if os(iOS)
                if let captureSession = motionManager.captureSession {
                    CameraPreviewView(session: captureSession)
                        .aspectRatio(16.0 / 9.0, contentMode: .fit)
                        .frame(maxWidth: .infinity)
                        .frame(maxHeight: 560)
                        .background(Color.black)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                #endif

                VStack(alignment: .leading, spacing: 8) {
                    Text("Prediction Timeline")
                        .font(.headline)
                    LivePredictionTimelineView(
                        spans: motionManager.livePredictionSpans,
                        duration: motionManager.liveTimelineDuration
                    )
                    .frame(height: 86)
                    .background(Color.gray.opacity(0.06))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }

                ProbabilityView(
                    latestProb: motionManager.latestProb,
                    smoothedProb: motionManager.latestSmoothed,
                    isChewing: motionManager.isChewing,
                    highThreshold: RuntimeConfig.highThreshold,
                    lowThreshold: RuntimeConfig.lowThreshold
                )

                if !motionManager.logPreview.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Recent Output")
                            .font(.headline)
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 3) {
                                ForEach(motionManager.logPreview.suffix(18), id: \.self) { line in
                                    Text(line)
                                        .font(.system(.caption2, design: .monospaced))
                                        .foregroundStyle(.secondary)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                        }
                        .frame(maxHeight: 180)
                    }
                }
            }
            .padding(20)
        }
        .navigationTitle("Recording")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct LivePredictionTimelineView: View {
    let spans: [PredictionSpan]
    let duration: Double

    var body: some View {
        GeometryReader { geo in
            let labelWidth: CGFloat = min(104, max(86, geo.size.width * 0.20))
            let laneWidth = max(geo.size.width - labelWidth - 12, 1)
            let timelineDuration = max(duration, spans.map(\.endTime).max() ?? 0)
            let coverage = PredictionSpanResolver.coverageSpans(from: spans, duration: timelineDuration)

            ZStack(alignment: .topLeading) {
                Text("Predicted")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .frame(width: labelWidth - 8, height: 38, alignment: .trailing)
                    .offset(x: 0, y: 12)

                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.gray.opacity(0.10))
                    .frame(width: laneWidth, height: 38)
                    .offset(x: labelWidth, y: 12)

                if coverage.isEmpty {
                    Text("Waiting for model output")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .frame(width: laneWidth, height: 38)
                        .offset(x: labelWidth, y: 12)
                } else {
                    ForEach(coverage) { span in
                        liveSpan(span, width: laneWidth, height: 38, duration: max(timelineDuration, 0.001))
                            .offset(x: labelWidth, y: 12)
                    }
                }

                Text(format(timelineDuration))
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .frame(width: laneWidth, alignment: .trailing)
                    .offset(x: labelWidth, y: 56)
            }
        }
    }

    private func liveSpan(_ span: PredictionSpan, width: CGFloat, height: CGFloat, duration: Double) -> some View {
        let startX = CGFloat(min(max(span.startTime / duration, 0), 1)) * width
        let endX = CGFloat(min(max(span.endTime / duration, 0), 1)) * width
        let spanWidth = max(endX - startX, 2)

        return ZStack {
            RoundedRectangle(cornerRadius: 4)
                .fill(span.isChewing ? Color.orange.opacity(0.68) : Color.blue.opacity(0.36))
            if spanWidth > 86 {
                Text(span.isChewing ? "Chewing" : "Not Chewing")
                    .font(.caption2.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .foregroundColor(span.isChewing ? .white : .primary.opacity(0.78))
                    .padding(.horizontal, 4)
            }
        }
        .frame(width: spanWidth, height: height - 4)
        .position(x: startX + spanWidth / 2, y: height / 2)
    }

    private func format(_ seconds: Double) -> String {
        guard seconds.isFinite && seconds > 0 else { return "00:00" }
        let total = Int(seconds.rounded())
        return String(format: "%02d:%02d", total / 60, total % 60)
    }
}

private struct SessionSidebarRow: View {
    let session: ReviewSessionSummary

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "waveform.path.ecg.rectangle")
                .foregroundStyle(.blue)
                .frame(width: 22)

            VStack(alignment: .leading, spacing: 5) {
                Text(session.title)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(2)

                Text(session.subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                HStack(spacing: 6) {
                    SessionBadge(text: session.formattedDuration, systemImage: "clock")
                    SessionBadge(text: "captured", systemImage: "tray.full")
                    if let label = session.activityLabel ?? session.activitySlug {
                        SessionBadge(text: label, systemImage: "figure.walk")
                    }
                    if session.goldLockCandidate {
                        SessionBadge(text: "benchmark", systemImage: "lock.fill")
                    }
                    if session.hasCorrections {
                        SessionBadge(text: "\(session.correctionCount)", systemImage: "pencil")
                    }
                    if session.predictionCount > 0 {
                        SessionBadge(text: "pred", systemImage: "chart.line.uptrend.xyaxis")
                    }
                }
            }
        }
    }
}

private struct LiveSessionDetailView: View {
    let sessionURL: URL
    let summary: ReviewSessionSummary?
    @ObservedObject var motionManager: MotionManager

    @State private var mealSummary: StoredMealSummary?
    @State private var calibrationProgress: CalibrationProgress?
    @State private var calibrationMessage: String?
    @State private var isVerifiedCalibration = false
    @State private var showCalibrationConfirmation = false
    @State private var cadenceGoalDraft = 0.6

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text(summary?.title ?? sessionURL.lastPathComponent)
                    .font(.title2.weight(.semibold))
                Text("This app captures synchronized video, AirPods motion, and model telemetry. It does not create or edit ground truth.")
                    .foregroundStyle(.secondary)

                Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 8) {
                    GridRow {
                        Text("Duration").foregroundStyle(.secondary)
                        Text(summary?.formattedDuration ?? "--:--")
                    }
                    GridRow {
                        Text("Predictions").foregroundStyle(.secondary)
                        Text("\(summary?.predictionCount ?? 0)")
                    }
                    if let activity = summary?.activityLabel ?? summary?.activitySlug {
                        GridRow {
                            Text("Activity").foregroundStyle(.secondary)
                            Text(activity)
                        }
                    }
                }

                if let mealSummary {
                    postMealSummary(mealSummary)
                }

                Text("Export this bundle to the computer pipeline. Legacy annotations are preserved only for migration; new chew-cycle and food-entry labels belong to CycleReviewer.")
                    .font(.callout)
                    .foregroundStyle(.secondary)

                LiveGuidanceView(stage: .export)

                ShareLink(item: sessionURL) {
                    Label("Export Session Bundle", systemImage: "square.and.arrow.up")
                }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("exportLiveSessionButton")
            }
            .padding(24)
        }
        .navigationTitle("Field Test")
        .task(id: sessionURL) { loadMealSummary() }
        .confirmationDialog(
            "Use this as a calibration meal?",
            isPresented: $showCalibrationConfirmation,
            titleVisibility: .visible
        ) {
            Button("Verify Calibration Meal") { verifyCalibrationMeal() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This saves the session's median confident chew interval to your personal \(mealSummary?.texture.title ?? "meal") baseline. Three verified meals activate cadence coaching for that texture.")
        }
    }

    @ViewBuilder
    private func postMealSummary(_ meal: StoredMealSummary) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Post-meal cadence")
                .font(.headline)
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                GridRow {
                    Text("Detected chews").foregroundStyle(.secondary)
                    Text("\(meal.detectedChews)")
                }
                GridRow {
                    Text("Median interval").foregroundStyle(.secondary)
                    Text(meal.medianInterval.map { String(format: "%.2f s", $0) } ?? "—")
                }
                if let p10 = meal.intervalP10, let p90 = meal.intervalP90 {
                    GridRow {
                        Text("Cadence distribution").foregroundStyle(.secondary)
                        Text(String(format: "P10 %.2f s · P90 %.2f s", p10, p90))
                    }
                }
                GridRow {
                    Text("Confidence coverage").foregroundStyle(.secondary)
                    Text(String(format: "%.0f%%", meal.confidenceCoverage * 100))
                }
                GridRow {
                    Text("Excluded uncertain").foregroundStyle(.secondary)
                    Text(String(format: "%.1f s", meal.uncertainTimeSec))
                }
            }

            if meal.chewEventModelPromoted, meal.medianInterval != nil {
                if isVerifiedCalibration {
                    Label(calibrationProgressText, systemImage: "checkmark.seal.fill")
                        .foregroundStyle(.green)
                    if calibrationProgress?.cadenceGoalActivated == true {
                        Stepper(
                            "Minimum rolling median: \(cadenceGoalDraft, format: .number.precision(.fractionLength(2))) s",
                            value: $cadenceGoalDraft,
                            in: 0.20...2.00,
                            step: 0.05
                        )
                        Button("Save Personal Cadence Goal") {
                            motionManager.updatePersonalizedCadenceGoal(
                                texture: meal.texture,
                                minimumMedianIntervalSec: cadenceGoalDraft
                            )
                            calibrationMessage = "Personal cadence goal saved."
                        }
                        .buttonStyle(.bordered)
                    }
                } else {
                    Button {
                        showCalibrationConfirmation = true
                    } label: {
                        Label("Verify as Calibration Meal", systemImage: "person.crop.circle.badge.checkmark")
                    }
                    .buttonStyle(.borderedProminent)
                }
            } else {
                Text("Calibration is unavailable because this session was not produced by a promoted individual-chew model.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if let calibrationMessage {
                Text(calibrationMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text("Cadence guidance is a personal wellness aid, not medical advice.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(14)
        .background(.gray.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
    }

    private var calibrationProgressText: String {
        guard let progress = calibrationProgress else { return "Calibration meal saved" }
        if progress.cadenceGoalActivated {
            return "Cadence baseline active (\(progress.verifiedMealCount) verified meals)"
        }
        return "Calibration saved (\(progress.verifiedMealCount) of 3 meals)"
    }

    private func loadMealSummary() {
        mealSummary = StoredMealSummary.load(from: sessionURL)
        isVerifiedCalibration = CompletedMealCalibration.isVerified(at: sessionURL)
        if let mealSummary {
            calibrationProgress = motionManager.calibrationProgress(for: mealSummary.texture)
            cadenceGoalDraft = motionManager.personalizedGoals(for: mealSummary.texture)?
                .minimumMedianInterChewIntervalSec ?? mealSummary.medianInterval ?? 0.6
        }
    }

    private func verifyCalibrationMeal() {
        do {
            let receipt = try motionManager.verifyCompletedCalibrationMeal(at: sessionURL)
            calibrationProgress = CalibrationProgress(
                texture: receipt.texture,
                verifiedMealCount: receipt.verifiedMealCount,
                cadenceGoalActivated: receipt.cadenceGoalActivated
            )
            isVerifiedCalibration = true
            cadenceGoalDraft = motionManager.personalizedGoals(for: receipt.texture)?
                .minimumMedianInterChewIntervalSec ?? cadenceGoalDraft
            calibrationMessage = receipt.cadenceGoalActivated
                ? "Your editable cadence goal now starts from your personal median."
                : "Verify \(max(0, 3 - receipt.verifiedMealCount)) more meal(s) of this texture to activate coaching."
        } catch {
            calibrationMessage = error.localizedDescription
        }
    }
}

private struct StoredMealSummary {
    let texture: MealTexture
    let detectedChews: Int
    let medianInterval: Double?
    let intervalP10: Double?
    let intervalP90: Double?
    let confidenceCoverage: Double
    let uncertainTimeSec: Double
    let chewEventModelPromoted: Bool

    static func load(from sessionURL: URL) -> StoredMealSummary? {
        let url = sessionURL.appendingPathComponent("meal_metrics.json")
        guard let data = try? Data(contentsOf: url),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let textureRaw = payload["texture"] as? String,
              let texture = MealTexture(rawValue: textureRaw) else { return nil }
        let distribution = payload["cadence_distribution_sec"] as? [String: Any]
        return StoredMealSummary(
            texture: texture,
            detectedChews: (payload["detected_chews"] as? NSNumber)?.intValue ?? 0,
            medianInterval: payload["median_inter_chew_interval_sec"] as? Double,
            intervalP10: distribution?["p10"] as? Double,
            intervalP90: distribution?["p90"] as? Double,
            confidenceCoverage: payload["confidence_coverage"] as? Double ?? 0,
            uncertainTimeSec: payload["time_excluded_uncertain_sec"] as? Double ?? 0,
            chewEventModelPromoted: payload["chew_event_model_promoted"] as? Bool ?? false
        )
    }
}

private struct SessionBadge: View {
    let text: String
    let systemImage: String

    var body: some View {
        Label(text, systemImage: systemImage)
            .font(.caption2)
            .labelStyle(.titleAndIcon)
            .foregroundStyle(.secondary)
            .lineLimit(1)
    }
}

// MARK: - Probability View

struct ProbabilityView: View {
    let latestProb: Double
    let smoothedProb: Double
    let isChewing: Bool
    let highThreshold: Double
    let lowThreshold: Double
    
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Probability bars
            HStack {
                Text("Raw:")
                    .frame(width: 60, alignment: .leading)
                ProbabilityBar(value: latestProb, high: highThreshold, low: lowThreshold)
                Text(String(format: "%.2f", latestProb))
                    .font(.system(.body, design: .monospaced))
                    .frame(width: 50)
            }
            
            HStack {
                Text("Smooth:")
                    .frame(width: 60, alignment: .leading)
                ProbabilityBar(value: smoothedProb, high: highThreshold, low: lowThreshold)
                Text(String(format: "%.2f", smoothedProb))
                    .font(.system(.body, design: .monospaced))
                    .frame(width: 50)
            }
            
            // Chewing state indicator
            HStack {
                Circle()
                    .fill(isChewing ? Color.orange : Color.green)
                    .frame(width: 20, height: 20)
                
                Text(isChewing ? "CHEWING" : "Idle")
                    .font(.title3)
                    .fontWeight(isChewing ? .bold : .regular)
                    .foregroundColor(isChewing ? .orange : .secondary)
            }
            .padding(.top, 4)
        }
    }
}

// MARK: - Probability Bar

struct ProbabilityBar: View {
    let value: Double
    let high: Double
    let low: Double
    
    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                // Background with threshold zones
                HStack(spacing: 0) {
                    Rectangle()
                        .fill(Color.green.opacity(0.2))
                        .frame(width: geo.size.width * CGFloat(low))
                    
                    Rectangle()
                        .fill(Color.yellow.opacity(0.2))
                        .frame(width: geo.size.width * CGFloat(high - low))
                    
                    Rectangle()
                        .fill(Color.orange.opacity(0.2))
                }
                
                // Value bar
                Rectangle()
                    .fill(barColor)
                    .frame(width: geo.size.width * CGFloat(min(value, 1.0)))
            }
            .cornerRadius(4)
        }
        .frame(height: 20)
    }
    
    var barColor: Color {
        if value >= high {
            return .orange
        } else if value >= low {
            return .yellow
        } else {
            return .green
        }
    }
}

// MARK: - Share Sheet

#if os(iOS)
struct ShareSheet: UIViewControllerRepresentable {
    let activityItems: [Any]
    
    func makeUIViewController(context: Context) -> UIActivityViewController {
        let vc = UIActivityViewController(activityItems: activityItems, applicationActivities: nil)
        return vc
    }
    
    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
#elseif os(macOS)
struct ShareSheet: View {
    let activityItems: [Any]
    
    var body: some View {
        VStack {
            Text("Share not implemented on macOS")
                .padding()
            
            if let url = activityItems.first as? URL {
                Text("Log file: \(url.path)")
                    .font(.caption)
                    .foregroundColor(.secondary)
                
                Button("Open in Finder") {
                    NSWorkspace.shared.selectFile(url.path, inFileViewerRootedAtPath: "")
                }
                .padding()
            }
        }
    }
}
#endif

#Preview {
    MainContentView()
}
