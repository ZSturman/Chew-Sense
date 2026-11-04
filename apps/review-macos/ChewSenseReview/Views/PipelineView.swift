import SwiftUI

/// At-a-glance state of the guided collection loop. Details stay in
/// disclosure groups; the primary view answers "what needs me?".
struct PipelineView: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController
    @StateObject private var store = PipelineStore()

    var body: some View {
        Group {
            if store.loopRoot == nil {
                VStack(spacing: 12) {
                    Text("Choose the ChewSense loop folder (workspace/loop).")
                    Button("Choose Folder…") { store.chooseRoot() }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        statusSection
                        tasksSection
                        requestsSection
                        evaluationSection
                        weaknessesSection
                        trainingSection
                        eatingDetectorSection
                    }
                    .padding(20)
                }
            }
        }
        .frame(minWidth: 620, minHeight: 560)
        .onAppear {
            store.adopt(workspace: state.workspaceRoot)
            store.startPolling()
        }
        .onDisappear { store.stopPolling() }
    }

    // MARK: Status

    private var statusSection: some View {
        let current = store.status["current"] as? [String: Any]
        let pending = store.status["pending"] as? [String] ?? []
        let attention = store.status["attention"] as? [[String: Any]] ?? []
        let completed = store.status["completed"] as? [String] ?? []
        return GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Circle().fill(store.serviceReachable ? Color.green : Color.red).frame(width: 9, height: 9)
                    Text(store.serviceReachable ? "Service running · \(stateText)" : "Service not running")
                        .font(.headline)
                    Spacer()
                    Button("Pause") { Task { await store.control("pause") } }
                        .help("Finish the current step, then wait.")
                    Button("Resume") { Task { await store.control("resume") } }
                    Button("Stop") { Task { await store.control("stop") } }
                        .help("Interrupt Vision now; the step reruns after Resume. Sources are never modified.")
                }
                .disabled(!store.serviceReachable)
                if let current {
                    Text("Now: \(current["step"] as? String ?? "") · \(current["session_id"] as? String ?? "new upload")")
                        .font(.callout)
                }
                Text("\(pending.count) pending · \(completed.count) processed · \(attention.count) need attention")
                    .font(.callout).foregroundStyle(.secondary)
                ForEach(Array(attention.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .top) {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        Text("\(item["session_id"] as? String ?? "upload"): \(item["error"] as? String ?? "")")
                            .font(.caption).textSelection(.enabled)
                        Spacer()
                        if item["state"] as? String == "needs_attention", let job = item["job_id"] as? String {
                            Button("Retry") { Task { await store.retry(job: job) } }
                        }
                    }
                }
                HStack {
                    Button("Pair iPhone…") { Task { await store.openPairing() } }.disabled(!store.serviceReachable)
                    if let code = store.pairingCode {
                        Text("Code: \(code)").font(.title3.monospacedDigit().bold()).textSelection(.enabled)
                        Text("Enter it in Collect → Mac Connection within 10 minutes.").font(.caption)
                    }
                    Spacer()
                    Button("Analyze now") { Task { await store.analyzeNow() } }.disabled(!store.serviceReachable)
                }
                if let message = store.message { Text(message).font(.caption).foregroundStyle(.secondary) }
            }
        } label: { Label("Processing", systemImage: "gearshape.2") }
    }

    private var stateText: String { (store.status["state"] as? String ?? "idle").capitalized }

    // MARK: Review tasks

    private var tasksSection: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                if store.tasks.isEmpty {
                    Text("Nothing needs review.").foregroundStyle(.secondary)
                }
                ForEach(Array(store.tasks.prefix(20).enumerated()), id: \.offset) { _, task in
                    TaskRow(task: task, open: { segment in open(task, at: segment) })
                }
            }
        } label: { Label("Sessions requiring review (\(store.tasks.count))", systemImage: "checklist") }
    }

    private func open(_ task: [String: Any], at segment: Double?) {
        guard let path = task["workspace"] as? String, let id = task["session_id"] as? String else { return }
        state.setWorkspaceRoot(URL(fileURLWithPath: path))
        guard let session = state.sessions.first(where: { $0.id == id }) else {
            store.message = "\(id) is not reviewable yet in \(URL(fileURLWithPath: path).lastPathComponent)."
            return
        }
        state.loadSession(session)
        if let segment {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { playback.seek(to: segment) }
        }
        NSApp.windows.first { $0.identifier?.rawValue != "pipeline" && $0.canBecomeMain }?.makeKeyAndOrderFront(nil)
    }

    // MARK: Requests

    private var requestsSection: some View {
        let entries = store.catalog["experiments"] as? [[String: Any]] ?? []
        return GroupBox {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(entries.enumerated()), id: \.offset) { _, entry in
                    let definition = entry["definition"] as? [String: Any] ?? [:]
                    let progress = entry["progress"] as? [String: Any] ?? [:]
                    let requests = entry["requests"] as? [[String: Any]] ?? []
                    HStack(alignment: .firstTextBaseline) {
                        Text(definition["title"] as? String ?? "?").font(.body.weight(.medium))
                        Text((entry["effective_priority"] as? String ?? "").capitalized)
                            .font(.caption2.bold()).foregroundStyle(.secondary)
                        Spacer()
                        Text("\(progress["completed_valid"] as? Int ?? 0)/\(progress["target"] as? Int ?? 0)")
                            .monospacedDigit()
                    }
                    if let reason = requests.first?["summary"] as? String {
                        Text("Requested: \(reason)").font(.caption).foregroundStyle(.secondary)
                    }
                }
                Text("Catalog v\(store.catalog["catalog_version"] as? Int ?? 0) synchronizes to Collect automatically.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        } label: { Label("Required recordings", systemImage: "video.badge.plus") }
    }

    // MARK: Evaluation

    private var evaluationSection: some View {
        let evaluation = store.report["evaluation"] as? [String: Any] ?? [:]
        let tolerances = evaluation["tolerances"] as? [String: [String: Any]] ?? [:]
        let acceptance = store.report["acceptance"] as? [String: Any] ?? [:]
        let checks = acceptance["checks"] as? [[String: Any]] ?? []
        let dataset = store.report["dataset"] as? [String: Any] ?? [:]
        let training = dataset["training"] as? [String: Any] ?? [:]
        return GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                Text("\(evaluation["evaluation_sessions"] as? Int ?? 0) independent (blind) evaluation sessions · "
                     + "\(training["verified_sessions"] as? Int ?? 0) verified training sessions")
                if tolerances.isEmpty {
                    Text("No independent evaluation yet. Complete a blind review of an evaluation session.")
                        .foregroundStyle(.secondary)
                } else {
                    Grid(alignment: .leading, horizontalSpacing: 16) {
                        GridRow { Text("Tolerance"); Text("Precision"); Text("Recall"); Text("F1"); Text("Gold events") }
                            .font(.caption.bold())
                        ForEach(tolerances.keys.sorted(by: >), id: \.self) { key in
                            let row = tolerances[key] ?? [:]
                            GridRow {
                                Text("±\(Int((Double(key) ?? 0) * 1000)) ms")
                                Text(Self.percent(row["precision"]))
                                Text(Self.percent(row["recall"]))
                                Text(Self.percent(row["f1"]))
                                Text("\(row["gold_events"] as? Int ?? 0)")
                            }
                            .monospacedDigit()
                        }
                    }
                }
                ForEach(Array(checks.enumerated()), id: \.offset) { _, check in
                    let state = check["state"] as? String ?? ""
                    Label("\(check["id"] as? String ?? ""): \(state.replacingOccurrences(of: "_", with: " "))",
                          systemImage: state == "met" ? "checkmark.circle" : state == "not_met" ? "xmark.circle" : "questionmark.circle")
                        .font(.caption)
                        .foregroundStyle(state == "met" ? .green : state == "not_met" ? .red : .secondary)
                }
                Text("Research-stage targets only. Metrics are Vision proposals vs frozen blind human review; they are not motion-model or production accuracy.")
                    .font(.caption2).foregroundStyle(.secondary)
                DisclosureGroup("By instructed activity and food") {
                    let byActivity = evaluation["by_instructed_activity"] as? [String: [String: Any]] ?? [:]
                    ForEach(byActivity.keys.sorted(), id: \.self) { kind in
                        let row = byActivity[kind] ?? [:]
                        Text("\(kind): \(row["fp"] as? Int ?? 0) false · \(row["fn"] as? Int ?? 0) missed · recall \(Self.percent(row["recall"]))")
                            .font(.caption).monospacedDigit()
                    }
                }
            }
        } label: { Label("Model performance", systemImage: "chart.bar") }
    }

    // MARK: Weaknesses and training

    private var weaknessesSection: some View {
        let weaknesses = store.report["weaknesses"] as? [[String: Any]] ?? []
        let lm = (store.report["lmstudio"] as? [String: Any])?["summary"] as? [String: Any]
        return GroupBox {
            VStack(alignment: .leading, spacing: 6) {
                if let overview = lm?["overview"] as? String {
                    Text(overview).font(.callout)
                    Text("Summary written by the local LM Studio model from the evidence below; advisory only.")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                ForEach(Array(weaknesses.prefix(12).enumerated()), id: \.offset) { _, weakness in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(weakness["title"] as? String ?? "").font(.body.weight(.medium))
                        Text("\((weakness["category"] as? String ?? "").replacingOccurrences(of: "_", with: " ")) · action: \((weakness["action"] as? String ?? "").replacingOccurrences(of: "_", with: " "))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        } label: { Label("Current weaknesses", systemImage: "scope") }
    }

    private var trainingSection: some View {
        let training = store.report["training"] as? [String: Any] ?? [:]
        let readiness = training["readiness"] as? [String: Any] ?? [:]
        let gates = readiness["gates"] as? [[String: Any]] ?? []
        let stopping = store.report["stopping"] as? [String: Any] ?? [:]
        return GroupBox {
            VStack(alignment: .leading, spacing: 4) {
                Text(readiness["ready"] as? Bool == true
                     ? "Experimental training gates pass. Promotion still requires explicit approval."
                     : "Training is gated; no model will be trained yet.")
                ForEach(Array(gates.enumerated()), id: \.offset) { _, gate in
                    Label(gate["detail"] as? String ?? "", systemImage: gate["passed"] as? Bool == true ? "checkmark" : "xmark")
                        .font(.caption)
                        .foregroundStyle(gate["passed"] as? Bool == true ? Color.primary : Color.secondary)
                }
                ForEach(stopping["notes"] as? [String] ?? [], id: \.self) { Text($0).font(.caption2).foregroundStyle(.secondary) }
            }
        } label: { Label("Training and evaluation readiness", systemImage: "lock.shield") }
    }

    // MARK: Chewing-bout detector

    /// Experimental motion-only detector trained on human coarse chewing spans by
    /// `chewsense-motion eating-experiment`. Descriptive only; it gates nothing.
    private var eatingDetectorSection: some View {
        let eating = store.report["eating_detector"] as? [String: Any] ?? [:]
        let test = eating["test"] as? [String: Any]
        let dev = eating["development"] as? [String: Any]
        let counts = eating["counts"] as? [String: Any] ?? [:]
        let weakest = eating["weakest_contexts"] as? [[String: Any]] ?? []
        let recommendations = eating["recommendations"] as? [[String: Any]] ?? []
        return GroupBox {
            VStack(alignment: .leading, spacing: 4) {
                Text(eating["status"] as? String ?? "Run Analyze to load the latest chewing-bout experiment.")
                if eating["available"] as? Bool == true {
                    Text("Labels: human coarse chewing spans, not individual chews or bites. Run \(eating["run_id"] as? String ?? "—"), \(counts["sessions"] as? Int ?? 0) recordings, one wearer.")
                        .font(.caption).foregroundStyle(.secondary)
                    if let test {
                        Text("Locked test · precision \(Self.score(test["precision"])) · recall \(Self.score(test["recall"])) · false-positive rate \(Self.percent(test["false_positive_rate"])) · bouts \(test["bouts_detected"] as? Int ?? 0)/\(test["bouts_reference"] as? Int ?? 0)")
                            .font(.caption.monospacedDigit())
                    }
                    if let dev {
                        Text("Development (out-of-fold) · F1 \(Self.score(dev["f1"])) · recall \(Self.score(dev["recall"])) · false-positive rate \(Self.percent(dev["false_positive_rate"]))")
                            .font(.caption.monospacedDigit())
                    }
                    ForEach(Array(weakest.prefix(3).enumerated()), id: \.offset) { _, item in
                        Label("\(item["context"] as? String ?? "") · \(Self.percent(item["false_positive_rate"])) false positives (\(item["split"] as? String ?? ""))",
                              systemImage: "exclamationmark.triangle")
                            .font(.caption)
                    }
                    if !recommendations.isEmpty {
                        Text("Record next").font(.caption.weight(.semibold)).padding(.top, 4)
                        ForEach(Array(recommendations.prefix(3).enumerated()), id: \.offset) { _, item in
                            Text("• \(item["title"] as? String ?? "") — \(item["reason"] as? String ?? "")")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if let path = eating["report_path"] as? String {
                        Button("Open experiment report") { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
                            .padding(.top, 4)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { Label("AirPods chewing detector (experimental)", systemImage: "waveform.path.ecg") }
    }

    static func score(_ value: Any?) -> String {
        guard let number = value as? Double else { return "—" }
        return String(format: "%.2f", number)
    }

    static func percent(_ value: Any?) -> String {
        guard let number = value as? Double else { return "—" }
        return String(format: "%.0f%%", number * 100)
    }
}

private struct TaskRow: View {
    let task: [String: Any]
    let open: (Double?) -> Void
    @State private var expanded = false

    var body: some View {
        let segments = task["segments"] as? [[String: Any]] ?? []
        let blocked = task["blocked"] as? Bool ?? false
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Image(systemName: task["kind"] as? String == "blind_review" ? "eye.slash" : "wand.and.stars")
                VStack(alignment: .leading) {
                    Text("\(task["title"] as? String ?? "") — \(task["session_id"] as? String ?? "")").font(.body.weight(.medium))
                    Text(task["detail"] as? String ?? "").font(.caption).foregroundStyle(.secondary)
                    if let sync = task["sync_note"] as? String { Text(sync).font(.caption2).foregroundStyle(.secondary) }
                }
                Spacer()
                Button("Open") { open(nil) }.disabled(blocked && task["kind"] as? String != "blind_review")
            }
            if !segments.isEmpty {
                DisclosureGroup("\(segments.count) highlighted spans", isExpanded: $expanded) {
                    ForEach(Array(segments.prefix(15).enumerated()), id: \.offset) { _, segment in
                        let start = segment["start_ts"] as? Double ?? 0
                        Button {
                            open(start)
                        } label: {
                            Text(String(format: "%6.1fs  ", start) + (segment["reason"] as? String ?? ""))
                                .font(.caption.monospacedDigit())
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.plain)
                        .disabled(blocked)
                    }
                }
                .font(.caption)
            }
        }
    }
}
