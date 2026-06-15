//
//  HeadOrientationView.swift
//  ChewSenseDebuggingModel
//
//  A compact 3D head widget built from SceneKit primitives that rotates in
//  real-time based on the AirPod attitude quaternion. No 3D asset required.
//

import SwiftUI
import SceneKit

#if os(iOS)

/// SwiftUI wrapper around an `SCNView` that renders a simple abstract head
/// (sphere cranium + nose + ears) and applies the latest AirPod quaternion
/// to a pivot node on every update.
struct HeadOrientationView: UIViewRepresentable {

    let attitude: HeadAttitude

    func makeUIView(context: Context) -> SCNView {
        let scnView = SCNView()
        scnView.scene = context.coordinator.scene
        scnView.backgroundColor = UIColor.clear
        scnView.allowsCameraControl = false
        scnView.antialiasingMode = .multisampling4X
        scnView.rendersContinuously = false   // only redraw when we set needs display
        scnView.isPlaying = false
        return scnView
    }

    func updateUIView(_ scnView: SCNView, context: Context) {
        let q = attitude
        // SCNVector4 is (x, y, z, w) for axis-angle, but SCNNode.orientation
        // also accepts a quaternion in (x, y, z, w) form directly.
        context.coordinator.pivotNode.orientation = SCNVector4(
            Float(q.x),
            Float(q.y),
            Float(q.z),
            Float(q.w)
        )
        scnView.setNeedsDisplay()
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    // MARK: - Coordinator

    final class Coordinator: NSObject {

        let scene: SCNScene
        let pivotNode: SCNNode

        override init() {
            scene = SCNScene()
            pivotNode = SCNNode()
            super.init()
            buildHead()
            setupLighting()
            setupCamera()
        }

        // MARK: Build geometry

        private func buildHead() {
            // --- Cranium ---
            let craniumGeo = SCNSphere(radius: 0.5)
            craniumGeo.firstMaterial?.diffuse.contents = UIColor(
                red: 0.90, green: 0.76, blue: 0.65, alpha: 1.0
            )
            craniumGeo.firstMaterial?.lightingModel = .blinn
            let cranium = SCNNode(geometry: craniumGeo)

            // --- Nose ---
            let noseGeo = SCNSphere(radius: 0.09)
            noseGeo.firstMaterial?.diffuse.contents = UIColor(
                red: 0.82, green: 0.65, blue: 0.55, alpha: 1.0
            )
            let nose = SCNNode(geometry: noseGeo)
            nose.position = SCNVector3(0.0, -0.12, 0.50)

            // --- Ears (left / right) ---
            let earGeo = SCNCapsule(capRadius: 0.07, height: 0.28)
            earGeo.firstMaterial?.diffuse.contents = UIColor(
                red: 0.85, green: 0.70, blue: 0.60, alpha: 1.0
            )

            let leftEar = SCNNode(geometry: earGeo)
            leftEar.position = SCNVector3(-0.56, 0.0, 0.0)
            leftEar.eulerAngles = SCNVector3(0, 0, Float.pi / 2)

            let rightEar = SCNNode(geometry: earGeo)
            rightEar.position = SCNVector3(0.56, 0.0, 0.0)
            rightEar.eulerAngles = SCNVector3(0, 0, Float.pi / 2)

            // --- Front-face marker (tiny flat disc so you can tell which
            //     way the head is facing when it rotates) ---
            let markerGeo = SCNCylinder(radius: 0.06, height: 0.01)
            markerGeo.firstMaterial?.diffuse.contents = UIColor.systemOrange
            let marker = SCNNode(geometry: markerGeo)
            marker.position = SCNVector3(0.0, 0.35, 0.35)
            marker.eulerAngles = SCNVector3(Float.pi / 2, 0, 0)

            pivotNode.addChildNode(cranium)
            pivotNode.addChildNode(nose)
            pivotNode.addChildNode(leftEar)
            pivotNode.addChildNode(rightEar)
            pivotNode.addChildNode(marker)

            scene.rootNode.addChildNode(pivotNode)
        }

        private func setupLighting() {
            // Ambient light — base illumination so back of head isn't black
            let ambient = SCNNode()
            ambient.light = SCNLight()
            ambient.light!.type = .ambient
            ambient.light!.intensity = 400
            ambient.light!.color = UIColor.white
            scene.rootNode.addChildNode(ambient)

            // Directional light — gives depth/shape
            let directional = SCNNode()
            directional.light = SCNLight()
            directional.light!.type = .directional
            directional.light!.intensity = 800
            directional.light!.color = UIColor.white
            directional.eulerAngles = SCNVector3(-Float.pi / 4, Float.pi / 6, 0)
            scene.rootNode.addChildNode(directional)
        }

        private func setupCamera() {
            let cameraNode = SCNNode()
            cameraNode.camera = SCNCamera()
            cameraNode.camera!.fieldOfView = 45
            cameraNode.position = SCNVector3(0, 0, 2.4)
            scene.rootNode.addChildNode(cameraNode)
        }
    }
}

#else

// macOS / Catalyst stub so the file compiles on all targets
struct HeadOrientationView: View {
    let attitude: HeadAttitude
    var body: some View {
        Image(systemName: "head.profile")
            .font(.largeTitle)
            .foregroundColor(.secondary)
    }
}

#endif
