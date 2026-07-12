//
//  RecordingComposerView.swift
//  ChewSenseLive
//
//  Photo-Booth-style camera surface used to compose a new recording. Shows
//  a live front-camera preview before the user presses Record, then overlays
//  a recording HUD (timer, prediction, probability bars) while recording.
//

import SwiftUI
import AVFoundation
import Combine
import Charts

struct RecordingComposerView: View {
    @ObservedObject var motionManager: MotionManager
    @ObservedObject var config: DetectionConfiguration

    /// Notes the user can edit while composing or recording.
    @Binding var sessionNotes: String

    /// Called when the composer should close (e.g. user cancels). The parent
    /// is responsible for routing to the review pane after a successful stop;
    /// it observes `motionManager.latestCompletedSessionURL` for that.
    let onCancel: () -> Void

    @State private var nowTick: Date = Date()
    @State private var showSensorPanel: Bool = false
    private let timer = Timer.publish(every: 0.1, on: .main, in: .common).autoconnect()

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea(edges: [])

            cameraSurface

            VStack {
                topBar
                Spacer()
                recordingHUD
                controlBar
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 16)
        }
        .navigationTitle("New Recording")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(motionManager.isRunning || motionManager.isFinalizingSession)
        .onAppear {
            #if os(iOS)
            motionManager.prepareCameraPreview()
            #endif
        }
        .onDisappear {
            #if os(iOS)
            motionManager.stopCameraPreview()
            #endif
        }
        .onReceive(timer) { date in
            nowTick = date
        }
    }

    // MARK: - Subviews

    @ViewBuilder
    private var cameraSurface: some View {
        #if os(iOS)
        if let session = motionManager.captureSession {
            CameraPreviewView(session: session)
                .ignoresSafeArea(edges: [])
        } else if AVCaptureDevice.authorizationStatus(for: .video) == .denied ||
                  AVCaptureDevice.authorizationStatus(for: .video) == .restricted {
            VStack(spacing: 16) {
                Image(systemName: "video.slash")
                    .font(.largeTitle)
                    .foregroundColor(.white.opacity(0.7))
                #if targetEnvironment(macCatalyst)
                Text("Camera access was denied.\nGo to System Settings → Privacy & Security → Camera and enable ChewSenseLive.")
                    .font(.callout)
                    .foregroundColor(.white.opacity(0.7))
                    .multilineTextAlignment(.center)
                Button("Open System Settings") {
                    if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera") {
                        UIApplication.shared.open(url)
                    }
                }
                .buttonStyle(.borderedProminent)
                #else
                Text("Camera access is required to record sessions.")
                    .font(.callout)
                    .foregroundColor(.white.opacity(0.7))
                    .multilineTextAlignment(.center)
                Button("Open Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        UIApplication.shared.open(url)
                    }
                }
                .buttonStyle(.borderedProminent)
                #endif
            }
            .padding()
        } else {
            VStack(spacing: 12) {
                ProgressView()
                Text(motionManager.statusText)
                    .font(.caption)
                    .foregroundColor(.white.opacity(0.7))
            }
        }
        #else
        VStack(spacing: 12) {
            Image(systemName: "video.slash")
                .font(.largeTitle)
                .foregroundColor(.white.opacity(0.7))
            Text("Camera preview is unavailable on this platform.")
                .foregroundColor(.white.opacity(0.7))
        }
        #endif
    }

    private var topBar: some View {
        HStack {
            if motionManager.isRunning {
                Label("REC", systemImage: "record.circle.fill")
                    .labelStyle(.titleAndIcon)
                    .font(.caption.weight(.bold))
                    .foregroundColor(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(Color.red.opacity(0.85))
                    .clipShape(Capsule())

                Text(elapsedString)
                    .font(.title3.monospacedDigit().weight(.semibold))
                    .foregroundColor(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(Color.black.opacity(0.55))
                    .clipShape(Capsule())
            } else {
                Text(motionManager.statusText)
                    .font(.caption)
                    .foregroundColor(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(Color.black.opacity(0.45))
                    .clipShape(Capsule())
            }

            Spacer()

            Button {
                onCancel()
            } label: {
                Image(systemName: "xmark")
                    .foregroundColor(.white)
                    .padding(10)
                    .background(Color.black.opacity(0.55))
                    .clipShape(Circle())
            }
            .disabled(motionManager.isFinalizingSession)
            .opacity(motionManager.isRunning ? 0 : 1)
            .accessibilityLabel("Close")
        }
    }

    @ViewBuilder
    private var recordingHUD: some View {
        if motionManager.isRunning {
            VStack(alignment: .leading, spacing: 10) {

                // MARK: Expandable sensor panel
                if showSensorPanel {
                    HStack(alignment: .top, spacing: 10) {
                        MotionWaveformView(samples: motionManager.recentSamples)
                            .frame(maxWidth: .infinity, minHeight: 110, maxHeight: 130)

                        HeadOrientationView(attitude: motionManager.latestAttitude)
                            .frame(width: 110, height: 110)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                }

                // MARK: Chewing label + panel toggle + raw/smooth numbers
                HStack {
                    Label(
                        motionManager.isChewing ? "Chewing" : "Not Chewing",
                        systemImage: motionManager.isChewing ? "circle.fill" : "circle"
                    )
                    .font(.callout.weight(.semibold))
                    .foregroundColor(motionManager.isChewing ? .orange : .blue)

                    Spacer()

                    Text(String(format: "raw %.2f  smooth %.2f",
                                motionManager.latestProb,
                                motionManager.latestSmoothed))
                        .font(.caption.monospacedDigit())
                        .foregroundColor(.white.opacity(0.85))

                    // Toggle sensor panel
                    Button {
                        withAnimation(.spring(response: 0.35, dampingFraction: 0.75)) {
                            showSensorPanel.toggle()
                        }
                    } label: {
                        Image(systemName: showSensorPanel ? "waveform.path.ecg" : "waveform")
                            .font(.callout)
                            .foregroundColor(showSensorPanel ? .orange : .white.opacity(0.75))
                            .padding(6)
                            .background(Color.white.opacity(0.12))
                            .clipShape(Circle())
                    }
                    .accessibilityLabel(showSensorPanel ? "Hide sensor panel" : "Show sensor panel")
                }

                ProbabilityBar(
                    value: motionManager.latestSmoothed,
                    high: RuntimeConfig.highThreshold,
                    low: RuntimeConfig.lowThreshold
                )
                .frame(height: 14)
            }
            .padding(12)
            .background(Color.black.opacity(0.55))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .padding(.bottom, 12)
        }
    }

    private var controlBar: some View {
        VStack(spacing: 12) {
            if !motionManager.isRunning && !motionManager.isFinalizingSession {
                presetCompactPicker
            }
            HStack {
                Spacer()
                recordButton
                Spacer()
            }
        }
    }

    /// Inline preset picker shown above the record button so the user
    /// always picks an activity tag *before* recording. Detection params
    /// are owned by the deployed model and are not editable here.
    private var presetCompactPicker: some View {
        VStack(spacing: 8) {
            Menu {
                ForEach(ActivityPreset.allCases) { preset in
                    Button {
                        config.activityPreset = preset
                    } label: {
                        Label(preset.displayName, systemImage: preset.systemImage)
                    }
                }
            } label: {
                HStack {
                    Image(systemName: config.activityPreset.systemImage)
                    Text(config.activityPreset.displayName)
                        .lineLimit(1)
                    Image(systemName: "chevron.down")
                        .font(.caption)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(Color.black.opacity(0.55))
                .foregroundColor(.white)
                .clipShape(Capsule())
            }

            if config.activityPreset == .custom {
                TextField("Describe the activity", text: $config.customActivity)
                    .textFieldStyle(.roundedBorder)
                    .padding(.horizontal, 24)
            }

            Toggle(isOn: $config.goldLockCandidate) {
                Text("Mark as benchmark candidate")
                    .font(.caption.weight(.semibold))
                    .foregroundColor(.white)
            }
            .toggleStyle(.switch)
            .padding(.horizontal, 24)

            if !config.isPresetReadyToRecord {
                Text("Add a description to enable recording.")
                    .font(.caption)
                    .foregroundColor(.orange)
            }
        }
    }

    private var recordButton: some View {
        Button(action: toggleRecording) {
            ZStack {
                Circle()
                    .stroke(Color.white, lineWidth: 4)
                    .frame(width: 78, height: 78)

                if motionManager.isFinalizingSession {
                    ProgressView()
                        .tint(.white)
                } else if motionManager.isRunning {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color.red)
                        .frame(width: 30, height: 30)
                } else {
                    Circle()
                        .fill(Color.red)
                        .frame(width: 64, height: 64)
                }
            }
        }
        .buttonStyle(.plain)
        .disabled(motionManager.isFinalizingSession || (!motionManager.isRunning && !config.isPresetReadyToRecord))
        .accessibilityLabel(motionManager.isRunning ? "Stop Recording" : "Start Recording")
    }

    // MARK: - Helpers

    private func toggleRecording() {
        if motionManager.isRunning {
            motionManager.stop()
        } else {
            // Detection params come from the bundled model; the only thing
            // the UI provides is the dataset metadata for this session.
            guard config.isPresetReadyToRecord else { return }
            var metadata = MotionManager.SessionMetadataInput()
            metadata.testScenario = config.testScenario
            metadata.sessionNotes = sessionNotes
            metadata.configJSON = config.toSessionConfigJSON()
            metadata.activitySlug = config.activitySlug
            metadata.activityLabel = config.activityLabel
            metadata.expectedLabelClass = config.activityPreset.expectedLabelClass.rawValue
            metadata.goldLockCandidate = config.goldLockCandidate
            motionManager.start(metadata: metadata)
        }
    }

    private var elapsedString: String {
        guard let started = motionManager.recordingStartedAt else { return "00:00" }
        // Reference nowTick so the timer publisher actually drives recomputation.
        let elapsed = max(0, nowTick.timeIntervalSince(started))
        let total = Int(elapsed)
        return String(format: "%02d:%02d", total / 60, total % 60)
    }
}
