import SwiftUI

/// Guide the reviewer through flagged clips, then suggest the agreement batch.
struct CoarseAssistPanel: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @EnvironmentObject var assist: CoarseAssistStore
    @Environment(\.scenePhase) private var scenePhase
    @State private var showSaveResult = false
    let onPlayClip: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Chewing spans").font(.title2.weight(.semibold))
                Text(state.candidates?.requiresFullCoarseReview == true
                     ? "Required first: say where chewing happens. The Mac then prepares the chew suggestions."
                     : "Optional, quick: say where chewing happens. It improves the AirPods chewing detector.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if state.isBlindReview {
                Text("Proposals stay hidden during an independent benchmark.")
            } else if let proposals = assist.proposals {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if let item = assist.selectedItem {
                            clipCard(item)
                        } else if !assist.document.batchCandidates.isEmpty {
                            agreementCard
                        } else if assist.document.isComplete {
                            Text(assist.isDirty ? "Review complete. Save your labels below." : "All clips reviewed.")
                                .font(.headline)
                        }
                        DisclosureGroup("All clips and evidence") { details(proposals) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                footer
            } else {
                Text(assist.message.isEmpty ? "Choose a recording." : assist.message)
            }
        }
        .padding(16)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Color(nsColor: .controlBackgroundColor))
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

    private func clipCard(_ item: CoarseAssistDocument.Item) -> some View {
        let flagged = assist.document.reviewClips.contains { $0.id == item.id }
        let clips = flagged ? assist.document.reviewClips : assist.document.agreementClips
        let index = clips.firstIndex { $0.id == item.id } ?? 0
        return VStack(alignment: .leading, spacing: 12) {
            Text(flagged ? "1. Check flagged clips" : "2. Check suggested labels")
                .font(.headline)
            Text("Clip \(index + 1) of \(clips.count) · \(timeRange(item))")
                .font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)

            Button(playback.isPlaying ? "Pause clip" : "Play clip",
                   systemImage: playback.isPlaying ? "pause.fill" : "play.fill", action: onPlayClip)
                .buttonStyle(.borderedProminent)
            Slider(value: Binding(
                get: { min(max(playback.currentTime, item.start), item.end) },
                set: { playback.seek(to: $0) }
            ), in: item.start...item.end)
            .accessibilityLabel("Clip position")

            Text("Was there chewing in this clip?").font(.headline)
            HStack(spacing: 6) {
                decisionButton("Chewing", label: "chewing", for: item)
                decisionButton("No chewing", label: "not_chewing", for: item)
                decisionButton("Can't tell", label: "unknown", for: item)
            }
            if item.label != nil {
                Text("Current choice: \(title(item.label ?? ""))").font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                if index > 0 {
                    Button("Previous clip") { assist.selectedSpanID = clips[index - 1].id }
                }
                if !flagged {
                    Button("Back to suggestions") { assist.selectedSpanID = nil }
                }
            }
            .controlSize(.small)
        }
    }

    private func decisionButton(_ title: String, label: String, for item: CoarseAssistDocument.Item) -> some View {
        Button(title) {
            assist.setLabel(item.id, label)
        }
        .buttonStyle(.bordered)
        .tint(item.label == label ? .accentColor : nil)
    }

    private var agreementCard: some View {
        let pending = assist.document.batchCandidates
        return VStack(alignment: .leading, spacing: 10) {
            Text("2. Check suggested labels").font(.headline)
            Text("Video and AirPods motion agree on these \(pending.count) remaining clips.")
                .foregroundStyle(.secondary)
            Text("\(pending.filter { $0.span.proposal == "chewing" }.count) chewing · \(pending.filter { $0.span.proposal == "not_chewing" }.count) no chewing")
                .font(.subheadline.monospacedDigit())
            Button("Use \(pending.count) suggested labels") { assist.batchAccept() }
                .buttonStyle(.borderedProminent)
            Button("Review one at a time") { assist.selectedSpanID = pending.first?.id }
        }
    }

    private func details(_ proposals: CoarseProposals) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            AssistTimeline(proposals: proposals).frame(height: 92)
            if let item = assist.selectedItem {
                Text(item.span.reason).font(.caption).foregroundStyle(.secondary)
                let evidence = item.span.evidence
                Text("Video: \(evidence.videoChewingWindows) chewing windows · Motion: \(evidence.motionChewingWindows) chewing windows")
                    .font(.caption).foregroundStyle(.secondary)
                if let label = item.span.existingLabel {
                    Text("Phone label: \(title(label))").font(.caption)
                }
                HStack {
                    Button("Start at playhead") { assist.moveStart(of: item.id, to: playback.currentTime) }
                    Button("End at playhead") { assist.moveEnd(of: item.id, to: playback.currentTime) }
                    if item.isDecided { Button("Clear choice") { assist.clear(item.id) } }
                }
                .controlSize(.small)
            }
            ForEach(assist.document.reviewClips + assist.document.agreementClips) { item in
                Button {
                    assist.selectedSpanID = item.id
                } label: {
                    Label("\(timeRange(item)) · \(title(item.label ?? item.span.proposal ?? "needs review"))",
                          systemImage: item.isDecided ? "checkmark.circle.fill" : "circle")
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.top, 8)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider()
            HStack {
                Text("\(assist.document.decidedCount) of \(assist.document.items.count) labelled")
                Spacer()
                if assist.isTiming {
                    TimelineView(.periodic(from: .now, by: 1)) { _ in reviewTime }
                    Button("Pause timing") { assist.pauseTimer() }.controlSize(.small)
                } else {
                    reviewTime
                }
            }
            .font(.caption).foregroundStyle(.secondary)
            if assist.document.isComplete {
                saveButton("Save labels").buttonStyle(.borderedProminent)
            } else {
                saveButton("Save progress").buttonStyle(.bordered)
            }
            if showSaveResult {
                Text(assist.message).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var reviewTime: some View {
        Text(!assist.isTiming && assist.activeSeconds == 0 ? "Timer starts with Play" : "\(Int(assist.activeSeconds)) s timed")
            .monospacedDigit()
    }

    private func saveButton(_ title: String) -> some View {
        Button(title) {
            playback.pause()
            assist.save()
            showSaveResult = true
        }
        .disabled(assist.document.decidedCount == 0 || !assist.isDirty)
    }

    private func cueSelected() {
        playback.pause()
        guard let item = assist.selectedItem else {
            playback.clearLoop()
            return
        }
        playback.setLoop(start: item.start, end: item.end, enabled: false)
        playback.seek(to: item.start)
    }

    private func timeRange(_ item: CoarseAssistDocument.Item) -> String {
        String(format: "%.1f–%.1f s", item.start, item.end)
    }

    private func title(_ label: String) -> String {
        switch label {
        case "chewing": return "chewing"
        case "not_chewing": return "no chewing"
        case "unknown": return "can't tell"
        case "mixed": return "mixed"
        default: return label
        }
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
