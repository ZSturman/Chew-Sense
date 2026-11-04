import SwiftUI

/// Explains what the experiment needs, then offers one Start button.
struct ExperimentPreparationView: View {
    @ObservedObject var services: CollectServices
    let entry: CatalogEntry
    @State private var run: GuidedRecordingController?

    var body: some View {
        let d = entry.definition
        List {
            Section("Purpose") {
                Text(d.purpose)
                Text("Requested because: \(entry.whyNeeded)").font(.footnote).foregroundStyle(.secondary)
                LabeledContent("Recordings", value: "\(services.catalog.completed(for: entry)) of \(entry.progress.target) completed")
                LabeledContent("Duration", value: "About \(ExperimentRow.duration(d))")
                LabeledContent("Status", value: d.required ? "Required" : "Optional · \(entry.effectivePriority) priority")
            }
            Section("Preparation") {
                ForEach(d.preparation, id: \.self) { Label($0, systemImage: "checkmark.circle") }
            }
            Section("You will need") {
                LabeledContent("AirPods", value: d.airpods.required ? d.airpods.configuration.replacingOccurrences(of: "_", with: " ") : "Optional")
                LabeledContent("Food or drink", value: d.food.needsFoodOrDrink ? (d.food.recommended ?? d.food.kind) : "None")
                if !d.equipment.isEmpty { LabeledContent("Equipment", value: d.equipment.joined(separator: ", ")) }
                Text("Camera: \(d.camera.position)").font(.footnote)
            }
            if !d.safetyNotes.isEmpty {
                Section("Safety") {
                    ForEach(d.safetyNotes, id: \.self) { Label($0, systemImage: "exclamationmark.triangle").foregroundStyle(.orange) }
                }
            }
            Section("Sequence") {
                ForEach(d.activities) { activity in
                    HStack {
                        Text(activity.instruction)
                        Spacer()
                        Text("\(Int(activity.durationS)) s").monospacedDigit().foregroundStyle(.secondary)
                    }
                }
            }
            Section {
                Button {
                    run = GuidedRecordingController(entry: entry, recorder: services.recorder, quality: services.quality,
                                                    catalog: services.catalog, uploads: services.uploads)
                } label: {
                    Text("Start Experiment").font(.title3.bold()).frame(maxWidth: .infinity).padding(.vertical, 8)
                }
                .buttonStyle(.borderedProminent)
                .listRowInsets(EdgeInsets())
                .accessibilityIdentifier("startExperimentButton")
            } footer: {
                Text("Instructions advance automatically. You will not need to touch the phone until the end.")
            }
        }
        .navigationTitle(d.title)
        .navigationBarTitleDisplayMode(.inline)
        .fullScreenCover(item: $run) { controller in
            GuidedRecordingView(services: services, controller: controller, onClose: { next in run = next })
        }
    }
}

extension GuidedRecordingController: Identifiable {
    var id: ObjectIdentifier { ObjectIdentifier(self) }
}
