import SwiftUI

/// Top HUD: session title, dirty/saved indicator, chew counter, action buttons.
struct HUDTop: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    let toggleSidebar: () -> Void
    let toggleHelp: () -> Void
    let toggleReviewQueue: () -> Void
    let toggleSections: () -> Void
    let toggleAssist: () -> Void
    let toggleSignals: () -> Void

    @State private var showExperiment = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Button(action: toggleSidebar) { Image(systemName: "sidebar.left") }
                    .help("Sessions (⌘1)")
                saveDot
                VStack(alignment: .leading, spacing: 2) {
                    Text(state.selectedSession?.id ?? "Choose a recording")
                        .font(.headline).lineLimit(1).truncationMode(.middle)
                    Text(state.isBlindReview ? "Independent benchmark · predictions hidden" : "Assisted review · automatic proposals")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Text("\(state.verifiedCount)/\(state.candidateChewCount) reviewed")
                    .font(.caption.monospacedDigit()).fixedSize()
                Button("Save", systemImage: "square.and.arrow.down") { state.saveOverrides() }
                    .disabled(state.selectedSession == nil)
                    .fixedSize()
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    Button("Chewing spans", systemImage: "wand.and.stars") { toggleAssist() }
                        .disabled(state.isBlindReview)
                        .help("Review video + motion chewing-span proposals")
                    Button("Sections", systemImage: "list.bullet") { toggleSections() }
                    Button("Review chews", systemImage: "checklist") { toggleReviewQueue() }
                    Button(state.isBlindReview ? "Benchmark tools" : "Review tools", systemImage: "timer") { showExperiment.toggle() }
                        .popover(isPresented: $showExperiment) { ExperimentReviewPanel() }
                    Button(state.overrides.fullVideoPassCompleted ? "Full pass checked" : "Full pass", systemImage: "film") {
                        state.toggleFullVideoPassCompleted()
                    }
                    Button(state.overrides.verified ? "Verified" : "Verify", systemImage: "checkmark.seal") { state.toggleVerified() }
                        .disabled(!state.overrides.verified && !state.canFinalizeReview)
                        .help(state.completionStatus)
                    Menu {
                        Button("Rotate video right") { playback.rotateDisplay(by: 1) }
                        Button("Rotate video left") { playback.rotateDisplay(by: -1) }
                        Divider()
                        Button("Show / hide signal charts") { toggleSignals() }.disabled(state.isBlindReview)
                        Button("Show / hide annotated video") { state.useAnnotatedVideo.toggle() }
                            .disabled(state.isBlindReview || state.selectedSession?.annotatedVideoURL == nil)
                        Button("Help") { toggleHelp() }
                    } label: { Label("More", systemImage: "ellipsis.circle") }
                }
                .fixedSize(horizontal: true, vertical: false)
            }
            Text(state.statusMessage).font(.caption).foregroundStyle(.secondary).lineLimit(1)
        }
        .controlSize(.small)
        .padding(12)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
    }

    private var saveDot: some View {
        Circle()
            .fill(state.isDirty ? Color.orange : Color.green)
            .frame(width: 8, height: 8)
    }

    private var counterPill: some View {
        let total = state.candidateChewCount
        let idx = (state.selectedChewIndex ?? -1) + 1
        return Text(idx > 0 ? "\(idx) / \(total)" : "0 / \(total)")
            .font(.system(.callout, design: .monospaced).monospacedDigit())
            .foregroundStyle(.white)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(Color.white.opacity(0.12), in: Capsule())
    }

    private var verifiedPill: some View {
        HStack(spacing: 4) {
            Image(systemName: "checkmark.seal.fill").foregroundStyle(.green)
            Text("\(state.verifiedCount) / \(state.candidateChewCount)")
                .font(.system(.callout, design: .monospaced).monospacedDigit())
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 10).padding(.vertical, 4)
        .background(Color.green.opacity(0.18), in: Capsule())
        .help("Chew candidates with a review decision")
    }

    private var statsBadge: some View {
        let s = state.stats
        return HStack(spacing: 6) {
            statBadge("V", s.visible, .gray)
            statBadge("A", s.added, .blue)
            statBadge("E", s.edited, .orange)
            statBadge("R", s.rejected, .red)
            statBadge("X", s.excluded, .purple)
        }
        .font(.system(.caption2, design: .monospaced).monospacedDigit())
    }

    private func statBadge(_ letter: String, _ count: Int, _ color: Color) -> some View {
        HStack(spacing: 3) {
            Text(letter).foregroundStyle(color)
            Text("\(count)").foregroundStyle(.white)
        }
        .padding(.horizontal, 6).padding(.vertical, 2)
        .background(Color.white.opacity(0.08), in: Capsule())
    }
}


private struct ExperimentReviewPanel: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @State private var start = 0.0
    @State private var end = 0.0
    @State private var disposition = "uncertain"

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(state.isBlindReview ? "Independent blind annotation" : "Assisted annotation").font(.headline)
            Text("Count each chew at its closed end. Mark invisible or uncertain ranges before completing the full pass.")
                .font(.caption).fixedSize(horizontal: false, vertical: true)
            Button(state.reviewTimerRunning ? "Pause labeling timer" : "Start labeling timer") { state.toggleReviewTimer() }
            Text("Saved active time: \(Int(state.overrides.reviewContext?.activeSeconds ?? 0)) s").font(.caption)
            HStack {
                TextField("Start seconds", value: $start, format: .number).frame(width: 90)
                Button("Use playhead") { start = playback.currentTime }
            }
            HStack {
                TextField("End seconds", value: $end, format: .number).frame(width: 90)
                Button("Use playhead") { end = playback.currentTime }
            }
            Picker("Exclude as", selection: $disposition) {
                Text("Uncertain").tag("uncertain")
                Text("Unobservable").tag("unobservable")
            }
            Button("Exclude range") { state.excludeSpan(start: start, end: end, disposition: disposition) }
            ForEach(Array((state.overrides.reviewContext?.excludedSpans ?? []).enumerated()), id: \.offset) { _, span in
                Text(String(format: "%.2f–%.2f s: %@", span.startTS, span.endTS, span.disposition)).font(.caption)
            }
            Button("Remove last exclusion") { state.removeLastExclusion() }
            Toggle("Beginning and end sync markers checked", isOn: Binding(
                get: { state.overrides.reviewContext?.syncVerified ?? false },
                set: { state.confirmSynchronization($0) }
            ))
            Text("Leave unchecked if timing is uncertain. Visual reviews can still be saved; motion training targets stay masked. For blind review, check alignment only after the independent annotation pass.")
                .font(.caption).fixedSize(horizontal: false, vertical: true)
            Button("Save review draft") { state.saveOverrides() }
                .keyboardShortcut("s", modifiers: .command)
            Text(state.statusMessage).font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding().frame(width: 380)
    }
}
