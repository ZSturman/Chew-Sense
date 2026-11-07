//
//  RoutePickerView.swift
//  ChewSenseLive
//
//  System route picker for manually selecting AirPods or another audio route.
//

import SwiftUI

struct LiveAirPodsConnectionSheet: View {
    @ObservedObject var motionManager: MotionManager

    var body: some View {
        AirPodsConnectionPanel(
            motionReady: motionManager.hasFreshMotion,
            audioRouteName: motionManager.audioRouteName,
            message: motionManager.airPodsMessage,
            recordingRecoveryMessage: motionManager.isRunning
                ? "The recording stays open during a dropout. Missing motion remains marked as a gap. Predictions resume when the model has enough fresh motion."
                : nil,
            onPlaySound: { motionManager.playAirPodsTestSound() },
            onRetry: { motionManager.retryAirPodsMotion() },
            onPrepare: { motionManager.prepareAirPodsConnection() }
        )
    }
}
