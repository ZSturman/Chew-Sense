import SwiftUI
import AVFoundation
import Combine

/// A model check has one setup, one start/stop control and an honest signal state.
struct RecordingComposerView: View {
    @ObservedObject var motionManager: MotionManager
    @ObservedObject var config: DetectionConfiguration
    @Binding var sessionNotes: String
    let onCancel: () -> Void

    @State private var nowTick = Date()
    @State private var showAirPods = false
    @Environment(\.scenePhase) private var scenePhase
    private let timer = Timer.publish(every: 0.25, on: .main, in: .common).autoconnect()
    private var active: Bool {
        motionManager.isRunning || motionManager.isStartingSession || motionManager.isFinalizingSession
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                connectionCard
                if active {
                    predictionCard
                    signalDetails
                } else {
                    setup
                }
                if config.recordReferenceVideo { referencePreview }
            }
            .padding(20)
        }
        .background(Color.gray.opacity(0.06))
        .navigationTitle("Model check")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(active)
        .toolbar {
            if !active {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel", action: onCancel) }
            }
            ToolbarItem(placement: .primaryAction) {
                Button("AirPods", systemImage: "airpodspro") { showAirPods = true }
            }
        }
        .safeAreaInset(edge: .bottom) { recordingControl }
        .sheet(isPresented: $showAirPods) { LiveAirPodsConnectionSheet(motionManager: motionManager) }
        .onAppear {
            motionManager.prepareAirPodsConnection()
            #if os(iOS)
            if config.recordReferenceVideo { motionManager.prepareCameraPreview() }
            #endif
        }
        .onDisappear {
            #if os(iOS)
            motionManager.stopCameraPreview()
            #endif
        }
        .onReceive(timer) { nowTick = $0 }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { motionManager.prepareAirPodsConnection() }
        }
        .onChange(of: config.recordReferenceVideo) { _, enabled in
            guard !active else { return }
            #if os(iOS)
            if enabled { motionManager.prepareCameraPreview() }
            else { motionManager.stopCameraPreview() }
            #endif
        }
    }

    private var connectionCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(motionManager.hasFreshMotion ? "AirPods motion connected" : "Connect your AirPods",
                      systemImage: motionManager.hasFreshMotion ? "checkmark.circle.fill" : "airpodspro")
                    .font(.headline)
                    .foregroundStyle(motionManager.hasFreshMotion ? .green : .orange)
                Spacer()
                if motionManager.isRunning { Text(elapsed).monospacedDigit() }
            }
            Text(motionManager.hasFreshMotion ? "Fresh samples are arriving from your AirPods." : motionManager.airPodsMessage)
                .font(.subheadline).foregroundStyle(.secondary)
            if !motionManager.hasFreshMotion {
                Button("Connect or retry AirPods") { showAirPods = true }
                    .buttonStyle(.bordered)
            }
            if active {
                Text("\(motionManager.capturedMotionSamples.formatted()) motion samples saved")
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16).background(.background, in: RoundedRectangle(cornerRadius: 16))
    }

    private var setup: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text("See what the model detects").font(.title2.bold())
                Text("Start a short test, then compare the predictions with what you actually did. The app saves the motion and both model outputs.")
                    .foregroundStyle(.secondary)
            }
            Picker("Model under test", selection: $config.eatingDetectorMode) {
                ForEach(EatingDetectorMode.allCases) { Text($0.title).tag($0) }
            }
            Text(config.eatingDetectorMode.explanation).font(.caption).foregroundStyle(.secondary)
            Picker("Activity (optional)", selection: $config.activityPreset) {
                Text("Unspecified / other").tag(ActivityPreset.custom)
                ForEach(ActivityPreset.allCases.filter { $0 != .custom }) { Text($0.displayName).tag($0) }
            }
            if config.activityPreset == .custom {
                TextField("Optional activity note", text: $config.customActivity)
                    .textFieldStyle(.roundedBorder)
            }
            Toggle("Save reference video", isOn: $config.recordReferenceVideo)
            Text("Detection uses only AirPods motion. Video is optional evidence for later label review.")
                .font(.caption).foregroundStyle(.secondary)
            DisclosureGroup("Research options") {
                VStack(alignment: .leading, spacing: 12) {
                    Picker("Food texture", selection: $config.mealTexture) {
                        ForEach(MealTexture.allCases) { Text($0.title).tag($0) }
                    }
                    Toggle("Benchmark candidate", isOn: Binding(
                        get: { config.recordReferenceVideo && config.goldLockCandidate },
                        set: { config.goldLockCandidate = $0 }))
                        .disabled(!config.recordReferenceVideo)
                    TextField("Session notes", text: $sessionNotes, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                    Text("Activity selections are context, not verified labels. Motion-only checks stay unlabelled.")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(.top, 10)
            }
        }
        .padding(16).background(.background, in: RoundedRectangle(cornerRadius: 16))
    }

    private var predictionCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Model prediction").font(.subheadline).foregroundStyle(.secondary)
            if !motionManager.hasFreshMotion {
                Label("Waiting for motion", systemImage: "pause.circle")
                    .font(.title2.bold())
                Text("No prediction is valid while the motion stream is missing.")
                    .foregroundStyle(.secondary)
            } else if !hasDrivingPrediction {
                ProgressView("Gathering enough motion…")
            } else {
                Label(motionManager.isChewing ? "Chewing detected" : "No chewing detected",
                      systemImage: motionManager.isChewing ? "waveform" : "circle")
                    .font(.title2.bold())
                    .foregroundStyle(motionManager.isChewing ? .orange : .primary)
                Text(config.eatingDetectorMode.title).font(.caption).foregroundStyle(.secondary)
                if config.eatingDetectorMode == .bundledChewNetWithShadow,
                   motionManager.exportedDetectorProbability != nil {
                    LabeledContent("Experimental shadow", value: motionManager.exportedDetectorChewing ? "Chewing" : "No chewing")
                        .font(.subheadline)
                }
            }
            Text("Predictions are unverified until compared with independent labels.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16).background(.background, in: RoundedRectangle(cornerRadius: 16))
    }

    private var hasDrivingPrediction: Bool {
        switch config.eatingDetectorMode {
        case .bundledChewNetWithShadow: return motionManager.hasChewNetPrediction
        case .exportedDetectorExperimental: return motionManager.exportedDetectorProbability != nil
        }
    }

    private var signalDetails: some View {
        DisclosureGroup("Signal details") {
            VStack(alignment: .leading, spacing: 12) {
                Text(motionManager.statusText).font(.caption)
                MotionWaveformView(samples: motionManager.recentSamples).frame(height: 100)
                LabeledContent("Model score", value: String(format: "%.3f", motionManager.latestSmoothed))
                if let score = motionManager.exportedDetectorProbability {
                    LabeledContent("Experimental score", value: String(format: "%.3f", score))
                }
            }.padding(.top, 12)
        }
        .padding(16).background(.background, in: RoundedRectangle(cornerRadius: 16))
    }

    @ViewBuilder private var referencePreview: some View {
        #if os(iOS)
        if let session = motionManager.captureSession {
            CameraPreviewView(session: session)
                .frame(height: 220).clipShape(RoundedRectangle(cornerRadius: 16))
        } else {
            Text("Reference video: \(motionManager.statusText)").font(.caption).foregroundStyle(.secondary)
        }
        #endif
    }

    private var recordingControl: some View {
        Button(action: toggleRecording) {
            HStack {
                if motionManager.isStartingSession || motionManager.isFinalizingSession { ProgressView() }
                Label(controlTitle, systemImage: motionManager.isRunning ? "stop.fill" : "record.circle")
            }
            .font(.headline).frame(maxWidth: .infinity).padding(.vertical, 10)
        }
        .buttonStyle(.borderedProminent)
        .tint(motionManager.isRunning ? .red : .blue)
        .disabled(motionManager.isStartingSession || motionManager.isFinalizingSession
                  || (!motionManager.isRunning && !motionManager.hasFreshMotion))
        .accessibilityIdentifier("liveCheckStartStop")
        .padding().background(.bar)
    }

    private var controlTitle: String {
        if motionManager.isFinalizingSession { return "Saving check…" }
        if motionManager.isStartingSession { return "Starting…" }
        return motionManager.isRunning ? "Stop and save" : "Start model check"
    }

    private func toggleRecording() {
        if motionManager.isRunning { motionManager.stop(); return }
        var metadata = MotionManager.SessionMetadataInput()
        metadata.testScenario = config.testScenario
        metadata.sessionNotes = sessionNotes
        metadata.configJSON = config.toSessionConfigJSON()
        metadata.activitySlug = config.activitySlug
        metadata.activityLabel = config.activityLabel
        metadata.expectedLabelClass = config.effectiveExpectedLabelClass
        metadata.goldLockCandidate = config.recordReferenceVideo && config.goldLockCandidate
        metadata.mealTexture = config.mealTexture
        metadata.recordReferenceVideo = config.recordReferenceVideo
        motionManager.start(metadata: metadata)
    }

    private var elapsed: String {
        let seconds = max(0, Int(nowTick.timeIntervalSince(motionManager.recordingStartedAt ?? nowTick)))
        return String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }
}
