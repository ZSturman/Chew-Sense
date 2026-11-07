#if os(iOS)
import SwiftUI
import AVKit
import CoreMotion

struct RoutePickerView: UIViewRepresentable {
    var tintColor: UIColor = .label
    func makeUIView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView()
        view.prioritizesVideoDevices = false
        view.tintColor = tintColor
        view.activeTintColor = .systemBlue
        return view
    }
    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {}
}

/// Identical connection controls for Collect and Live; each app owns its stream.
struct AirPodsConnectionPanel: View {
    let motionReady: Bool
    let audioRouteName: String
    let message: String
    let recordingRecoveryMessage: String?
    let onPlaySound: () -> Void
    let onRetry: () -> Void
    let onPrepare: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Label(motionReady ? "Motion is ready" : "Waiting for AirPods motion",
                          systemImage: motionReady ? "checkmark.circle.fill" : "airpodspro")
                        .foregroundStyle(motionReady ? .green : .orange)
                    LabeledContent("Audio output", value: audioRouteName)
                    HStack {
                        Text("Choose AirPods")
                        Spacer()
                        RoutePickerView().frame(width: 48, height: 44)
                            .accessibilityLabel("Choose AirPods audio output")
                            .accessibilityIdentifier("airPodsRoutePicker")
                    }
                    Button("Play test sound", systemImage: "speaker.wave.2", action: onPlaySound)
                        .accessibilityIdentifier("airPodsTestSound")
                    Button("Retry motion connection", systemImage: "arrow.clockwise", action: onRetry)
                        .accessibilityIdentifier("retryAirPodsMotion")
                    Text(message).font(.callout)
                } header: { Text("Connect or reconnect") }
                  footer: {
                    Text("Wear your AirPods, select them under Choose AirPods, then retry motion. Play a short test sound to check the route. If they are not listed, pair them in Settings → Bluetooth and return here.")
                }
                if let recordingRecoveryMessage {
                    Section {
                        Text(recordingRecoveryMessage)
                    }
                }
                if CMHeadphoneMotionManager.authorizationStatus() == .denied || CMHeadphoneMotionManager.authorizationStatus() == .restricted {
                    Section("Motion access is blocked") {
                        Button("Open app settings") {
                            if let url = URL(string: UIApplication.openSettingsURLString) {
                                UIApplication.shared.open(url)
                            }
                        }
                    }
                }
            }
            .navigationTitle("AirPods")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
        .onAppear(perform: onPrepare)
    }
}
#endif
