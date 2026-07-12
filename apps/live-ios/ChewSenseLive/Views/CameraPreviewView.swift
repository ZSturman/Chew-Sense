//
//  CameraPreviewView.swift
//  ChewSenseLive
//
//  Live front-camera preview backed by the debug recorder capture session.
//

import AVFoundation
import SwiftUI
import UIKit

struct CameraPreviewView: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> Preview {
        let view = Preview()
        view.videoPreviewLayer.videoGravity = .resizeAspectFill
        applyOrientation(to: view)
        // Assign the session on a background queue to avoid a priority inversion:
        // the setter synchronizes with AVFoundation's internal default-QoS queues,
        // blocking the main thread. AVCaptureVideoPreviewLayer.session is thread-safe.
        DispatchQueue.global(qos: .userInitiated).async { [session] in
            view.videoPreviewLayer.session = session
        }
        return view
    }

    func updateUIView(_ uiView: Preview, context: Context) {
        applyOrientation(to: uiView)
    }

    private func applyOrientation(to view: Preview) {
        guard let connection = view.videoPreviewLayer.connection else { return }
        // Match DebugVideoRecorder's pinned movie-output orientation so the
        // live preview matches the recorded video on each platform.
        #if targetEnvironment(macCatalyst)
        if #available(iOS 17.0, *) {
            connection.videoRotationAngle = 0
        } else {
            connection.videoOrientation = .landscapeRight
        }
        #else
        if #available(iOS 17.0, *) {
            connection.videoRotationAngle = 90
        } else {
            connection.videoOrientation = .portrait
        }
        #endif
    }

    final class Preview: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }

        var videoPreviewLayer: AVCaptureVideoPreviewLayer {
            layer as! AVCaptureVideoPreviewLayer
        }
    }
}
