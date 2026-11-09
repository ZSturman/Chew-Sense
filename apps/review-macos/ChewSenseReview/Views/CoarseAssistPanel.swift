import SwiftUI

/// Chewing spans, laid out like Check suggestions: one clip at a time with the
/// same Replay / Play / Loop / speed controls, a short reason it was flagged,
/// one prominent answer and the alternatives below. Flagged clips come first;
/// clips where video and AirPods agree can then be accepted together.
struct CoarseAssistPanel: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @EnvironmentObject var assist: CoarseAssistStore
    @Environment(\.scenePhase) private var scenePhase
    @State private var showSaveResult = false
    @State private var showEdges = false
    @State private var showAll = false
    let onPlayClip: () -> Void
    var stepNumber: Int = 1

    /// Required when the recording has no reviewed chewing intervals yet; otherwise optional.
    private var purpose: String {
        state.candidates?.requiresFullCoarseReview == true
            ? "Required: the Mac prepares chew suggestions from these answers."
            : "Optional: these answers train the AirPods chewing detector."
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if state.isBlindReview {
                        Text("Chewing spans stay hidden during a blind review.")
                    } else if let proposals = assist.proposals {
                        if let item = assist.selectedItem {
                            clipCard(item)
                        } else if !assist.document.batchCandidates.isEmpty {
                            agreementCard
                        } else {
                            allDecided
                        }
                        DisclosureGroup("All clips (\(assist.document.items.count))", isExpanded: $showAll) {
                            allClips(proposals)
                        }
                        .font(.callout)
                    } else {
                        StepHeading(number: stepNumber, title: "Chewing spans", detail: assist.message.isEmpty ? "Choose a recording." : assist.message)
                    }
                }
                .padding(18)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            if assist.proposals != nil, !state.isBlindReview {
                Divider()
                footer.padding(.horizontal, 18).padding(.vertical, 12)
            }
        }
        .frame(maxHeight: .infinity, alignment: .top)
        .onDisappear {
            assist.pauseTimer()
            playback.pause()
            playback.clearLoop()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { assist.pauseTimer() }
        }
        .onChange(of: assist.selectedSpanID, initial: true) { _, _ in cueSelected() }
        .onChange(of: assist.isDirty) { _, dirty in
            if dirty { showSaveResult = false }
        }
    }

    // MARK: One clip

    private func clipCard(_ item: CoarseAssistDocument.Item) -> some View {
        let flagged = assist.document.reviewClips.contains { $0.id == item.id }
        let clips = flagged ? assist.document.reviewClips : assist.document.agreementClips
        let index = clips.firstIndex { $0.id == item.id } ?? 0
        let left = clips.filter { !$0.isDecided }.count
        return VStack(alignment: .leading, spacing: 14) {
            StepHeading(number: stepNumber, title: flagged ? "Check flagged clips" : "Check agreed clips",
                        detail: "Is there chewing in this clip? Answer for the whole clip. \(purpose)")
            VStack(alignment: .leading, spacing: 4) {
                ProgressView(value: Double(clips.count - left), total: Double(max(clips.count, 1)))
                Text(left == 0 ? "All \(clips.count) answered" : "Clip \(index + 1) of \(clips.count) · \(left) left · \(timeRange(item))")
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
            Label(Self.whyFlagged(item.span), systemImage: flagged ? "exclamationmark.bubble" : "checkmark.bubble")
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let label = item.label {
                Label("Your answer: \(title(label))", systemImage: label == "unknown" ? "minus.circle" : "checkmark.circle.fill")
                    .foregroundStyle(label == "unknown" ? Color.secondary : Color.green)
            }
            Text("Was there chewing?").font(.headline)
            ClipControls(replay: { replay(item) }, toggle: onPlayClip)
            Button { assist.setLabel(item.id, "chewing") } label: {
                Label("Yes, chewing", systemImage: "checkmark").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent).controlSize(.large)
            .keyboardShortcut(.return, modifiers: [])
            .accessibilityIdentifier("spanChewing")
            HStack {
                Button { assist.setLabel(item.id, "not_chewing") } label: { Text("No chewing").frame(maxWidth: .infinity) }
                    .accessibilityIdentifier("spanNotChewing")
                Button { assist.setLabel(item.id, "unknown") } label: { Text("Can't tell – leave out").frame(maxWidth: .infinity) }
                    .accessibilityIdentifier("spanLeaveOut")
            }
            .controlSize(.large)
            Text("“Leave out” keeps this clip out of training and tests altogether, including your phone label for it. A clip you skip keeps your phone label.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            DisclosureGroup("Fix the clip edges", isExpanded: $showEdges) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("If the chewing starts or stops inside the clip, pause there and move the edge. The neighbouring clip moves with it.")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack {
                        Button("Start here") { assist.moveStart(of: item.id, to: playback.currentTime) }
                        Button("End here") { assist.moveEnd(of: item.id, to: playback.currentTime) }
                    }
                }
                .padding(.top, 6)
            }
            .font(.callout)
            HStack {
                Button("Previous") { if index > 0 { assist.selectedSpanID = clips[index - 1].id } }
                    .disabled(index == 0)
                Spacer()
                if item.isDecided { Button("Mark as not answered") { assist.clear(item.id) } }
                Button("Skip") { assist.selectedSpanID = clips[(index + 1) % clips.count].id }
            }
            .buttonStyle(.link).font(.callout)
        }
    }

    /// One plain sentence on why the clip needs a person.
    static func whyFlagged(_ span: CoarseProposals.Span) -> String {
        let e = span.evidence
        let phone = span.existingLabel.map { " Your phone label here: \(phoneTitle($0))." } ?? ""
        if span.conflictWithExistingLabel, let proposal = span.proposal {
            return "Video and AirPods agree on \(phoneTitle(proposal)), but your phone label says \(phoneTitle(span.existingLabel ?? "something else"))."
        }
        if span.isAgreement, let proposal = span.proposal {
            return "Video and AirPods agree: \(phoneTitle(proposal)).\(phone)"
        }
        if e.motionChewingWindows > 0 && e.videoChewingWindows == 0 {
            let percent = e.motionMeanProbability.map { " (\(Int(($0 * 100).rounded()))% likely)" } ?? ""
            return "Flagged: the AirPods felt chewing here\(percent), but the video detector saw none.\(phone)"
        }
        if e.videoChewingWindows > 0 && e.motionChewingWindows == 0 {
            return "Flagged: the video detector saw chewing here, but the AirPods didn't.\(phone)"
        }
        if e.videoUnavailableWindows > 0 {
            return "Flagged: the face wasn't clearly visible, so the video couldn't check this part.\(phone)"
        }
        return "Flagged: \(span.reason).\(phone)"
    }

    private static func phoneTitle(_ label: String) -> String {
        switch label {
        case "chewing": "chewing"
        case "not_chewing": "no chewing"
        case "unknown": "can't tell"
        case "mixed": "partly chewing"
        default: label
        }
    }

    // MARK: Agreed clips

    private var agreementCard: some View {
        let pending = assist.document.batchCandidates
        let chewing = pending.filter { $0.span.proposal == "chewing" }.count
        return VStack(alignment: .leading, spacing: 14) {
            StepHeading(number: stepNumber, title: "Check agreed clips",
                        detail: "Video and AirPods agree on these \(pending.count) clips: \(chewing) chewing, \(pending.count - chewing) no chewing. Accept them together, or check each one like the flagged clips.")
            Button { assist.batchAccept() } label: {
                Label("Use the \(pending.count) agreed answers", systemImage: "checkmark").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent).controlSize(.large)
            Button("Check them one at a time") { assist.selectedSpanID = pending.first?.id }
                .buttonStyle(.link)
        }
    }

    private var allDecided: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Every clip is answered", systemImage: "checkmark.circle.fill")
                .font(.title3.weight(.semibold)).foregroundStyle(.green)
            Text(assist.isDirty ? "Save to keep these labels." : "Saved.")
                .foregroundStyle(.secondary)
        }
    }

    // MARK: All clips

    private func allClips(_ proposals: CoarseProposals) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            AssistTimeline(proposals: proposals).frame(height: 92)
            ForEach(assist.document.reviewClips + assist.document.agreementClips) { item in
                Button { assist.selectedSpanID = item.id } label: {
                    HStack {
                        Image(systemName: item.isDecided ? (item.label == "unknown" ? "minus.circle" : "checkmark.circle.fill") : "circle")
                            .foregroundStyle(item.isDecided ? (item.label == "unknown" ? Color.secondary : Color.green) : Color.secondary)
                        Text(item.isDecided ? title(item.label ?? "") : (item.span.isAgreement ? "agreed: \(title(item.span.proposal ?? ""))" : "flagged"))
                        Spacer()
                        Text(timeRange(item)).monospacedDigit().foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 3).padding(.horizontal, 6)
                    .background(item.id == assist.selectedSpanID ? Color.accentColor.opacity(0.15) : .clear,
                                in: RoundedRectangle(cornerRadius: 5))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.top, 6)
    }

    // MARK: Save

    private var footer: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("\(assist.document.decidedCount) of \(assist.document.items.count) answered")
                Spacer()
                if assist.isTiming {
                    Button("Pause timing") { assist.pauseTimer() }.buttonStyle(.link)
                }
            }
            .font(.caption).foregroundStyle(.secondary)
            Button(assist.document.isComplete ? "Save labels" : "Save progress") {
                playback.pause()
                assist.save()
                showSaveResult = true
            }
            .modifier(Prominence(primary: assist.document.isComplete && assist.isDirty))
            .disabled(assist.document.decidedCount == 0 || !assist.isDirty)
            if showSaveResult {
                Text(assist.message).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: Playback

    private func replay(_ item: CoarseAssistDocument.Item) {
        assist.startTimer()
        ChewCue.play(start: item.start, end: item.end, playback: playback)
    }

    private func cueSelected() {
        playback.pause()
        guard let item = assist.selectedItem else {
            playback.clearLoop()
            return
        }
        replay(item)
    }

    private func timeRange(_ item: CoarseAssistDocument.Item) -> String {
        String(format: "%.1f–%.1f s", item.start, item.end)
    }

    private func title(_ label: String) -> String {
        label == "unknown" ? "left out" : Self.phoneTitle(label)
    }
}

/// Lanes: phone labels, video, motion probability, proposals/decisions, playhead.
private struct AssistTimeline: View {
    @EnvironmentObject var playback: PlaybackController
    @EnvironmentObject var assist: CoarseAssistStore
    let proposals: CoarseProposals

    var body: some View {
        GeometryReader { geometry in
            let duration = max(proposals.summary.durationS, 0.1)
            let width = geometry.size.width
            let x = { (t: Double) in CGFloat(t / duration) * width }
            Canvas { context, size in
                let lane = size.height / 4
                func bar(_ start: Double, _ end: Double, row: Int, _ color: Color) {
                    let rect = CGRect(x: x(start), y: CGFloat(row) * lane + 2, width: max(1, x(end) - x(start)), height: lane - 4)
                    context.fill(Path(rect), with: .color(color))
                }
                for label in proposals.existingLabels {
                    bar(label.start, label.end, row: 0, label.label == "chewing" ? .blue : .blue.opacity(0.2))
                }
                for window in proposals.windows {
                    let center = window.tEnd - 1.5
                    let color: Color = window.videoState == "chewing" ? .green : window.videoState == "not_chewing" ? .gray.opacity(0.35) : .clear
                    bar(center - 0.25, center + 0.25, row: 1, color)
                }
                var motion = Path()
                var started = false
                for window in proposals.windows {
                    guard let p = window.pMotion else { started = false; continue }
                    let point = CGPoint(x: x(window.tEnd - 1.5), y: 3 * lane - 2 - CGFloat(p) * (lane - 4))
                    if started { motion.addLine(to: point) } else { motion.move(to: point); started = true }
                }
                context.stroke(motion, with: .color(.orange), lineWidth: 1.2)
                for item in assist.document.items {
                    let base: Color = item.span.conflictWithExistingLabel ? .red : !item.span.isAgreement ? .orange
                        : item.span.proposal == "chewing" ? .green : .gray
                    let fill: Color = item.label == "chewing" ? .green : item.label == "not_chewing" ? .gray : item.label == "unknown" ? .yellow : base.opacity(0.35)
                    bar(item.start, item.end, row: 3, fill)
                    if item.id == assist.selectedSpanID {
                        context.stroke(Path(CGRect(x: x(item.start), y: 3 * lane + 1, width: max(1, x(item.end) - x(item.start)), height: lane - 2)),
                                       with: .color(.primary), lineWidth: 1.5)
                    }
                }
                let head = x(playback.currentTime)
                context.stroke(Path { $0.move(to: CGPoint(x: head, y: 0)); $0.addLine(to: CGPoint(x: head, y: size.height)) },
                               with: .color(.red), lineWidth: 1)
            }
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0).onEnded { value in
                let time = Double(value.location.x / max(width, 1)) * duration
                playback.seek(to: time)
                if let item = assist.document.items.first(where: { $0.start <= time && time < $0.end }) {
                    assist.selectedSpanID = item.id
                }
            })
        }
        .overlay(alignment: .topLeading) {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(["Phone", "Video", "Motion", "Spans"], id: \.self) { name in
                    Text(name).font(.system(size: 8)).foregroundStyle(.secondary).frame(height: 23, alignment: .top)
                }
            }
            .allowsHitTesting(false)
        }
        .background(Color.black.opacity(0.05), in: RoundedRectangle(cornerRadius: 6))
    }
}
