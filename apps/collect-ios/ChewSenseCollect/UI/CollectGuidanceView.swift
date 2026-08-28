import SwiftUI

enum CollectGuidanceStage: Equatable {
    case library
    case capture(motionAvailable: Bool)
    case coarseReview
    case export

    var title: String {
        switch self {
        case .library: "Start a capture"
        case .capture: "Check capture readiness"
        case .coarseReview: "Complete coarse review"
        case .export: "Export the completed bundle"
        }
    }

    var nextStep: String {
        switch self {
        case .library:
            "Tap New, choose whether this is Eating or Not Eating, and keep your face in the camera frame."
        case .capture(let motionAvailable):
            motionAvailable
                ? "AirPods motion is ready. Choose Eating or Not Eating to start; Stop saves the session."
                : "Connect AirPods and wait for motion data before starting. Recording is intentionally disabled until it is available."
        case .coarseReview:
            "Mark each chewing interval as Start Chewing then Stop Chewing. Finish only after every interval is closed."
        case .export:
            "Use Share to copy the complete session folder to your workspace. The next stage is Vision proposal generation on the Mac."
        }
    }

    var doneWhen: String {
        switch self {
        case .library: "Done when the camera screen is open."
        case .capture: "Done when the session is saved with video and motion CSV."
        case .coarseReview: "Done when Labelling finished is enabled and selected."
        case .export: "Done when the destination has the session folder, session.json, video, and motion CSV."
        }
    }
}

struct CollectGuidanceView: View {
    let stage: CollectGuidanceStage

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Label(stage.title, systemImage: "list.number")
                .font(.subheadline.weight(.semibold))
            Text(stage.nextStep)
            Text(stage.doneWhen)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .font(.footnote)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(.blue.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
        .accessibilityIdentifier("collectGuidanceCard")
    }
}
