import SwiftUI

/// Assisted chewing-span review: video and AirPods motion each judge the
/// recording; agreement spans can be accepted together, everything else gets
/// one decision. Proposals stay proposals until saved here.
struct CoarseAssistPanel: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @EnvironmentObject var viewport: TimelineViewport
    @EnvironmentObject var assist: CoarseAssistStore
    @Environment(\.scenePhase) private var scenePhase
    let onClose: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Chewing spans").font(.headline)
                Spacer()
                Button { onClose() } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
            }
            if state.isBlindReview {
                Text("Proposals stay hidden during an independent benchmark.")
                    .font(.caption).foregroundStyle(.secondary)
            } else if let proposals = assist.proposals {
                summary(proposals)
                AssistTimeline(proposals: proposals)
                    .frame(height: 92)
                selectedControls
                spanList
                footer
            } else {
                Text(assist.message.isEmpty ? "Choose a recording." : assist.message)
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(14)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Color(nsColor: .controlBackgroundColor))
        .onDisappear { assist.pauseTimer() }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { assist.pauseTimer() }
        }
        .onChange(of: assist.selectedSpanID) { _, _ in cueSelected() }
    }

    private func summary(_ proposals: CoarseProposals) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Video and AirPods motion agree on \(Int((proposals.summary.coverage * 100).rounded()))% of this recording. "
                 + "Decide the \(proposals.summary.reviewItems) other spans; accept agreements together.")
                .font(.caption).fixedSize(horizontal: false, vertical: true)
            if proposals.summary.labelConflicts > 0 {
                Label("\(proposals.summary.labelConflicts) spans where both sources disagree with your phone label",
                      systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.red)
            }
            HStack {
                Text("\(assist.document.decidedCount)/\(assist.document.items.count) decided")
                if assist.isTiming {
                    TimelineView(.periodic(from: .now, by: 1)) { _ in reviewTime }
                } else {
                    reviewTime
                }
                Spacer()
                Button(assist.isTiming ? "Pause timing" : "Start timing") {
                    if assist.isTiming { assist.pauseTimer() }
                    else { assist.startTimer() }
                }
                .controlSize(.small)
            }
            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
        }
    }

    private var reviewTime: some View {
        Text("\(Int(assist.activeSeconds)) s timed")
    }

    @ViewBuilder
    private var selectedControls: some View {
        if let item = assist.selectedItem {
            VStack(alignment: .leading, spacing: 6) {
                Text(String(format: "%.1f–%.1f s", item.start, item.end)).font(.callout.monospacedDigit().weight(.semibold))
                Text(evidenceText(item.span)).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 6) {
                    if let proposal = item.span.proposal {
                        Button("Accept \(title(proposal))") { assist.accept(item.id) }
                    }
                    Button("Chewing") { assist.setLabel(item.id, "chewing") }
                    Button("Not chewing") { assist.setLabel(item.id, "not_chewing") }
                    Button("Can't tell") { assist.setLabel(item.id, "unknown") }
                }
                HStack(spacing: 6) {
                    Button("Start at playhead") { assist.moveStart(of: item.id, to: playback.currentTime) }
                    Button("End at playhead") { assist.moveEnd(of: item.id, to: playback.currentTime) }
                    if item.isDecided { Button("Undo decision") { assist.clear(item.id) } }
                }
                .controlSize(.small)
            }
            .padding(10)
            .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    private var spanList: some View {
        let needsYou = assist.document.items.filter { !$0.span.isAgreement || $0.span.conflictWithExistingLabel }
        let agreements = assist.document.items.filter { $0.span.isAgreement && !$0.span.conflictWithExistingLabel }
        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 6) {
                Text("Needs you (\(needsYou.filter { !$0.isDecided }.count) left)").font(.caption.weight(.semibold))
                ForEach(needsYou.sorted(by: Self.conflictsFirst)) { row($0) }
                HStack {
                    Text("Agreement (\(agreements.count))").font(.caption.weight(.semibold))
                    Spacer()
                    Button("Accept \(assist.document.batchCandidates.count) together") { assist.batchAccept() }
                        .disabled(assist.document.batchCandidates.isEmpty)
                        .controlSize(.small)
                }
                .padding(.top, 6)
                ForEach(agreements) { row($0) }
            }
        }
    }

    /// Spans contradicting a phone label first, then in time order.
    private static func conflictsFirst(_ a: CoarseAssistDocument.Item, _ b: CoarseAssistDocument.Item) -> Bool {
        let rankA = a.span.conflictWithExistingLabel ? 0 : 1
        let rankB = b.span.conflictWithExistingLabel ? 0 : 1
        return rankA != rankB ? rankA < rankB : a.start < b.start
    }

    private func row(_ item: CoarseAssistDocument.Item) -> some View {
        Button { assist.selectedSpanID = item.id } label: {
            HStack(spacing: 8) {
                Image(systemName: item.isDecided ? "checkmark.circle.fill" : item.span.conflictWithExistingLabel ? "exclamationmark.triangle.fill" : "circle")
                    .foregroundStyle(item.isDecided ? .green : item.span.conflictWithExistingLabel ? .red : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(String(format: "%.1f–%.1f s · %@", item.start, item.end,
                                item.label.map(title) ?? (item.span.proposal.map { "proposed \(title($0))" } ?? "no proposal")))
                        .font(.caption.monospacedDigit())
                    Text(item.span.reason).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
            }
            .padding(6)
            .background(color(for: item).opacity(assist.selectedSpanID == item.id ? 0.3 : 0.1),
                        in: RoundedRectangle(cornerRadius: 6))
        }
        .buttonStyle(.plain)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Button("Save labels") { assist.save() }
                    .buttonStyle(.borderedProminent)
                    .disabled(assist.document.decidedCount == 0 || !assist.isDirty)
                if let revision = assist.savedRevision {
                    Text("Saved v\(String(format: "%04d", revision))").font(.caption).foregroundStyle(.secondary)
                }
            }
            Text(assist.message).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("Saving writes a new immutable coarse-review revision. Phone labels and proposals are never changed. "
                 + "Batch-accepted spans train the model but are never used to score it.")
                .font(.caption2).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func cueSelected() {
        guard let item = assist.selectedItem else { return }
        playback.pause()
        playback.setLoop(start: item.start, end: item.end, enabled: true)
        playback.seek(to: item.start)
        viewport.center = (item.start + item.end) / 2
        viewport.zoom = max(1, playback.duration / max(1, item.end - item.start + 2))
    }

    private func evidenceText(_ span: CoarseProposals.Span) -> String {
        let e = span.evidence
        let video = e.videoUnavailableWindows == span.windows
            ? "face not visible"
            : "video chewing in \(e.videoChewingWindows)/\(span.windows - e.videoUnavailableWindows) windows"
        let motion = e.motionMeanProbability.map { String(format: "motion %.0f%% (chewing in %d/%d windows)", $0 * 100, e.motionChewingWindows, span.windows) } ?? "no motion"
        let existing = span.existingLabel.map { " · phone label: \(title($0))" } ?? ""
        return "\(span.reason). \(video); \(motion)\(existing)"
    }

    private func title(_ label: String) -> String {
        switch label {
        case "chewing": return "chewing"
        case "not_chewing": return "not chewing"
        case "unknown": return "can't tell"
        case "mixed": return "mixed"
        default: return label
        }
    }

    private func color(for item: CoarseAssistDocument.Item) -> Color {
        if item.span.conflictWithExistingLabel { return .red }
        if !item.span.isAgreement { return .orange }
        return item.span.proposal == "chewing" ? .green : .gray
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
