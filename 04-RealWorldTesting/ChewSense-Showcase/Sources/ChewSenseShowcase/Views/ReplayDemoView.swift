import SwiftUI

struct ReplayDemoView: View {
    @ObservedObject var session: SessionStore
    let onShowReport: () -> Void

    @StateObject private var engine = InferenceEngine()
    @StateObject private var player = ReplayPlayer()
    @State private var selectedSample: SampleSession = .eating
    @State private var loadError: String?

    enum SampleSession: String, CaseIterable, Identifiable {
        case eating = "Eating sample"
        case notEating = "Not-eating sample"
        var id: String { rawValue }
        var resource: String {
            switch self {
            case .eating:    return "eating-sample"
            case .notEating: return "not-eating-sample"
            }
        }
        var subtitle: String {
            switch self {
            case .eating:    return "Labeled chewing session — model should fire."
            case .notEating: return "Walking / talking baseline — model should stay quiet."
            }
        }
        var symbol: String {
            switch self {
            case .eating:    return "fork.knife"
            case .notEating: return "figure.walk"
            }
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.padLg) {
                header

                pickerCards

                if let error = engine.errorMessage {
                    ContentUnavailableView(
                        "Replay unavailable",
                        systemImage: "exclamationmark.triangle",
                        description: Text(error)
                    )
                    .frame(maxWidth: .infinity, minHeight: 180)
                } else {
                    Theme.card {
                        VStack(alignment: .leading, spacing: 16) {
                            HStack {
                                Text("Probability")
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
                            HStack(spacing: 16) {
                                legendDot(color: Theme.accent, label: "Smoothed")
                                legendDot(color: .primary.opacity(0.7), label: "Raw")
                                legendDot(color: Theme.danger, label: "High \(format(RuntimeConfig.highThreshold))")
                                legendDot(color: Theme.warn, label: "Low \(format(RuntimeConfig.lowThreshold))")
                            }
                            .font(.caption).foregroundStyle(.secondary)
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
                    StatTile(label: "Bites", value: "\(engine.bites.count)", systemImage: "circle.grid.2x2", accent: Theme.accent)
                    StatTile(label: "Position", value: format(player.currentTime) + "s",
                             systemImage: "timer", accent: .blue)
                    StatTile(label: "Duration", value: format(player.totalTime) + "s",
                             systemImage: "clock", accent: .secondary)
                }

                transport

                if let loadError {
                    Text(loadError).foregroundStyle(.red).font(.callout)
                }
            }
            .padding(Theme.padLg)
        }
        .onAppear { wireEngine(); loadSelected() }
        .onChange(of: selectedSample) { _, _ in loadSelected() }
        .onDisappear { player.pause() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Replay Demo")
                .font(.system(.title, design: .rounded).weight(.semibold))
            Text("The same inference path that runs on AirPods, replayed against a recorded session. No hardware required.")
                .foregroundStyle(.secondary)
        }
    }

    private var pickerCards: some View {
        HStack(spacing: 12) {
            ForEach(SampleSession.allCases) { item in
                Button {
                    selectedSample = item
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: item.symbol)
                            .font(.title2)
                            .foregroundStyle(item == selectedSample ? .white : Theme.accent)
                            .frame(width: 36)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.rawValue).font(.system(.body, design: .rounded).weight(.semibold))
                            Text(item.subtitle).font(.caption).foregroundStyle(item == selectedSample ? .white.opacity(0.85) : .secondary)
                        }
                        Spacer()
                    }
                    .padding(Theme.pad)
                    .background(item == selectedSample ? Theme.accent : Theme.card,
                                in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
                    .foregroundStyle(item == selectedSample ? Color.white : .primary)
                    .overlay(
                        RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Color.primary.opacity(0.06))
                    )
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var transport: some View {
        Theme.card {
            VStack(spacing: 12) {
                ProgressView(value: player.progress)
                    .tint(Theme.accent)

                HStack(spacing: 16) {
                    Button { player.restart(); engine.reset() } label: {
                        Image(systemName: "backward.end.fill")
                    }

                    Button {
                        if player.isPlaying { player.pause() }
                        else { player.play() }
                    } label: {
                        Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                            .font(.title2)
                            .frame(width: 44, height: 44)
                            .background(Theme.accent, in: Circle())
                            .foregroundStyle(.white)
                    }
                    .buttonStyle(.plain)
                    .disabled(!engine.modelState.isReady)

                    Picker("Speed", selection: $player.speed) {
                        ForEach(ReplayPlayer.Speed.allCases) { speed in
                            Text(speed.label).tag(speed)
                        }
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 220)

                    Spacer()

                    Button {
                        player.pause()
                        let summary = engine.finalizeSummary(label: selectedSample.rawValue)
                        session.latestSummary = summary
                        onShowReport()
                    } label: {
                        Label("Stop & Review", systemImage: "chart.bar.doc.horizontal")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.accent)
                    .disabled(engine.bites.isEmpty && player.currentTime < 1)
                }
            }
        }
    }

    private func wireEngine() {
        player.onSample = { [engine] sample in
            engine.push(sample)
        }
        player.onFinished = { [engine, session, selectedSample] in
            session.latestSummary = engine.finalizeSummary(label: selectedSample.rawValue)
        }
    }

    private func loadSelected() {
        player.pause()
        engine.reset()
        do {
            try player.load(resource: selectedSample.resource)
            loadError = nil
        } catch {
            loadError = "Failed to load sample: \(error.localizedDescription)"
        }
    }

    private func legendDot(color: Color, label: String) -> some View {
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(label)
        }
    }

    private func format(_ t: Double) -> String { String(format: "%.1f", t) }
}
