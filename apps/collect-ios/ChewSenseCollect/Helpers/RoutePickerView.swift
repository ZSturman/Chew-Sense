import SwiftUI

/// Collect's adapter for the connection UI shared with Live.
struct AirPodsConnectionSheet: View {
    @ObservedObject var controller: RecorderController
    var keepCameraPreview = false

    var body: some View {
        AirPodsConnectionPanel(
            motionReady: controller.isMotionAvailable,
            audioRouteName: controller.audioRouteName,
            message: controller.airPodsMessage,
            recordingRecoveryMessage: controller.isRecording
                ? "Video keeps recording. The instruction timer resumes when fresh motion returns. Missing samples remain marked as a gap."
                : nil,
            onPlaySound: { controller.playAirPodsTestSound() },
            onRetry: { controller.retryAirPodsMotion() },
            onPrepare: {
                if controller.isRecording { controller.checkIMUReadiness() }
                else { controller.prepare(startPreview: keepCameraPreview) }
            }
        )
    }
}
