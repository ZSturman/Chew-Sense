import SwiftUI

/// "Check suggestions" step: one suggested chew at a time. The chew loops in the
/// main video with at least five frames of context; the reviewer accepts, fixes,
/// rejects or marks it unclear, and the next pending suggestion follows. Food
/// entries come after the chews. Every decision stays revisitable from the list.
struct ChewDecisionCard: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @EnvironmentObject var viewport: TimelineViewport

    @State private var showTiming = false
    @State private var showAll = false
    @AppStorage(ChewCue.loopKey) private var loopClips = true

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let chew = currentChew {
                chewCard(chew)
            } else if let entry = currentFoodEntry {
                foodEntryCard(entry)
            } else {
                allDecided
            }
            DisclosureGroup("All suggestions (\(suggestedChews.count))", isExpanded: $showAll) { allList }
                .font(.callout)
        }
        .onAppear { cueIfNeeded() }
        .onChange(of: state.focusedSectionID) { _, _ in cueIfNeeded(force: true) }
    }

    // MARK: Chew

    private func chewCard(_ chew: ReviewChew) -> some View {
        let decision = state.disposition(for: chew.id)
        let total = state.candidates?.chews.count ?? 0
        let remaining = state.pendingChews.count
        return VStack(alignment: .leading, spacing: 14) {
            StepHeading(number: 1, title: "Check each suggestion",
                        detail: "Watch the clip. A chew is one close → open → close of the jaw.")
            VStack(alignment: .leading, spacing: 4) {
                ProgressView(value: Double(total - remaining), total: Double(max(total, 1)))
                Text(remaining == 0 ? "All \(total) suggestions decided" : "\(remaining) of \(total) left")
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
            if let context = instructionContext(for: chew) {
                Label("Requested here: \(context)", systemImage: "text.bubble")
                    .font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .help("What the recording asked you to do at this time. It's a hint, not a label.")
            }
            if let decision {
                Label("Your answer: \(answerText(decision))", systemImage: decisionIcon(decision))
                    .foregroundStyle(decisionColor(decision))
            }
            Text("Is this a real chew?").font(.headline)
            clipControls(replay: { cue(chew) },
                         toggle: { ChewCue.toggle(chew, state: state, playback: playback, viewport: viewport) })
            Button { acceptAndAdvance(chew) } label: {
                Label("Yes, it's a chew", systemImage: "checkmark").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent).controlSize(.large)
            .keyboardShortcut(.return, modifiers: [])
            .help("Accept and go to the next suggestion (Return or V)")
            .accessibilityIdentifier("acceptChew")
            HStack {
                Button { reject(chew) } label: { Text("Not a chew").frame(maxWidth: .infinity) }
                    .accessibilityIdentifier("rejectChew")
                Menu {
                    Button("Unclear: the video is ambiguous") { state.markChewUncertain(id: chew.id); advance(from: chew) }
                    Button("Can't see: face or mouth hidden") { state.markChewUnobservable(id: chew.id); advance(from: chew) }
                } label: { Text("Can't tell") }
                .help("Unclear chews are left out of timing training; nothing is guessed.")
            }
            .controlSize(.large)
            HStack {
                Button { split(chew) } label: {
                    Label("Split in two here", systemImage: "scissors").frame(maxWidth: .infinity)
                }
                .disabled(!canSplit(chew))
                .help("For one suggestion that is really two chews: pause where the mouth closes between them, then split. Both chews are kept.")
                .accessibilityIdentifier("splitChew")
                Button { addMissed(keeping: chew) } label: {
                    Label("Add a chew here", systemImage: "plus").frame(maxWidth: .infinity)
                }
                .help("Add a missed chew at the paused video position, fitted between the chews around it (A)")
                .accessibilityIdentifier("addChewHere")
            }
            if !canSplit(chew) {
                Text("Two chews in one? Pause where the mouth closes between them, then split.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            DisclosureGroup("Fix the timing", isExpanded: $showTiming) { timingEditor(chew) }
                .font(.callout)
            HStack {
                Button("Previous") { step(by: -1, from: chew) }
                Spacer()
                if decision != nil {
                    Button("Mark as not decided") { state.revertChewToAuto(id: chew.id) }
                }
                Button("Skip") { step(by: 1, from: chew) }
            }
            .buttonStyle(.link).font(.callout)
        }
    }

    private func timingEditor(_ chew: ReviewChew) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Nudge a marker one frame at a time, or pause the video where it belongs and press Set. Changing the timing counts as a fix.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Grid(alignment: .leading, horizontalSpacing: 8, verticalSpacing: 6) {
                markerRow(.start, chew: chew, color: .green, key: "1")
                markerRow(.peak, chew: chew, color: .yellow, key: "2")
                markerRow(.end, chew: chew, color: .red, key: "3")
            }
            Text("Shortcuts: hold S, P or E and press ← / → to nudge.")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .padding(.top, 6)
    }

    private func markerRow(_ marker: ChewMarker, chew: ReviewChew, color: Color, key: String) -> some View {
        let time = chew.timestamp(for: marker)
        return GridRow {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(markerName(marker)).font(.callout)
            Text(String(format: "%.3f s", time)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            HStack(spacing: 2) {
                Button { nudge(chew, marker, by: -1.0 / 30.0) } label: { Image(systemName: "chevron.left") }
                    .help("One frame earlier")
                Button { nudge(chew, marker, by: 1.0 / 30.0) } label: { Image(systemName: "chevron.right") }
                    .help("One frame later")
                Button("Set") {
                    state.updateMarker(chewID: chew.id, marker: marker, to: playback.currentTime)
                }
                .help("Move to the paused video position (\(key))")
                Button { playback.pause(); playback.clearLoop(); playback.seek(to: time) } label: { Image(systemName: "scope") }
                    .help("Show this frame")
            }
            .buttonStyle(.borderless)
        }
    }

    // MARK: Food entry

    private func foodEntryCard(_ entry: ReviewFoodEntry) -> some View {
        let total = state.candidates?.foodEntries.count ?? 0
        return VStack(alignment: .leading, spacing: 14) {
            StepHeading(number: 1, title: "Check food entries",
                        detail: "The detector thinks food went into the mouth here. Watch the two-second loop.")
            Text("\(state.pendingFoodEntries.count) of \(total) left")
                .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            Text("Did food go into the mouth?").font(.headline)
            clipControls(replay: { cue(entry) },
                         toggle: { ChewCue.toggle(entry, state: state, playback: playback) })
            Button {
                if state.foodEntryDisposition(for: entry.id) == nil { state.acceptBite(id: entry.id) }
                cueNext()
            } label: { Label("Yes", systemImage: "checkmark").frame(maxWidth: .infinity) }
            .buttonStyle(.borderedProminent).controlSize(.large)
            .keyboardShortcut(.return, modifiers: [])
            HStack {
                Button { state.rejectBite(id: entry.id); cueNext() } label: { Text("No").frame(maxWidth: .infinity) }
                Menu {
                    Button("Unclear: the video is ambiguous") { state.markFoodEntryUncertain(id: entry.id); cueNext() }
                    Button("Can't see: face or mouth hidden") { state.markFoodEntryUnobservable(id: entry.id); cueNext() }
                } label: { Text("Can't tell") }
            }
            .controlSize(.large)
            Button("Move it to the paused video position") { state.moveBite(id: entry.id, to: playback.currentTime) }
                .buttonStyle(.link).font(.callout)
        }
    }

    private var allDecided: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Every suggestion is decided", systemImage: "checkmark.circle.fill")
                .font(.title3.weight(.semibold)).foregroundStyle(.green)
            if state.focusedReviewSection != nil {
                Text("This covers the section you picked. Choose Whole recording under More options to see the rest.")
                    .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Button("Continue: watch for missed chews") { state.chosenStep = .finish }
                .buttonStyle(.borderedProminent).controlSize(.large)
        }
    }

    // MARK: All suggestions

    private var allList: some View {
        LazyVStack(alignment: .leading, spacing: 2) {
            ForEach(Array(suggestedChews.enumerated()), id: \.element.id) { index, chew in
                let decision = state.disposition(for: chew.id)
                Button { cue(chew) } label: {
                    HStack {
                        Image(systemName: decision.map(decisionIcon) ?? "circle")
                            .foregroundStyle(decision.map(decisionColor) ?? .secondary)
                        Text("Chew \(index + 1)")
                        Spacer()
                        Text(String(format: "%.1f s", chew.startTS)).monospacedDigit().foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 3).padding(.horizontal, 6)
                    .background(chew.id == state.selectedChewID ? Color.accentColor.opacity(0.15) : .clear,
                                in: RoundedRectangle(cornerRadius: 5))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            ForEach(state.bites) { entry in
                let decision = state.foodEntryDisposition(for: entry.id)
                Button { cue(entry) } label: {
                    HStack {
                        Image(systemName: decision.map(decisionIcon) ?? "circle")
                            .foregroundStyle(decision.map(decisionColor) ?? .secondary)
                        Text("Food entry")
                        Spacer()
                        Text(String(format: "%.1f s", entry.ts)).monospacedDigit().foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 3).padding(.horizontal, 6)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            Button("Add a missed food entry at the playhead") { state.addBite(at: playback.currentTime) }
                .buttonStyle(.link).padding(.top, 6)
        }
        .padding(.top, 6)
    }

    // MARK: Selection and playback

    /// Suggested chews in the reviewed section (or the whole recording), excluding the reviewer's own additions.
    private var suggestedChews: [ReviewChew] {
        let ids = Set(state.candidates?.chews.map(\.id) ?? [])
        return state.reviewQueueChews.filter { ids.contains($0.id) }
    }

    private var pendingInQueue: [ReviewChew] {
        suggestedChews.filter { state.disposition(for: $0.id) == nil }
    }

    /// The selected suggestion, if any; otherwise the first pending one.
    private var currentChew: ReviewChew? {
        if state.selectedBiteID != nil { return nil }
        if let selected = state.selectedChew, suggestedChews.contains(where: { $0.id == selected.id }) { return selected }
        return pendingInQueue.first
    }

    private var currentFoodEntry: ReviewFoodEntry? {
        if let selected = state.selectedBite { return selected }
        return pendingInQueue.isEmpty ? state.pendingFoodEntries.first : nil
    }

    private func cueIfNeeded(force: Bool = false) {
        if !force, let selected = state.selectedChew, state.disposition(for: selected.id) == nil,
           suggestedChews.contains(where: { $0.id == selected.id }) {
            ChewCue.loop(selected, state: state, playback: playback, viewport: viewport)
            return
        }
        cueNext()
    }

    /// Next pending chew (or food entry) after the current one, wrapping around.
    private func cueNext(after chew: ReviewChew? = nil) {
        let pending = pendingInQueue
        if let chew, let index = suggestedChews.firstIndex(where: { $0.id == chew.id }),
           let next = suggestedChews[(index + 1)...].first(where: { state.disposition(for: $0.id) == nil }) ?? pending.first {
            cue(next)
        } else if let next = pending.first {
            cue(next)
        } else if let entry = state.pendingFoodEntries.first {
            cue(entry)
        } else {
            state.selectChew(id: nil)
            state.selectBite(id: nil)
            playback.pause()
            playback.clearLoop()
        }
    }

    private func cue(_ chew: ReviewChew) {
        ChewCue.loop(chew, state: state, playback: playback, viewport: viewport)
    }

    private func cue(_ entry: ReviewFoodEntry) {
        ChewCue.loop(entry, state: state, playback: playback)
    }

    /// Replay from the clip's start, pause/resume and loop on/off, right above the answers.
    @ViewBuilder
    private func clipControls(replay: @escaping () -> Void, toggle: @escaping () -> Void) -> some View {
        HStack {
            Button(action: replay) {
                Label("Replay", systemImage: "arrow.counterclockwise").frame(maxWidth: .infinity)
            }
            .help("Play this clip again from its start (R)")
            .accessibilityIdentifier("replayClip")
            Button(action: toggle) {
                Label(playback.isPlaying ? "Pause" : "Play",
                      systemImage: playback.isPlaying ? "pause.fill" : "play.fill")
                    .frame(maxWidth: .infinity)
            }
            .help("Pause or resume (Space)")
            .accessibilityIdentifier("toggleClip")
            Toggle(isOn: $loopClips) {
                Label("Loop", systemImage: "repeat")
            }
            .toggleStyle(.button)
            .help(loopClips ? "Looping: the clip repeats until you answer. Click to play each clip once and pause."
                            : "Plays once and pauses at the end. Click to loop the clip until you answer.")
            .accessibilityIdentifier("loopClips")
        }
        .controlSize(.large)
        .onChange(of: loopClips) { _, loop in playback.setLooping(loop) }
        SpeedControl()
    }

    private func acceptAndAdvance(_ chew: ReviewChew) {
        // Keep a fix or an earlier acceptance; only change undecided or negative answers.
        switch state.disposition(for: chew.id) {
        case .accepted, .edited, .added: break
        default: state.acceptChew(id: chew.id)
        }
        advance(from: chew)
    }

    private func canSplit(_ chew: ReviewChew) -> Bool {
        let t = playback.currentTime
        return !playback.isPlaying && t - chew.startTS >= 0.05 && chew.endTS - t >= 0.05
    }

    private func split(_ chew: ReviewChew) {
        if state.splitChew(id: chew.id, at: playback.currentTime) {
            ChewCue.loop(state.chews.first { $0.id == chew.id } ?? chew, state: state, playback: playback, viewport: viewport)
        }
    }

    private func addMissed(keeping chew: ReviewChew) {
        playback.pause()
        if state.addChewFitting(at: playback.currentTime) { state.selectChew(id: chew.id) }
    }

    private func reject(_ chew: ReviewChew) {
        state.selectChew(id: chew.id)
        state.deleteSelectedOrAt(videoTime: chew.peakTS)
        advance(from: chew)
    }

    private func advance(from chew: ReviewChew) {
        showTiming = false
        cueNext(after: chew)
    }

    private func step(by offset: Int, from chew: ReviewChew) {
        let list = suggestedChews
        guard let index = list.firstIndex(where: { $0.id == chew.id }) else { return }
        let target = index + offset
        guard list.indices.contains(target) else { return }
        cue(list[target])
    }

    private func nudge(_ chew: ReviewChew, _ marker: ChewMarker, by delta: Double) {
        let target = chew.timestamp(for: marker) + delta
        state.updateMarker(chewID: chew.id, marker: marker, to: target)
        playback.pause()
        playback.clearLoop()
        playback.seek(to: state.chews.first { $0.id == chew.id }?.timestamp(for: marker) ?? target)
    }

    // MARK: Wording

    private func instructionContext(for chew: ReviewChew) -> String? {
        state.reviewSections.first {
            $0.source == .instruction && $0.start <= chew.peakTS && chew.peakTS < $0.end
        }?.title
    }

    private func markerName(_ marker: ChewMarker) -> String {
        switch marker {
        case .start: "Closed (start)"
        case .peak: "Widest open"
        case .end: "Closed (end)"
        }
    }

    private func answerText(_ decision: ReviewDisposition) -> String {
        switch decision {
        case .accepted: "a chew"
        case .edited: "a chew, timing fixed"
        case .added: "added by you"
        case .rejected: "not a chew"
        case .uncertain: "unclear"
        case .unobservable: "can't see"
        }
    }

    private func decisionIcon(_ decision: ReviewDisposition) -> String {
        switch decision {
        case .accepted, .added: "checkmark.circle.fill"
        case .edited: "slider.horizontal.3"
        case .rejected: "xmark.circle.fill"
        case .uncertain: "questionmark.circle.fill"
        case .unobservable: "eye.slash.fill"
        }
    }

    private func decisionColor(_ decision: ReviewDisposition) -> Color {
        switch decision {
        case .accepted, .edited, .added: .green
        case .rejected: .red
        case .uncertain, .unobservable: .purple
        }
    }
}

/// The clip for one suggestion: a chew with at least five frames of
/// context, or two seconds around a food entry.
@MainActor
enum ChewCue {
    /// Reviewer preference: loop each clip until answered (default) or play it once and pause.
    static let loopKey = "ChewSenseReview.loopSuggestions"
    static var loops: Bool { UserDefaults.standard.object(forKey: loopKey) as? Bool ?? true }

    static func window(_ chew: ReviewChew, state: ReviewerState, playback: PlaybackController) -> (start: Double, end: Double) {
        let context = max(state.loopPadding, 5.0 / max(state.chewCycles?.fps ?? 30.0, 1.0))
        let end = playback.duration > 0 ? min(playback.duration, chew.endTS + context) : chew.endTS + context
        return (max(0, chew.startTS - context), end)
    }

    static func window(_ entry: ReviewFoodEntry, playback: PlaybackController) -> (start: Double, end: Double) {
        let end = playback.duration > 0 ? min(playback.duration, entry.ts + 1.0) : entry.ts + 1.0
        return (max(0, entry.ts - 1.0), end)
    }

    /// Select, zoom the timeline to it, and play from the start (looping if the reviewer chose to).
    static func loop(_ chew: ReviewChew, state: ReviewerState, playback: PlaybackController, viewport: TimelineViewport) {
        state.selectChew(id: chew.id)
        viewport.focus(on: chew, duration: max(playback.duration, 1))
        let clip = window(chew, state: state, playback: playback)
        playback.playRange(start: clip.start, end: clip.end, loop: loops)
    }

    static func loop(_ entry: ReviewFoodEntry, state: ReviewerState, playback: PlaybackController) {
        state.selectBite(id: entry.id)
        let clip = window(entry, playback: playback)
        playback.playRange(start: clip.start, end: clip.end, loop: loops)
    }

    /// Pause, or resume where it paused (restarting the clip if it finished or the playhead left it).
    static func toggle(_ chew: ReviewChew, state: ReviewerState, playback: PlaybackController, viewport: TimelineViewport) {
        if playback.isPlaying { playback.pause(); return }
        guard state.selectedChewID == chew.id else { return loop(chew, state: state, playback: playback, viewport: viewport) }
        let clip = window(chew, state: state, playback: playback)
        playback.resumeOrRestart(start: clip.start, end: clip.end, loop: loops)
    }

    static func toggle(_ entry: ReviewFoodEntry, state: ReviewerState, playback: PlaybackController) {
        if playback.isPlaying { playback.pause(); return }
        guard state.selectedBiteID == entry.id else { return loop(entry, state: state, playback: playback) }
        let clip = window(entry, playback: playback)
        playback.resumeOrRestart(start: clip.start, end: clip.end, loop: loops)
    }
}
