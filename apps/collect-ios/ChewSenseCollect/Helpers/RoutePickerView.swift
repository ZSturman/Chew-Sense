//
//  RoutePickerView.swift
//  ChewSenseCollect
//
//  Created by Zachary Sturman on 11/13/25.
//
import SwiftUI
import AVKit
import CoreMotion

struct RoutePickerView: UIViewRepresentable {
    var tintColor: UIColor = .label
    func makeUIView(context: Context) -> AVRoutePickerView {
        let v = AVRoutePickerView()
        v.prioritizesVideoDevices = false
        if #available(iOS 13.0, *) {
            v.activeTintColor = .systemBlue
        }
        v.tintColor = tintColor
        return v
    }

    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {
        // No dynamic updates needed
    }
}

struct AirPodsConnectionSheet: View {
    @ObservedObject var controller: RecorderController
    var keepCameraPreview = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Label(controller.isMotionAvailable ? "Motion is ready" : "Waiting for AirPods motion",
                          systemImage: controller.isMotionAvailable ? "checkmark.circle.fill" : "airpodspro")
                        .foregroundStyle(controller.isMotionAvailable ? .green : .orange)
                    LabeledContent("Audio output", value: controller.audioRouteName)
                    HStack {
                        Text("Choose AirPods")
                        Spacer()
                        RoutePickerView().frame(width: 48, height: 44)
                            .accessibilityLabel("Choose AirPods audio output")
                            .accessibilityIdentifier("airPodsRoutePicker")
                    }
                    Button("Play test sound", systemImage: "speaker.wave.2") { controller.playAirPodsTestSound() }
                        .accessibilityIdentifier("airPodsTestSound")
                    Button("Retry motion connection", systemImage: "arrow.clockwise") { controller.retryAirPodsMotion() }
                        .accessibilityIdentifier("retryAirPodsMotion")
                    Text(controller.airPodsMessage).font(.callout)
                } header: { Text("Connect or reconnect") }
                  footer: { Text("Wear the AirPod, use the output picker to select it, then play the test sound or retry motion. No music app is needed. If it is not listed, pair/connect it in iPad Settings → Bluetooth, then return here.") }
                if controller.isRecording {
                    Section {
                        Text("Video keeps recording. The instruction timer resumes when fresh motion returns; missing samples remain marked as a gap.")
                    }
                }
                if CMHeadphoneMotionManager.authorizationStatus() == .denied || CMHeadphoneMotionManager.authorizationStatus() == .restricted {
                    Section("Motion access is blocked") {
                        Button("Open app settings") {
                            if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                        }
                    }
                }
            }
            .navigationTitle("AirPods")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
        .onAppear {
            if controller.isRecording { controller.checkIMUReadiness() }
            else { controller.prepare(startPreview: keepCameraPreview) }
        }
    }
}
