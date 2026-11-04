//
//  CameraPreviewView.swift
//  ChewSenseCollect
//
//  Created by Zachary Sturman on 11/13/25.
//

import Foundation
import SwiftUI
import AVFoundation

struct CameraPreviewView: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> Preview {
        let v = Preview()
        v.videoPreviewLayer.session = session
        v.videoPreviewLayer.videoGravity = .resizeAspectFill
        v.configureRotation(session: session)
        return v
    }

    func updateUIView(_ uiView: Preview, context: Context) {
        uiView.configureRotation(session: session)
    }

    final class Preview: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var videoPreviewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
        private var rotationCoordinator: AVCaptureDevice.RotationCoordinator?
        private var rotationObservation: NSKeyValueObservation?

        func configureRotation(session: AVCaptureSession) {
            guard rotationCoordinator == nil,
                  let device = (session.inputs.first { $0 is AVCaptureDeviceInput } as? AVCaptureDeviceInput)?.device
            else { return }
            let coordinator = AVCaptureDevice.RotationCoordinator(device: device, previewLayer: videoPreviewLayer)
            rotationCoordinator = coordinator
            rotationObservation = coordinator.observe(\.videoRotationAngleForHorizonLevelPreview, options: [.initial, .new]) { [weak self] coordinator, _ in
                guard let connection = self?.videoPreviewLayer.connection else { return }
                let angle = coordinator.videoRotationAngleForHorizonLevelPreview
                if connection.isVideoRotationAngleSupported(angle) { connection.videoRotationAngle = angle }
            }
        }
    }
}
