import SwiftUI
import AppKit

// MARK: - Steps

/// One stage of reviewing a recording. Steps are derived from what is saved,
/// so a review resumes at the right place. They only guide: the validation
/// rules (every suggestion decided, one Finish confirmation) stay in ReviewerState.
enum GuidedStep: String, Identifiable, Equatable {
    /// Chewing spans: label flagged clips as chewing or not (assisted only).
    case spans
    /// Accept, fix or reject each suggested chew and food entry (assisted only).
    case chews
    /// Mark every chew with no suggestions shown (blind only).
    case mark
    /// Watch the whole recording, then confirm with Finish review.
    case finish
    case done

    var id: String { rawValue }

    func title(blind: Bool) -> String {
        switch self {
        case .spans: "Chewing spans"
        case .chews: "Check suggestions"
        case .mark: "Mark chews"
        case .finish: blind ? "Finish" : "Watch and finish"
        case .done: "Done"
        }
    }
}

struct GuidedPlan: Equatable {
    /// Steps shown in the header, in order.
    var steps: [GuidedStep]
    /// The step the reviewer should do next.
    var current: GuidedStep
}

extension ReviewerState {
    /// Suggested chews with no decision yet, in time order.
    var pendingChews: [ReviewChew] {
        let decided = Set(overrides.chewDecisions.map(\.candidateID))
        let candidateIDs = Set(candidates?.chews.map(\.id) ?? [])
        return chews.filter { candidateIDs.contains($0.id) && !decided.contains($0.id) }
    }

    var pendingFoodEntries: [ReviewFoodEntry] {
        let decided = Set(overrides.foodEntryDecisions.map(\.candidateID))
        let candidateIDs = Set(candidates?.foodEntries.map(\.id) ?? [])
        return bites.filter { candidateIDs.contains($0.id) && !decided.contains($0.id) }
    }

    var pendingSuggestionCount: Int { pendingChews.count + pendingFoodEntries.count }

    /// - Parameter spansPending: this recording has chewing-span proposals still to label.
    func guidedPlan(spansPending: Bool) -> GuidedPlan {
        if retirement != nil { return GuidedPlan(steps: [], current: .done) }
        if isBlindReview {
            return GuidedPlan(steps: [.mark, .finish], current: overrides.verified ? .done : .mark)
        }
        let spansRequired = candidates?.requiresFullCoarseReview == true
        let hasSuggestions = candidates.map { !$0.chews.isEmpty || !$0.foodEntries.isEmpty } ?? false
        var steps: [GuidedStep] = []
        if spansRequired { steps.append(.spans) }
        if hasSuggestions { steps.append(.chews) }
        steps.append(.finish)
        // Span labels are a separate file, so they can follow a finished chew review.
        if spansPending && !spansRequired { steps.append(.spans) }

        let current: GuidedStep
        if spansRequired && spansPending {
            current = .spans
        } else if overrides.verified {
            current = spansPending ? .spans : .done
        } else if pendingSuggestionCount > 0 {
            current = .chews
        } else {
            current = .finish
        }
        return GuidedPlan(steps: steps, current: current)
    }

    /// Whether a step's work is already saved (checkmarks in the header).
    func isComplete(_ step: GuidedStep, spansPending: Bool) -> Bool {
        switch step {
        case .spans: !spansPending
        case .chews: pendingSuggestionCount == 0
        case .mark, .finish, .done: overrides.verified
        }
    }

    /// Fraction of the recording played during this visit.
    func watchedFraction(duration: Double) -> Double {
        guard duration > 0 else { return 0 }
        return min(1, Double(watchedSeconds.count) / max(1, duration.rounded(.up)))
    }
}

// MARK: - Names and task progress

/// "Baseline 90s (Nov 2)" rather than a session ID. Mirrors `recording_name`
/// in loop/project_status.py for recordings without an experiment title.
enum RecordingName {
    static func of(_ id: String) -> String {
        var stem = id
        if let range = id.range(of: #"-\d{8}"#, options: .regularExpression) {
            stem = String(id[..<range.lowerBound])
        }
        if stem.hasPrefix("Guided-") { stem.removeFirst("Guided-".count) }
        stem = stem.replacingOccurrences(of: "-", with: " ").trimmingCharacters(in: .whitespaces)
        let title = stem.isEmpty ? id : stem.prefix(1).uppercased() + stem.dropFirst().lowercased()
        guard let range = id.range(of: #"\d{8}-\d{6}"#, options: .regularExpression) else { return title }
        let parser = DateFormatter()
        parser.locale = Locale(identifier: "en_US_POSIX")
        parser.dateFormat = "yyyyMMdd"
        guard let day = parser.date(from: String(id[range].prefix(8))) else { return title }
        let output = DateFormatter()
        output.locale = Locale(identifier: "en_US_POSIX")
        output.dateFormat = "MMM d"
        return "\(title) (\(output.string(from: day)))"
    }
}

/// Checks a shared next step against the files Review writes, so a task finished
/// here disappears at once instead of waiting for the Mac to refresh its status.
enum ReviewTaskProgress {
    static func isDoneLocally(_ step: ProjectStatus.Step, loopRoot: URL?) -> Bool {
        guard let loopRoot, let id = step.sessionId else { return false }
        let workspace = step.id.hasPrefix("blind-") ? "blind" : "assisted"
        let annotations = loopRoot.appending(path: "\(workspace)/sessions/raw/\(id)/annotations")
        if step.id.hasPrefix("spans-") {
            let names = (try? FileManager.default.contentsOfDirectory(atPath: annotations.path)) ?? []
            return names.contains { $0.hasPrefix("coarse-review-v") && $0.hasSuffix(".json") }
        }
        guard step.id.hasPrefix("blind-") || step.id.hasPrefix("assisted-") else { return false }
        if FileManager.default.fileExists(atPath: annotations.appending(path: "canonical_events.json").path) {
            return true
        }
        guard let draft = try? OverridePersistence.loadIfPresent(annotations.appending(path: "review-draft.json")) else {
            return false
        }
        return draft.verified && draft.fullVideoPassCompleted
    }

    /// Review steps from the shared status that still need doing here.
    static func openSteps(_ status: ProjectStatus?, loopRoot: URL?) -> [ProjectStatus.Step] {
        (status?.steps ?? []).filter { $0.app == "review" && !isDoneLocally($0, loopRoot: loopRoot) }
    }

    /// The workspace and step a shared next step opens; nil when it has no recording.
    static func destination(of step: ProjectStatus.Step) -> (id: String, workspace: String, step: GuidedStep?)? {
        guard case .session(let id, let workspace)? = ChewSenseLink.forStep(step).map({ $0.link }) else { return nil }
        return (id, workspace, step.id.hasPrefix("spans-") ? .spans : nil)
    }
}

// MARK: - Up next (home)

/// The first screen: the one review task that needs attention, a Start button,
/// and everything else one click away.
struct UpNextView: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var project: ProjectStatusStore
    @Environment(\.openWindow) private var openWindow
    let showRecordings: () -> Void
    @State private var startError: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                if let status = project.status {
                    content(status)
                } else {
                    noSharedStatus
                }
                Divider()
                HStack(spacing: 18) {
                    Button("All recordings", action: showRecordings)
                    Button("Pipeline") { openWindow(id: "pipeline") }
                    Button("Training and evaluation") { openWindow(id: "training") }
                }
                .buttonStyle(.link)
                .font(.callout)
                if let status = project.status {
                    DisclosureGroup("Project status") {
                        ProjectStatusCard(status: status, app: "review")
                            .padding(.top, 8)
                    }
                    .font(.callout)
                }
            }
            .frame(maxWidth: 560, alignment: .leading)
            .padding(.horizontal, 40).padding(.vertical, 48)
            .frame(maxWidth: .infinity)
        }
        .accessibilityIdentifier("upNext")
    }

    @ViewBuilder
    private func content(_ status: ProjectStatus) -> some View {
        let steps = ReviewTaskProgress.openSteps(status, loopRoot: state.loopRoot)
        StageStrip(stages: status.stages)
        if let next = steps.first {
            VStack(alignment: .leading, spacing: 12) {
                Text("Up next").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
                Text(next.title).font(.largeTitle.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                Text(next.detail).font(.title3).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button { start(next) } label: {
                    Label("Start", systemImage: "arrow.right").padding(.horizontal, 10)
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .keyboardShortcut(.defaultAction)
                .padding(.top, 4)
                .accessibilityIdentifier("startNextTask")
                if let startError {
                    Label(startError, systemImage: "exclamationmark.triangle")
                        .font(.callout).foregroundStyle(.orange)
                }
            }
            if steps.count > 1 {
                VStack(alignment: .leading, spacing: 10) {
                    Text("After that").font(.headline)
                    ForEach(steps.dropFirst()) { step in
                        HStack {
                            Text(step.title)
                            Spacer()
                            Button("Open") { start(step) }.buttonStyle(.link)
                        }
                        .font(.callout)
                    }
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 10) {
                Label("Nothing needs you in Review", systemImage: "checkmark.circle.fill")
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(.green)
                if let other = status.steps.first(where: { $0.app != "review" && $0.optional != true }) {
                    Text("Next: \(other.title)").font(.title3)
                    Text("\(ProjectStatus.place(of: other.app, in: "review")). \(other.detail)")
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// Without the loop service's status: continue drafts in this workspace, or pick one.
    @ViewBuilder
    private var noSharedStatus: some View {
        let inProgress = state.sessions.filter { $0.status == .inReview }
        VStack(alignment: .leading, spacing: 12) {
            Text(inProgress.isEmpty ? "Choose a recording to review" : "Continue where you left off")
                .font(.largeTitle.weight(.semibold))
            Text("The Mac service's task list isn't available, so Review can't suggest the next task. Start it with tools/loop-service start, or choose a recording yourself.")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(inProgress) { session in
                Button(RecordingName.of(session.id)) { state.loadSession(session) }
            }
            Button("Show recordings", action: showRecordings)
                .buttonStyle(.borderedProminent).controlSize(.large)
        }
    }

    private func start(_ step: ProjectStatus.Step) {
        startError = nil
        guard let destination = ReviewTaskProgress.destination(of: step) else {
            openWindow(id: "pipeline")
            return
        }
        state.openSession(destination.id, workspace: destination.workspace, step: destination.step)
        if state.selectedSession?.id != destination.id { startError = state.statusMessage }
    }
}

/// Collect › Label › Train › Test, with the current stage emphasised.
private struct StageStrip: View {
    let stages: [ProjectStatus.Stage]

    var body: some View {
        HStack(spacing: 6) {
            ForEach(Array(stages.enumerated()), id: \.element.id) { index, stage in
                if index > 0 {
                    Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
                }
                Text(stage.title)
                    .font(.callout.weight(stage.current ? .semibold : .regular))
                    .foregroundStyle(stage.current ? Color.accentColor : Color.secondary)
                    .help(stage.detail)
            }
            if let current = stages.first(where: \.current) {
                Text("· \(current.detail)").font(.callout).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Guide panel

/// The right-hand panel: what to do in the current step, its controls, and
/// optional tools tucked away under More options.
struct GuidePanel: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @EnvironmentObject var project: ProjectStatusStore
    @EnvironmentObject var assist: CoarseAssistStore
    let plan: GuidedPlan
    let step: GuidedStep
    let onPlayClip: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let retirement = state.retirement {
                retired(retirement)
            } else if let error = state.candidateIntegrityError {
                blocked(error)
            } else if step == .spans {
                CoarseAssistPanel(onPlayClip: onPlayClip)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        switch step {
                        case .chews: ChewDecisionCard()
                        case .mark: markStep
                        case .finish: finishStep
                        case .done, .spans: doneStep
                        }
                    }
                    .padding(18)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                if step != .done {
                    Divider()
                    ScrollView {
                        DisclosureGroup("More options") { ReviewToolsSection().padding(.top, 8) }
                            .padding(.horizontal, 18).padding(.vertical, 10)
                    }
                    .frame(maxHeight: 280)
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Color(nsColor: .controlBackgroundColor))
        .accessibilityIdentifier("guidePanel")
    }

    private var stepNumber: Int { (plan.steps.firstIndex(of: step) ?? 0) + 1 }
    private var watched: Double { state.watchedFraction(duration: playback.duration) }

    // MARK: Blind: mark every chew

    private var markStep: some View {
        VStack(alignment: .leading, spacing: 16) {
            StepHeading(number: stepNumber, title: "Mark every chew",
                        detail: "Play the recording. When you see a chew, pause where the mouth is open widest and press A (Add chew). No chewing at all is a valid answer.")
            HStack {
                playButton(fromStart: false).modifier(Prominence(primary: watched < 0.9))
                Button("Add chew", systemImage: "plus") { addChewAtPlayhead() }
                    .help("Add a chew centred on the playhead (A)")
            }
            WatchProgress()
            Text(state.chews.isEmpty ? "No chews marked yet." : "\(state.chews.count) chew\(state.chews.count == 1 ? "" : "s") marked.")
                .foregroundStyle(.secondary)
            Text("Suggestions stay hidden so this review is an independent check of the detector.")
                .font(.caption).foregroundStyle(.secondary)
            Divider()
            Button("I've watched it all") { state.chosenStep = .finish }
                .modifier(Prominence(primary: watched >= 0.9))
                .accessibilityIdentifier("blindContinue")
        }
    }

    // MARK: Watch and finish

    @State private var showFinish = false

    private var finishStep: some View {
        VStack(alignment: .leading, spacing: 16) {
            if state.isBlindReview {
                StepHeading(number: stepNumber, title: "Finish the review",
                            detail: "Make sure you watched the whole recording and marked every chew you saw.")
                Text(state.chews.isEmpty ? "You marked no chews." : "You marked \(state.chews.count) chew\(state.chews.count == 1 ? "" : "s").")
            } else {
                StepHeading(number: stepNumber, title: "Watch for missed chews",
                            detail: "Play the whole recording. If you see a chew without a marker, pause where the mouth is open widest and press A (Add chew).")
                Text(decisionSummary).foregroundStyle(.secondary)
            }
            HStack {
                playButton(fromStart: watched == 0).modifier(Prominence(primary: watched < 0.9 && state.finishBlocker == nil))
                if !state.isBlindReview {
                    Button("Add chew", systemImage: "plus") { addChewAtPlayhead() }
                }
            }
            WatchProgress()
            Divider()
            if let blocker = state.finishBlocker {
                Label(blocker, systemImage: "exclamationmark.circle")
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
                if state.pendingSuggestionCount > 0 {
                    Button("Check the remaining suggestions") { state.chosenStep = .chews }
                        .buttonStyle(.borderedProminent)
                }
            }
            Button("Finish review", systemImage: "checkmark.seal") { showFinish = true }
                .modifier(Prominence(primary: watched >= 0.9 && state.finishBlocker == nil))
                .disabled(state.finishBlocker != nil)
                .accessibilityIdentifier("finishReview")
            if state.isBlindReview {
                Button("Back to marking") { state.chosenStep = .mark }.buttonStyle(.link)
            }
        }
        .confirmationDialog("Finish this review?", isPresented: $showFinish) {
            Button("Finish and save") { state.finishReview() }
        } message: {
            Text(state.isBlindReview
                 ? "You watched the whole recording and marked every chew you saw, or there were none. It is saved as independent truth."
                 : "You watched the whole recording and every suggested chew is accepted, fixed or rejected. It is saved as reviewed labels.")
        }
    }

    private var decisionSummary: String {
        let decisions = state.overrides.chewDecisions
        let accepted = decisions.filter { $0.disposition == .accepted }.count
        let fixed = decisions.filter { $0.disposition == .edited }.count
        let rejected = decisions.filter { $0.disposition == .rejected }.count
        let unclear = decisions.filter { $0.disposition == .uncertain || $0.disposition == .unobservable }.count
        var parts = ["\(accepted) accepted", "\(fixed) fixed", "\(rejected) rejected"]
        if unclear > 0 { parts.append("\(unclear) unclear") }
        parts.append("\(state.overrides.addedChews.count) added")
        return parts.joined(separator: " · ")
    }

    // MARK: Done

    private var doneStep: some View {
        let next = ReviewTaskProgress.openSteps(project.status, loopRoot: state.loopRoot)
            .first { $0.sessionId != state.selectedSession?.id || ($0.id.hasPrefix("blind-") != state.isBlindReview) }
        return VStack(alignment: .leading, spacing: 16) {
            Label("Review finished", systemImage: "checkmark.seal.fill")
                .font(.title2.weight(.semibold)).foregroundStyle(.green)
            Text("Saved. The Mac checks it and locks it in automatically.")
                .foregroundStyle(.secondary)
            if let next, let destination = ReviewTaskProgress.destination(of: next) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Up next").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
                    Text(next.title).font(.headline)
                    Button { state.openSession(destination.id, workspace: destination.workspace, step: destination.step) } label: {
                        Label("Start", systemImage: "arrow.right")
                    }
                    .buttonStyle(.borderedProminent).controlSize(.large)
                    .accessibilityIdentifier("startNextTask")
                }
            } else {
                Button("Back to Up next") { state.closeSession() }
                    .buttonStyle(.borderedProminent).controlSize(.large)
            }
            if assist.needsReview {
                Button("Optional: label chewing spans for this recording") { state.chosenStep = .spans }
                    .buttonStyle(.link)
            }
            Text("To change something, press Undo (⌘Z) before the Mac locks it in.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    // MARK: Retired

    private func retired(_ retirement: RecordingRetirement) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Retired recording", systemImage: "archivebox")
                .font(.title2.weight(.semibold))
            Text("It isn't used for training or tests, and there is nothing to review. You can still watch it.")
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 4) {
                Text("Kept for research as").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Text(retirement.categoryTitle)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Why").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Text(retirement.reason).fixedSize(horizontal: false, vertical: true)
            }
            Text("The video, motion and every file made from them are kept unchanged.")
                .font(.caption).foregroundStyle(.secondary)
            Button("Back to Up next") { state.closeSession() }
                .buttonStyle(.borderedProminent).controlSize(.large)
        }
        .padding(18)
        .accessibilityIdentifier("retiredRecording")
    }

    // MARK: Blocked

    private func blocked(_ error: String) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("This recording can't be reviewed yet", systemImage: "exclamationmark.triangle.fill")
                .font(.headline).foregroundStyle(.orange)
            Text("Its files don't match what the review expects, so Review won't save anything here. Nothing was changed. Reprocess the recording on the Mac (Pipeline → Retry), or ask for help with the details below.")
                .fixedSize(horizontal: false, vertical: true)
            Button("Back to Up next") { state.closeSession() }
                .buttonStyle(.borderedProminent)
            DisclosureGroup("Technical details") {
                Text(error).font(.caption).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .font(.callout)
        }
        .padding(18)
    }

    // MARK: Shared controls

    private func playButton(fromStart: Bool) -> some View {
        Button(playback.isPlaying ? "Pause" : (fromStart ? "Play from start" : "Play"),
               systemImage: playback.isPlaying ? "pause.fill" : "play.fill") {
            if playback.isPlaying { playback.pause(); return }
            state.selectChew(id: nil)
            playback.clearLoop()
            if fromStart || playback.currentTime >= playback.duration - 0.05 { playback.seekToStart() }
            playback.play()
        }
        .accessibilityIdentifier("guidePlay")
    }

    private func addChewAtPlayhead() {
        state.addChewFitting(at: playback.currentTime)
    }
}

/// "Watched 0:45 of 1:30" with a thin bar. A hint for the reviewer, never a gate.
struct WatchProgress: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController

    var body: some View {
        let fraction = state.watchedFraction(duration: playback.duration)
        VStack(alignment: .leading, spacing: 4) {
            ProgressView(value: fraction)
            Text(fraction >= 0.99 ? "You've watched the whole recording."
                 : "Watched \(clock(fraction * playback.duration)) of \(clock(playback.duration))")
                .font(.caption).foregroundStyle(.secondary).monospacedDigit()
        }
    }

    private func clock(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded()))
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

struct StepHeading: View {
    let number: Int
    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Step \(number)").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            Text(title).font(.title2.weight(.semibold))
            Text(detail).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// Exactly one prominent button at a time: the rest are bordered.
struct Prominence: ViewModifier {
    let primary: Bool

    func body(content: Content) -> some View {
        if primary {
            content.buttonStyle(.borderedProminent).controlSize(.large)
        } else {
            content.buttonStyle(.bordered).controlSize(.large)
        }
    }
}

/// Header steps: number or checkmark, current step emphasised; click to go there.
struct StepPills: View {
    let plan: GuidedPlan
    let current: GuidedStep
    let blind: Bool
    let isComplete: (GuidedStep) -> Bool
    let select: (GuidedStep) -> Void

    var body: some View {
        HStack(spacing: 6) {
            ForEach(Array(plan.steps.enumerated()), id: \.element) { index, step in
                if index > 0 {
                    Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
                }
                Button { select(step) } label: {
                    HStack(spacing: 5) {
                        if isComplete(step) {
                            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                        } else {
                            Text("\(index + 1)").font(.caption.weight(.bold)).monospacedDigit()
                                .frame(width: 16, height: 16)
                                .background(Circle().strokeBorder(step == current ? Color.accentColor : Color.secondary.opacity(0.6)))
                        }
                        Text(step.title(blind: blind))
                            .font(.callout.weight(step == current ? .semibold : .regular))
                    }
                    .foregroundStyle(step == current ? Color.accentColor : Color.secondary)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("step-\(step.rawValue)")
            }
        }
    }
}
