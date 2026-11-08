import SwiftUI
import AppKit

/// What happened after upload: recordings, checks, labels, splits, the model,
/// how well it does, where it is wrong and what to record next. Everything is
/// read from `pipeline_status.json`; the detailed HTML report stays one click away.
struct TrainingStatusView: View {
    @EnvironmentObject var state: ReviewerState
    @StateObject private var store = TrainingStatusStore()
    private let initialSelection: String?

    init(initialSelection: String? = nil) {
        self.initialSelection = initialSelection
    }

    var body: some View {
        Group {
            if store.workspace == nil {
                VStack(spacing: 12) {
                    Text("Choose the ChewSense workspace folder.")
                    Button("Choose Folder…") { store.choose() }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        ProjectStatusBox()
                        TrainingStatusContent(store: store, initialSelection: initialSelection)
                    }
                    .padding(20)
                }
            }
        }
        .frame(minWidth: 900, minHeight: 640)
        .onAppear { store.adopt(reviewRoot: state.workspaceRoot) }
    }
}

/// The scrollable content, separate so it can also be rendered for the docs.
struct TrainingStatusContent: View {
    @ObservedObject var store: TrainingStatusStore
    @State private var selectedRecording: String?

    init(store: TrainingStatusStore, initialSelection: String? = nil) {
        self.store = store
        _selectedRecording = State(initialValue: initialSelection)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            if let status = store.status {
                dataSection(status)
                modelSection(status)
                recordingsSection(status)
                errorsSection(status)
                assistSection(status)
                behaviorSection(status)
                nextSection(status)
                historySection(status)
            } else if let error = store.loadError {
                Text(error).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: Header and actions

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Training and evaluation").font(.title2.bold())
                    Text(store.status.map { "Status from \(TrainingStatusContent.date($0.generatedAt))" } ?? "")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Refresh status", systemImage: "arrow.clockwise") { store.refresh() }
                    .disabled(store.running != nil)
                Button("Run development check", systemImage: "play") { store.runDevelopmentCheck() }
                    .disabled(store.running != nil)
                    .help("chewsense-motion eating-experiment --dev-only: cross-validation on development recordings; the locked test is not consulted.")
                if let path = store.status?.model.reportPath {
                    Button("Open HTML report") { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
                }
            }
            if store.running != nil || !store.log.isEmpty {
                GroupBox {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            if let running = store.running {
                                ProgressView().controlSize(.small)
                                Text(running).font(.caption)
                                Button("Cancel") { store.cancel() }.controlSize(.small)
                            } else {
                                Label(store.lastRunFailed ? "Last command failed" : "Last command finished",
                                      systemImage: store.lastRunFailed ? "xmark.octagon" : "checkmark.circle")
                                    .foregroundStyle(store.lastRunFailed ? .red : .green).font(.caption)
                            }
                        }
                        ScrollView {
                            Text(store.log.suffix(4000)).font(.system(.caption2, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                        }
                        .frame(maxHeight: 140)
                    }
                }
            }
        }
    }

    // MARK: 1–5 Data

    private func dataSection(_ status: PipelineStatus) -> some View {
        let data = status.data
        return GroupBox {
            VStack(alignment: .leading, spacing: 6) {
                Text("\(data.usableRecordings) usable recordings (\(data.rejectedRecordings) rejected) · \(data.participants) wearer · "
                     + "\(TrainingStatusContent.number(data.labelledMinutes)) labelled minutes, \(TrainingStatusContent.number(data.chewingMinutes)) chewing")
                Text("Chewing with a shared video/motion clock: \(TrainingStatusContent.number(data.verifiedClockChewingMinutes)) min. The rest are 2025 labels whose alignment cannot be checked.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack(alignment: .top, spacing: 24) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Splits").font(.caption.weight(.semibold))
                        ForEach(data.bySplit.keys.sorted(), id: \.self) { key in
                            let split = data.bySplit[key]!
                            Text("\(key): \(split.recordings) recordings, \(split.withChewing) with chewing · \(split.chewingWindows)/\(split.labelledWindows) chewing windows")
                                .font(.caption.monospacedDigit())
                        }
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Label provenance (minutes)").font(.caption.weight(.semibold))
                        ForEach(data.labelledMinutesByTier.keys.sorted(), id: \.self) { tier in
                            Text("\(tier): \(TrainingStatusContent.number(data.labelledMinutesByTier[tier] ?? 0))").font(.caption.monospacedDigit())
                        }
                    }
                }
                if !data.awaitingAssistedReview.isEmpty {
                    Label("Chewing-span proposals waiting for review: \(data.awaitingAssistedReview.joined(separator: ", "))",
                          systemImage: "wand.and.stars").font(.caption)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { Label("Training data", systemImage: "tray.full") }
    }

    // MARK: 6–8 Model

    private func modelSection(_ status: PipelineStatus) -> some View {
        let model = status.model
        return GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                if !model.trained {
                    Text("No model has been trained. Run: chewsense-motion eating-experiment --dev-only")
                } else {
                    Text("Selected: \(model.selectedModel ?? "—") from \(model.runId ?? "—") (\(TrainingStatusContent.date(model.finishedAt))) · threshold \(TrainingStatusContent.score(model.threshold))")
                    if model.trainedOnCurrentData == false {
                        Label("Labels or recordings changed since this run (\(model.recordingsNotInRun?.count ?? 0) recordings not in it). Run a development check.",
                              systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.caption)
                    } else {
                        Label("Trained on exactly the current labelled data.", systemImage: "checkmark.circle").font(.caption)
                    }
                    HStack(alignment: .top, spacing: 24) {
                        scores("Development (out-of-fold)", model.development)
                        scores("Locked test", model.test)
                    }
                    Text("The locked test has been consulted \(model.testConsultations ?? 0) times. Choose models on development results; run the full experiment only to confirm.")
                        .font(.caption).foregroundStyle(.secondary)
                    if let comparison = model.comparison {
                        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 2) {
                            GridRow { ForEach(["Model", "Dev F1", "Dev AUC-PR", "Test F1", "Python ms/window"], id: \.self) { Text($0).font(.caption.weight(.semibold)) } }
                            ForEach(comparison) { row in
                                GridRow {
                                    Text(row.model + (row.selected ? " ✓" : ""))
                                    Text(TrainingStatusContent.score(row.devF1)); Text(TrainingStatusContent.score(row.devAucPr)); Text(TrainingStatusContent.score(row.testF1))
                                    Text(TrainingStatusContent.score(row.latencyMs))
                                }
                                .font(.caption.monospacedDigit())
                            }
                        }
                    }
                }
                Divider()
                if let exported = model.live?.exportedDetector {
                    Text("Live: exported detector \(exported.modelId ?? "—") · \(exported.status ?? "—")").font(.callout)
                    Text(exported.modeInLive ?? "").font(.caption).foregroundStyle(.secondary)
                }
                if let chewnet = model.live?.chewnet {
                    Text("Live default: ChewNet \(chewnet.modelId ?? "—") — \(chewnet.role ?? "")").font(.caption).foregroundStyle(.secondary)
                }
                if let parity = model.parity {
                    Label("Python vs Swift: \(parity.status?.uppercased() ?? "—") · \(parity.recordingsPassed ?? 0)/\(parity.recordings ?? 0) recordings, \(parity.scoredWindows ?? 0) windows, features bit-identical \(TrainingStatusContent.percent(parity.featureValuesBitIdentical)), max probability difference \(TrainingStatusContent.score(parity.maxProbabilityAbsDiff)), state disagreements \(parity.stateDisagreements ?? 0)",
                          systemImage: parity.status == "pass" ? "checkmark.seal" : "xmark.seal")
                        .font(.caption)
                    Text(parity.inputs ?? "").font(.caption2).foregroundStyle(.secondary)
                }
                Text("Physical-device validation: \(model.live?.physicalDeviceValidation ?? "unknown")").font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { Label("Model", systemImage: "cpu") }
    }

    private func scores(_ title: String, _ scores: PipelineStatus.Scores?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption.weight(.semibold))
            if let s = scores {
                let ci = (s.f1Ci95 ?? []).compactMap { $0 }
                Text("F1 \(TrainingStatusContent.score(s.f1))" + (ci.count == 2 ? String(format: " (95%% CI %.2f–%.2f)", ci[0], ci[1]) : ""))
                Text("precision \(TrainingStatusContent.score(s.precision)) · recall \(TrainingStatusContent.score(s.recall)) · false-positive rate \(TrainingStatusContent.percent(s.falsePositiveRate))")
                Text("\(s.recordings ?? 0) recordings · \(s.chewingWindows ?? 0) chewing / \(s.nonChewingWindows ?? 0) other windows")
                Text("bouts found \(s.boutsDetected ?? 0)/\(s.boutsReference ?? 0) · false bouts \(s.falseBouts ?? 0) (\(TrainingStatusContent.score(s.falseBoutsPerHour)) per hour) · onset \(TrainingStatusContent.score(s.onsetLatencyMedianS)) s")
            } else {
                Text("not evaluated")
            }
        }
        .font(.caption.monospacedDigit())
    }

    // MARK: 1–5 Recordings

    private func recordingsSection(_ status: PipelineStatus) -> some View {
        GroupBox {
            Table(status.recordings, selection: $selectedRecording) {
                TableColumn("Recording") { row in
                    Text(row.sessionId).foregroundStyle(row.usable ? .primary : .secondary).help(row.reason ?? row.attention ?? "")
                }
                .width(min: 200, ideal: 250)
                TableColumn("Split") { row in Text(row.split ?? "—") }.width(70)
                TableColumn("Clock / sync") { row in
                    Text(row.usable ? (row.sync?.clock == "shared_host_clock" ? "shared clock · " : "") + (row.sync?.syncCheck ?? "—") : "—")
                        .help(row.sync?.syncCheck ?? "")
                }
                .width(min: 120, ideal: 170)
                TableColumn("Quality") { row in Text(row.sync?.qualityChecks ?? "—").help(row.sync?.qualityChecks ?? "") }
                    .width(min: 80, ideal: 120)
                TableColumn("Labels") { row in
                    if let labels = row.labels {
                        Text("\(TrainingStatusContent.number(labels.labelledS ?? 0)) s · \(TrainingStatusContent.number(labels.chewingS ?? 0)) s chewing"
                             + ((labels.assistedS ?? 0) > 0 ? " · assisted" : ""))
                            .help((labels.byTierS ?? [:]).map { "\($0.key): \(Int($0.value)) s" }.sorted().joined(separator: "\n"))
                    } else {
                        Text(row.reason ?? "—").foregroundStyle(.secondary)
                    }
                }
                .width(min: 160, ideal: 210)
                TableColumn("Proposals") { row in
                    if let proposals = row.proposals {
                        Text("\(TrainingStatusContent.percent(proposals.coverage)) agree · \(proposals.reviewItems ?? 0) to review")
                    } else {
                        Text(row.video == true ? "—" : "no video").foregroundStyle(.secondary)
                    }
                }
                .width(min: 110, ideal: 140)
            }
            .frame(height: 260)
        } label: { Label("Recordings (select one to see its timeline)", systemImage: "list.bullet.rectangle") }
    }

    // MARK: 9 Errors

    private func errorsSection(_ status: PipelineStatus) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                if let id = selectedRecording, let timeline = status.model.timelines?[id] {
                    Text("\(id) · \(timeline.split) · \(timeline.falsePositiveWindows) false-positive and \(timeline.falseNegativeWindows) missed windows")
                        .font(.callout.weight(.semibold))
                    PredictionTimeline(timeline: timeline, threshold: status.model.threshold ?? 0.5)
                        .frame(height: 150)
                    Text("Lanes: human labels (blue chewing, grey not chewing, outline = assisted), guided instructions (context only), model probability with threshold, decoded bouts, false positives (red) and misses (orange). Development recordings show out-of-fold predictions.")
                        .font(.caption2).foregroundStyle(.secondary)
                } else {
                    Text("Select a recording above to see predictions against labels.").font(.caption).foregroundStyle(.secondary)
                }
                if let errors = status.model.errorsByRecording {
                    Text("Recordings with the most errors").font(.caption.weight(.semibold))
                    Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 2) {
                        GridRow { ForEach(["Recording", "Split", "Recall", "False-positive rate", "False bouts"], id: \.self) { Text($0).font(.caption.weight(.semibold)) } }
                        ForEach(errors.prefix(8)) { row in
                            GridRow {
                                Button(row.sessionId) { selectedRecording = row.sessionId }.buttonStyle(.link)
                                Text(row.split); Text(TrainingStatusContent.score(row.recall)); Text(TrainingStatusContent.percent(row.falsePositiveRate))
                                Text("\(row.falseBouts ?? 0)")
                            }
                            .font(.caption.monospacedDigit())
                        }
                    }
                }
                if let contexts = status.model.errorsByContext?.filter({ ($0.negatives ?? 0) >= 10 && ($0.falsePositiveRate ?? 0) > 0.03 }), !contexts.isEmpty {
                    Text("Activities mistaken for chewing").font(.caption.weight(.semibold))
                    ForEach(contexts.prefix(6)) { context in
                        Text("\(context.context) (\(context.split)): \(TrainingStatusContent.percent(context.falsePositiveRate)) of \(context.negatives ?? 0) windows")
                            .font(.caption)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { Label("Where predictions are wrong", systemImage: "exclamationmark.magnifyingglass") }
    }

    private func assistSection(_ status: PipelineStatus) -> some View {
        GroupBox {
            if let assist = status.assistedLabeling {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Video + motion agreement on \(assist.recordings ?? 0) recordings, \(assist.windowsScored ?? 0) human-labelled windows:")
                    Text("coverage \(TrainingStatusContent.percent(assist.coverage?.rate)) · proposed chewing correct \(TrainingStatusContent.percent(assist.autoChewingPrecision?.rate)) (95% CI from \(TrainingStatusContent.percent(assist.autoChewingPrecision?.low))) · proposed not-chewing correct \(TrainingStatusContent.percent(assist.autoNotChewingPrecision?.rate)) (from \(TrainingStatusContent.percent(assist.autoNotChewingPrecision?.low)))")
                        .font(.caption.monospacedDigit())
                    Text("false positives \(assist.falsePositiveWindows ?? 0) · false negatives \(assist.falseNegativeWindows ?? 0) · windows needing review \(assist.reviewWindows ?? 0) (\(assist.videoUnavailableWindows ?? 0) without a visible face)")
                        .font(.caption.monospacedDigit())
                    if let effort = assist.effortModel {
                        Text("Review effort: \(Int(effort.assistedS ?? 0)) s vs \(Int(effort.manualS ?? 0)) s marking by hand — \(effort.status ?? ""). Review records real active time when you use Chewing spans.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                Text("Not evaluated yet: chewsense-motion assist-eval").font(.caption)
            }
        } label: { Label("Assisted labeling", systemImage: "wand.and.stars") }
    }

    // MARK: Behaviour foundation

    private func behaviorSection(_ status: PipelineStatus) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 6) {
                if let timing = status.behavior?.boutTiming {
                    Text("Bouts (development, out-of-fold): \(timing.matchedBouts ?? 0)/\(timing.referenceBouts ?? 0) found · start \(TrainingStatusContent.score(timing.medianOnsetErrorS)) s late (median) · end error \(TrainingStatusContent.score(timing.medianAbsOffsetErrorS)) s · pauses \(timing.pausesDetected ?? 0)/\(timing.referencePauses ?? 0), error \(TrainingStatusContent.score(timing.medianAbsPauseErrorS)) s")
                        .font(.caption.monospacedDigit())
                }
                if let cadence = status.behavior?.cadence, (cadence.windows ?? 0) > 0 {
                    Text("Cadence vs video: \(cadence.windows ?? 0) windows, median difference \(TrainingStatusContent.score(cadence.medianAbsDifferenceHz)) Hz, \(TrainingStatusContent.percent(cadence.withinRange02Hz)) within 0.2 Hz. \(cadence.caveat ?? "")")
                        .font(.caption)
                }
                if let cycles = status.behavior?.videoCycleConfidence, let auc = cycles.loroAuc {
                    Text("Video chew-cycle confidence: rhythm-calibrated, AUC \(TrainingStatusContent.score(auc)) vs \(TrainingStatusContent.score(cycles.fixedConstantsAuc)) for the former constants (\(cycles.cycles ?? 0) cycles).")
                        .font(.caption)
                }
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 3) {
                    GridRow { ForEach(["Measurement", "Kind", "Status", "Needed"], id: \.self) { Text($0).font(.caption.weight(.semibold)) } }
                    ForEach(status.behavior?.registry ?? []) { item in
                        GridRow {
                            Text(item.name.replacingOccurrences(of: "_", with: " "))
                            Text(item.observability.replacingOccurrences(of: "_", with: " "))
                                .foregroundStyle(item.observability == "not_measurable" ? .secondary : .primary)
                            Text(item.status).lineLimit(2)
                            Text(item.needed).foregroundStyle(.secondary).lineLimit(2)
                        }
                        .font(.caption)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { Label("Chewing behaviour (foundation; not health advice)", systemImage: "waveform") }
    }

    // MARK: 10 Next, history

    private func nextSection(_ status: PipelineStatus) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(status.next.prefix(8)) { item in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(item.title ?? "").font(.callout.weight(.semibold))
                        Text(item.reason ?? "").font(.caption).foregroundStyle(.secondary)
                        if let command = item.command { Text(command).font(.caption.monospaced()).textSelection(.enabled) }
                    }
                }
                Divider()
                ForEach(status.limitations, id: \.self) { Text("• \($0)").font(.caption).foregroundStyle(.secondary) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { Label("What would help most", systemImage: "arrow.forward.circle") }
    }

    private func historySection(_ status: PipelineStatus) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 6) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 2) {
                    GridRow { ForEach(["Run", "Finished", "Test", "Model", "Data", "Dev F1", "Test F1", "Note"], id: \.self) { Text($0).font(.caption.weight(.semibold)) } }
                    ForEach(status.history.reversed()) { run in
                        GridRow {
                            Text(run.runId ?? "—"); Text(TrainingStatusContent.date(run.finishedAt))
                            Text(run.testEvaluated == false ? "dev only" : "consulted")
                            Text(run.selectedModel ?? "—"); Text(run.datasetFingerprint ?? "—")
                            Text(TrainingStatusContent.score(run.devF1)); Text(TrainingStatusContent.score(run.testF1)); Text(run.note ?? "")
                        }
                        .font(.caption.monospacedDigit())
                    }
                }
                Text("Commands").font(.caption.weight(.semibold)).padding(.top, 4)
                ForEach(status.commands.keys.sorted(), id: \.self) { key in
                    Text("\(key): \(status.commands[key] ?? "")").font(.caption.monospaced()).textSelection(.enabled)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { Label("Experiment history", systemImage: "clock.arrow.circlepath") }
    }

    // MARK: Formatting

    static func score(_ value: Double?) -> String { value.map { String(format: "%.2f", $0) } ?? "—" }
    static func percent(_ value: Double?) -> String { value.map { String(format: "%.0f%%", $0 * 100) } ?? "—" }
    static func number(_ value: Double) -> String { String(format: value >= 100 ? "%.0f" : "%.1f", value) }

    static func date(_ iso: String?) -> String {
        guard let iso else { return "—" }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = parser.date(from: iso) ?? ISO8601DateFormatter().date(from: iso)
        return date.map { $0.formatted(date: .abbreviated, time: .shortened) } ?? iso
    }
}

/// Labels, instructions, probability, decoded bouts and errors for one recording.
private struct PredictionTimeline: View {
    let timeline: PipelineStatus.Timeline
    let threshold: Double

    var body: some View {
        Canvas { context, size in
            let end = max(timeline.tEnd.last ?? 1, timeline.humanIntervals.compactMap { $0.count > 1 ? $0[1].number : nil }.max() ?? 1)
            let x = { (t: Double) in CGFloat(t / end) * size.width }
            let lanes: [CGFloat] = [0, 18, 36, 112, 128]
            func bar(_ start: Double, _ stop: Double, y: CGFloat, height: CGFloat, _ color: Color, stroke: Bool = false) {
                let rect = CGRect(x: x(start), y: y, width: max(1, x(stop) - x(start)), height: height)
                if stroke { context.stroke(Path(rect), with: .color(color), lineWidth: 1) } else { context.fill(Path(rect), with: .color(color)) }
            }
            for interval in timeline.humanIntervals where interval.count >= 4 {
                guard let start = interval[0].number, let stop = interval[1].number else { continue }
                let chewing = interval[2].string == "chewing"
                bar(start, stop, y: lanes[0], height: 14, chewing ? .blue : .gray.opacity(0.3))
                if interval[3].string?.hasPrefix("human_assisted") == true || interval[3].string == "human_batch_accepted" {
                    bar(start, stop, y: lanes[0], height: 14, .primary, stroke: true)
                }
            }
            for item in timeline.guidedContext where item.count >= 3 {
                guard let start = item[0].number, let stop = item[1].number else { continue }
                bar(start, stop, y: lanes[1], height: 14, .purple.opacity(item[2].string?.contains("chew") == true ? 0.5 : 0.15))
            }
            let plotTop = lanes[2], plotHeight: CGFloat = 70
            context.stroke(Path { $0.move(to: CGPoint(x: 0, y: plotTop + plotHeight * (1 - threshold)))
                $0.addLine(to: CGPoint(x: size.width, y: plotTop + plotHeight * (1 - threshold))) },
                           with: .color(.secondary), style: StrokeStyle(lineWidth: 0.5, dash: [4, 3]))
            var line = Path()
            var drawing = false
            for (t, p) in zip(timeline.tEnd, timeline.probability) {
                guard let p else { drawing = false; continue }
                let point = CGPoint(x: x(t - 1.5), y: plotTop + plotHeight * (1 - p))
                if drawing { line.addLine(to: point) } else { line.move(to: point); drawing = true }
            }
            context.stroke(line, with: .color(.accentColor), lineWidth: 1.2)
            for bout in timeline.bouts where bout.count == 2 {
                bar(bout[0] - 1.5, bout[1] - 1.5, y: lanes[3], height: 12, .green)
            }
            for (index, t) in timeline.tEnd.enumerated() {
                guard let p = timeline.probability[index] else { continue }
                let label = timeline.label[index]
                if label == 0 && p >= threshold { bar(t - 1.75, t - 1.25, y: lanes[4], height: 12, .red) }
                if label == 1 && p < threshold { bar(t - 1.75, t - 1.25, y: lanes[4], height: 12, .orange) }
            }
        }
        .overlay(alignment: .topLeading) {
            VStack(alignment: .leading, spacing: 0) {
                Text("labels").frame(height: 18, alignment: .top)
                Text("instructions").frame(height: 18, alignment: .top)
                Text("probability").frame(height: 76, alignment: .top)
                Text("bouts").frame(height: 16, alignment: .top)
                Text("errors").frame(height: 16, alignment: .top)
            }
            .font(.system(size: 8)).foregroundStyle(.secondary).allowsHitTesting(false)
        }
        .background(Color.black.opacity(0.04), in: RoundedRectangle(cornerRadius: 6))
    }
}
