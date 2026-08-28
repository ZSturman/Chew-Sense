import SwiftUI

enum LiveGuidanceStage: Equatable {
    case prepare
    case record
    case export

    var title: String {
        switch self {
        case .prepare: "Prepare a field test"
        case .record: "Record with complete context"
        case .export: "Hand off the field-test bundle"
        }
    }

    var nextStep: String {
        switch self {
        case .prepare:
            "Tap New, select the activity and meal texture, then confirm the camera and AirPods are ready."
        case .record:
            "Start recording only after the activity and texture are correct. Use feedback to flag predictions; it does not create ground truth."
        case .export:
            "Export the finished bundle to the computer pipeline. Review creates authoritative chew and food-entry decisions, not this app."
        }
    }

    var doneWhen: String {
        switch self {
        case .prepare: "Done when the New Recording screen shows the intended activity and texture."
        case .record: "Done when the session is stopped and its model telemetry is saved."
        case .export: "Done when the bundle is available to the Mac pipeline for audit and review."
        }
    }
}

struct LiveGuidanceView: View {
    let stage: LiveGuidanceStage

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
        .accessibilityIdentifier("liveGuidanceCard")
    }
}
