import SwiftUI

/// Full-screen camera preview with the current instruction, a large
/// countdown, the next activity, overall progress and quality indicators.
struct GuidedRecordingView: View {
    @ObservedObject var services: CollectServices
    @ObservedObject var controller: GuidedRecordingController
    @ObservedObject private var quality: QualityMonitor
    @State private var showAirPods = false
    /// White frame around the preview with the display at full brightness, to light the face.
    @AppStorage("GuidedScreenLight") private var screenLight = false
    @State private var brightnessBeforeLight: CGFloat?
    /// Called with a follow-up run (repeat of affected segments) or nil to close.
    let onClose: (GuidedRecordingController?) -> Void

    init(services: CollectServices, controller: GuidedRecordingController,
         onClose: @escaping (GuidedRecordingController?) -> Void) {
        self.services = services
        self.controller = controller
        self.quality = controller.quality
        self.onClose = onClose
    }

    var body: some View {
        ZStack {
            if let session = controller.recorder.captureSession {
                CameraPreviewView(session: session).ignoresSafeArea()
            } else {
                Color.black.ignoresSafeArea()
            }
            if screenLight {
                Rectangle().strokeBorder(.white, lineWidth: 56).ignoresSafeArea().allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
            switch controller.stage {
            case .completed(let folder, let sessionID):
                GuidedCompletionView(services: services, controller: controller, folder: folder,
                                     sessionID: sessionID, onClose: onClose)
            case .failed(let message):
                failure(message)
            default:
                overlay
            }
        }
        .onAppear {
            controller.prepare()
            applyScreenLight(screenLight)
        }
        .onChange(of: screenLight) { _, on in applyScreenLight(on) }
        .onDisappear {
            applyScreenLight(false)
            controller.teardown()
            controller.recorder.stopCameraPreview()
        }
        .statusBarHidden(controller.stage == .recording)
        .sheet(isPresented: $showAirPods) {
            AirPodsConnectionSheet(controller: controller.recorder, keepCameraPreview: true)
        }
    }

    private var overlay: some View {
        VStack(spacing: 12) {
            header
            Spacer()
            if controller.stage == .ready {
                preflight
            } else {
                instruction
            }
            Spacer()
            footer
        }
        .padding()
        .foregroundStyle(.white)
    }

    private var header: some View {
        VStack(spacing: 8) {
            HStack {
                Text(controller.entry.definition.title).font(.headline)
                if controller.isRepeat { Text("· repeat").font(.subheadline) }
                Spacer()
                Button("Screen light", systemImage: screenLight ? "lightbulb.fill" : "lightbulb") { screenLight.toggle() }
                    .labelStyle(.iconOnly)
                    .buttonStyle(.bordered).tint(screenLight ? .yellow : .white)
                    .accessibilityIdentifier("guidedScreenLightButton")
                Button("AirPods", systemImage: "airpodspro") { showAirPods = true }
                    .buttonStyle(.bordered).tint(.white)
                    .accessibilityIdentifier("guidedAirPodsButton")
                if controller.stage == .recording {
                    Label("REC", systemImage: "record.circle").font(.caption.bold()).foregroundStyle(.red)
                }
            }
            ProgressView(value: controller.engine.progress).tint(.white)
                .accessibilityIdentifier("guidedProgress")
            QualityChips(snapshot: quality.snapshot, faceRequired: controller.entry.definition.camera.faceRequired,
                         minBrightness: controller.entry.definition.camera.minBrightness)
        }
        .padding(12)
        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 14))
    }

    @ViewBuilder
    private var instruction: some View {
        let engine = controller.engine
        VStack(spacing: 10) {
            if engine.phase == .leadIn {
                Text("Get ready").font(.title2.bold())
                Text("\(Int(ceil(controller.timeRemaining)))")
                    .font(.system(size: 120, weight: .heavy, design: .rounded)).monospacedDigit()
                if let next = engine.nextActivity { Text("First: \(next.instruction)").font(.title3) }
            } else if let activity = engine.currentActivity {
                Text(activity.instruction)
                    .font(.system(size: 34, weight: .bold, design: .rounded))
                    .multilineTextAlignment(.center)
                    .accessibilityIdentifier("guidedInstruction")
                Text("\(Int(ceil(controller.timeRemaining)))")
                    .font(.system(size: 110, weight: .heavy, design: .rounded)).monospacedDigit()
                    .opacity(engine.isTimerPaused ? 0.4 : 1)
                    .accessibilityIdentifier("guidedCountdown")
                if engine.isTimerPaused, let issue = engine.activeIssues.first {
                    Label(issue.prompt, systemImage: "pause.circle.fill")
                        .font(.title3.bold())
                        .padding(12)
                        .background(.orange, in: RoundedRectangle(cornerRadius: 12))
                        .accessibilityIdentifier("qualityPrompt")
                    Text("The timer is paused; recording continues.").font(.footnote)
                } else if activity.onQualityLoss == .mark, let issue = engine.activeIssues.first {
                    Label(issue.prompt, systemImage: "exclamationmark.circle").font(.headline)
                        .padding(8).background(.yellow.opacity(0.7), in: RoundedRectangle(cornerRadius: 10))
                }
            } else {
                ProgressView().tint(.white)
                Text(controller.stage == .finishing ? "Saving…" : "Starting…").font(.title3)
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity)
        .background(.black.opacity(0.4), in: RoundedRectangle(cornerRadius: 20))
    }

    private var preflight: some View {
        VStack(spacing: 14) {
            let issues = controller.preflightIssues
            if issues.isEmpty {
                Label("Ready", systemImage: "checkmark.seal.fill").font(.title2.bold()).foregroundStyle(.green)
            } else {
                ForEach(issues, id: \.self) { Label($0, systemImage: "exclamationmark.triangle.fill").font(.headline) }
            }
            if controller.isDimButUsable {
                Text("Lighting is dim, but your face is visible, so you can record. Screen light can help.")
                    .font(.footnote).multilineTextAlignment(.center)
            }
            Toggle("Screen light", isOn: $screenLight).tint(.yellow)
            Toggle("Sound and haptic cues", isOn: $controller.cuesEnabled).tint(.green)
            Button {
                controller.begin()
            } label: {
                Text("Start Experiment").font(.title2.bold()).frame(maxWidth: .infinity).padding(.vertical, 10)
            }
            .buttonStyle(.borderedProminent)
            .disabled(controller.entry.definition.airpods.required && !controller.recorder.isMotionAvailable)
            .accessibilityIdentifier("beginGuidedButton")
            Text("A \(Int(controller.entry.definition.leadInS))-second countdown starts after recording begins.")
                .font(.caption)
        }
        .padding(20)
        .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 20))
    }

    private var footer: some View {
        HStack {
            if let next = controller.engine.nextActivity, controller.engine.phase != .leadIn {
                Text("Next: \(next.instruction)").font(.callout)
            }
            Spacer()
            if controller.stage == .recording {
                Button("End early", role: .destructive) { controller.endEarly() }
                    .buttonStyle(.bordered).tint(.red)
            } else if controller.stage == .ready {
                Button("Cancel") { onClose(nil) }.buttonStyle(.bordered).tint(.white)
            }
        }
        .padding(12)
        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 14))
    }

    private func applyScreenLight(_ on: Bool) {
        guard let screen = UIApplication.shared.connectedScenes.compactMap({ ($0 as? UIWindowScene)?.screen }).first
        else { return }
        if on {
            if brightnessBeforeLight == nil { brightnessBeforeLight = screen.brightness }
            screen.brightness = 1
        } else if let previous = brightnessBeforeLight {
            screen.brightness = previous
            brightnessBeforeLight = nil
        }
    }

    private func failure(_ message: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill").font(.largeTitle).foregroundStyle(.orange)
            Text(message).multilineTextAlignment(.center)
            Button("Close") { onClose(nil) }.buttonStyle(.borderedProminent)
        }
        .padding(24)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
        .padding()
    }
}

struct QualityChips: View {
    let snapshot: QualitySnapshot
    let faceRequired: Bool
    let minBrightness: Double

    var body: some View {
        HStack(spacing: 8) {
            if faceRequired {
                chip("Face", "face.smiling", snapshot.faceVisible)
                lightChip
            }
            chip("AirPods", "airpodspro", snapshot.motionFresh)
            chip("Still", "iphone", snapshot.phoneMotionG.map { $0 <= 0.25 })
        }
        .font(.caption2.bold())
    }

    /// Dim light is only a problem when it also hides the face.
    @ViewBuilder
    private var lightChip: some View {
        if let brightness = snapshot.brightness, brightness < minBrightness {
            if snapshot.faceVisible == false {
                chip("Light", "sun.min", false)
            } else {
                chip("Dim", "sun.min", nil, color: .yellow, state: "dim but usable")
            }
        } else {
            chip("Light", "sun.max", snapshot.brightness.map { _ in true })
        }
    }

    private func chip(_ title: String, _ icon: String, _ ok: Bool?, color: Color? = nil, state: String? = nil) -> some View {
        Label(title, systemImage: icon)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background((color ?? (ok == nil ? Color.gray : ok! ? Color.green : Color.red)).opacity(0.75), in: Capsule())
            .accessibilityLabel("\(title) \(state ?? (ok == nil ? "unknown" : ok! ? "good" : "problem"))")
    }
}
