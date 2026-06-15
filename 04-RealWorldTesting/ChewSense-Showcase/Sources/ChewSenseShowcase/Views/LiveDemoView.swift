import SwiftUI

struct LiveDemoView: View {
    @ObservedObject var session: SessionStore
    let onShowReport: () -> Void

    @StateObject private var engine = InferenceEngine()
    @StateObject private var motion = LiveMotionSource()
    @State private var elapsed: TimeInterval = 0
    @State private var startWall: Date?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.padLg) {
                header
                preflight

                if let error = engine.errorMessage {
                    ContentUnavailableView(
                        "Inference unavailable",
                        systemImage: "exclamationmark.triangle",
                        description: Text(error)
                    )
                    .frame(maxWidth: .infinity, minHeight: 180)
                } else {
                    Theme.card {
                        VStack(alignment: .leading, spacing: 16) {
                            HStack {
                                Text("Live probability")
                                    .font(.system(.title3, design: .rounded).weight(.semibold))
                                Spacer()
                                StatePill(state: engine.state)
                            }
                            ProbabilityBar(
                                probability: engine.probability,
                                smoothed: engine.smoothed,
                                high: RuntimeConfig.highThreshold,
                                low: RuntimeConfig.lowThreshold
                            )
                        }
                    }

                    Theme.card {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Acceleration magnitude")
                                .font(.caption).foregroundStyle(.secondary).textCase(.uppercase)
                            WaveformView(samples: engine.waveform)
                        }
                    }
                }

                HStack(spacing: 12) {
                    StatTile(label: "Bites", value: "\(engine.bites.count)", systemImage: "circle.grid.2x2")
                    StatTile(label: "Elapsed", value: String(format: "%.0f s", elapsed),
                             systemImage: "clock", accent: .blue)
                    StatTile(label: "Status", value: motion.connectionStatus,
                             systemImage: motion.isRunning ? "antenna.radiowaves.left.and.right" : "antenna.radiowaves.left.and.right.slash",
                             accent: motion.isRunning ? Theme.accent : .secondary)
                }

                controls
            }
            .padding(Theme.padLg)
        }
        .onAppear { wire() }
        .onDisappear { stop() }
        .onReceive(Timer.publish(every: 0.5, on: .main, in: .common).autoconnect()) { _ in
            if motion.isRunning, let s = startWall { elapsed = Date().timeIntervalSince(s) }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Live Demo")
                .font(.system(.title, design: .rounded).weight(.semibold))
            Text("Connect AirPods Pro / Max, press Start, and chew. Real-time inference on-device.")
                .foregroundStyle(.secondary)
        }
    }

    private var preflight: some View {
        Theme.card {
            VStack(alignment: .leading, spacing: 8) {
                Text("Pre-flight").font(.caption).foregroundStyle(.secondary).textCase(.uppercase)
                checklistRow(ok: motion.isAvailable,
                             label: motion.isAvailable
                                ? "AirPods Pro / Max detected"
                                : "AirPods Pro / Max not detected — connect to enable Start")
                checklistRow(
                    ok: engine.modelState.isReady,
                    label: engine.modelState.isReady
                        ? "ChewNet model loaded (\(NormalizationConstants.MODEL_ID))"
                        : engine.modelState.message
                )
                checklistRow(ok: true, label: "Inference path matches Replay Demo")
            }
        }
    }

    private func checklistRow(ok: Bool, label: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(ok ? Theme.accent : Theme.warn)
            Text(label).font(.callout)
            Spacer()
        }
    }

    private var controls: some View {
        Theme.card {
            HStack(spacing: 16) {
                if motion.isActive {
                    Button(role: .destructive) {
                        stopAndReport()
                    } label: {
                        Label("Stop & Review", systemImage: "stop.fill")
                            .frame(minWidth: 160)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.danger)
                } else {
                    Button {
                        start()
                    } label: {
                        Label("Start", systemImage: "play.fill")
                            .frame(minWidth: 160)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.accent)
                    .disabled(!motion.isAvailable || !engine.modelState.isReady)
                }

                Button {
                    engine.reset()
                    elapsed = 0
                    startWall = motion.isActive ? Date() : nil
                } label: {
                    Label("Reset", systemImage: "arrow.counterclockwise")
                }
                Spacer()
            }
        }
    }

    private func wire() {
        motion.onSample = { [engine] sample in
            engine.push(sample)
        }
    }

    private func start() {
        engine.reset()
        startWall = Date()
        elapsed = 0
        motion.start()
    }

    private func stop() {
        motion.stop()
    }

    private func stopAndReport() {
        motion.stop()
        let summary = engine.finalizeSummary(label: "Live session")
        session.latestSummary = summary
        onShowReport()
    }
}
