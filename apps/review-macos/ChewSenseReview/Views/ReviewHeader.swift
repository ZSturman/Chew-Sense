import SwiftUI

/// One row above the video: back to Up next, the recording, where you are in
/// its review, save state, and a menu for everything else.
struct ReviewHeader: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @Environment(\.openWindow) private var openWindow
    let plan: GuidedPlan
    let step: GuidedStep
    let isComplete: (GuidedStep) -> Bool
    let toggleSidebar: () -> Void
    let toggleHelp: () -> Void
    let toggleSignals: () -> Void

    var body: some View {
        HStack(spacing: 14) {
            Button { state.closeSession() } label: { Label("Up next", systemImage: "chevron.left") }
                .buttonStyle(.borderless)
                .help("Back to Up next (⌘0)")
                .accessibilityIdentifier("backToUpNext")
            VStack(alignment: .leading, spacing: 1) {
                Text(RecordingName.of(state.selectedSession?.id ?? ""))
                    .font(.headline).lineLimit(1).truncationMode(.middle)
                Text(state.retirement != nil ? "Retired · kept for research"
                     : state.isBlindReview ? "Blind review · suggestions hidden" : "Suggestions shown")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .help(technicalDetails)
            Spacer(minLength: 12)
            if state.candidateIntegrityError == nil, state.retirement == nil {
                StepPills(plan: plan, current: step, blind: state.isBlindReview,
                          isComplete: isComplete, select: { state.chosenStep = $0 })
            }
            Spacer(minLength: 12)
            saveState
            Menu {
                Button("All recordings", action: toggleSidebar)
                Button("Help", action: toggleHelp)
                Divider()
                Button("Save now") { state.saveOverrides() }
                Divider()
                Button("Show or hide signal charts", action: toggleSignals)
                    .disabled(state.isBlindReview)
                Button("Show or hide annotated video") { state.useAnnotatedVideo.toggle() }
                    .disabled(state.isBlindReview || state.selectedSession?.annotatedVideoURL == nil)
                Button("Rotate video right") { playback.rotateDisplay(by: 1) }
                Button("Rotate video left") { playback.rotateDisplay(by: -1) }
                Divider()
                Button("Pipeline") { openWindow(id: "pipeline") }
                Button("Training and evaluation") { openWindow(id: "training") }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .help("More")
        }
        .padding(.horizontal, 4)
    }

    @ViewBuilder
    private var saveState: some View {
        if state.isDirty {
            Text("Saving…").font(.caption).foregroundStyle(.secondary)
        } else if state.retirement != nil {
            Label("Retired", systemImage: "archivebox").font(.caption).foregroundStyle(.secondary)
        } else if state.overrides.verified {
            Label("Finished", systemImage: "checkmark.seal.fill").font(.caption).foregroundStyle(.green)
        } else if state.candidateIntegrityError == nil {
            Label("Saved", systemImage: "checkmark").font(.caption).foregroundStyle(.secondary)
                .help("Your decisions save automatically. Close the app any time and continue later.")
        }
    }

    private var technicalDetails: String {
        guard let session = state.selectedSession else { return "" }
        var lines = [session.id]
        if let candidates = state.candidates {
            lines.append("Suggestions revision v\(String(format: "%04d", candidates.candidateRevision))")
        }
        lines.append(session.rawDir.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().path)
        return lines.joined(separator: "\n")
    }
}

/// Optional tools under "More options": jump to a section, mark unclear parts,
/// confirm the timing nods, and the labelling timer.
struct ReviewToolsSection: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @EnvironmentObject var viewport: TimelineViewport
    @State private var start = 0.0
    @State private var end = 0.0
    @State private var disposition = "uncertain"

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            if !state.reviewSections.isEmpty { sections }
            unclearRanges
            timing
            timer
        }
        .font(.callout)
        .toggleStyle(.checkbox)
    }

    // MARK: Sections

    private var sections: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Jump to a section").font(.headline)
            Text(state.isBlindReview
                 ? "Requested actions only help you find your way; check what actually happens in the video."
                 : "Blue: your phone labels. Purple: what the recording asked you to do (a hint, never a label).")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Toggle("Phone labels", isOn: $state.showPhoneSections)
            Toggle("Requested actions", isOn: $state.showInstructionSections)
            Toggle("Chewing sections only", isOn: $state.chewingSectionsOnly)
            Button("Whole recording") {
                state.focusedSectionID = nil
                playback.clearLoop()
                viewport.fit()
            }
            .buttonStyle(.link)
            ForEach(state.visibleReviewSections) { section in
                Button {
                    playback.pause()
                    state.selectChew(id: nil)
                    state.focusedSectionID = section.id
                    playback.setLoop(start: section.start, end: section.end, enabled: false)
                    playback.seek(to: section.start)
                    viewport.center = (section.start + section.end) / 2
                    viewport.zoom = max(1, playback.duration / max(1, section.end - section.start + 1))
                } label: {
                    HStack {
                        Circle().fill(section.source == .phone ? Color.blue : Color.purple).frame(width: 7, height: 7)
                        Text(section.title).lineLimit(1)
                        Spacer()
                        Text(String(format: "%.0f–%.0f s", section.start, section.end))
                            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2).padding(.horizontal, 4)
                    .background(state.focusedSectionID == section.id ? Color.accentColor.opacity(0.15) : .clear,
                                in: RoundedRectangle(cornerRadius: 4))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            if state.focusedReviewSection != nil {
                Text("Check suggestions now shows this section only. Others stay pending.")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if !state.isBlindReview {
                    Button("Accept the suggestions I checked in this section") { state.acceptReviewedSectionProposals() }
                        .disabled(state.reviewQueueChews.allSatisfy { state.disposition(for: $0.id) != nil })
                }
            }
        }
    }

    // MARK: Unclear parts

    private var unclearRanges: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Mark a part as unclear").font(.headline)
            Text("For stretches where you can't see the mouth or can't tell what happens. They're left out of training.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                TextField("From (s)", value: $start, format: .number).frame(width: 70)
                Button("Use playhead") { start = playback.currentTime }.buttonStyle(.link)
            }
            HStack {
                TextField("To (s)", value: $end, format: .number).frame(width: 70)
                Button("Use playhead") { end = playback.currentTime }.buttonStyle(.link)
            }
            Picker("Reason", selection: $disposition) {
                Text("Unclear").tag("uncertain")
                Text("Can't see").tag("unobservable")
            }
            .fixedSize()
            Button("Mark as unclear") { state.excludeSpan(start: start, end: end, disposition: disposition) }
            ForEach(Array((state.overrides.reviewContext?.excludedSpans ?? []).enumerated()), id: \.offset) { _, span in
                Text(String(format: "%.2f–%.2f s · %@", span.startTS, span.endTS,
                            span.disposition == "unobservable" ? "can't see" : "unclear"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if state.overrides.reviewContext?.excludedSpans.isEmpty == false {
                Button("Remove the last one") { state.removeLastExclusion() }.buttonStyle(.link)
            }
        }
    }

    // MARK: Timing nods

    private var timing: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Timing nods").font(.headline)
            Toggle("The nods at the start and end line up with the motion", isOn: Binding(
                get: { state.overrides.reviewContext?.syncVerified ?? false },
                set: { state.confirmSynchronization($0) }
            ))
            Text(state.isBlindReview
                 ? "Optional, and only after you've marked the chews. Leave it unchecked if unsure; the review still counts."
                 : "Optional. Leave it unchecked if unsure; the review still counts, but AirPods training waits for confirmed timing.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Timer

    private var timer: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Labelling timer").font(.headline)
            HStack {
                Button(state.reviewTimerRunning ? "Pause timer" : "Start timer") { state.toggleReviewTimer() }
                Text("\(Int(state.overrides.reviewContext?.activeSeconds ?? 0)) s saved")
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
        }
    }
}
