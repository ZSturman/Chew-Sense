import SwiftUI

/// Automatic completeness check, repeat-only-affected-segments, the optional
/// no-food confirmation and Send to Mac.
struct GuidedCompletionView: View {
    @ObservedObject var services: CollectServices
    @ObservedObject var controller: GuidedRecordingController
    let folder: URL
    let sessionID: String
    let onClose: (GuidedRecordingController?) -> Void
    @State private var attested = false
    @State private var attestError: String?

    var body: some View {
        let validation = controller.validation
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if validation?.complete == true {
                    Label("Recording complete", systemImage: "checkmark.seal.fill")
                        .font(.title2.bold()).foregroundStyle(.green)
                        .accessibilityIdentifier("guidedComplete")
                } else {
                    Label("Recording saved, but incomplete", systemImage: "exclamationmark.triangle.fill")
                        .font(.title2.bold()).foregroundStyle(.orange)
                        .accessibilityIdentifier("guidedIncomplete")
                    Text("Nothing was discarded. The recording is kept and marked with what failed.")
                        .font(.footnote)
                }
                ForEach(Array((validation?.checks ?? []).enumerated()), id: \.offset) { _, check in
                    Label(check.message, systemImage: check.passed ? "checkmark.circle" : "xmark.circle")
                        .foregroundStyle(check.passed ? Color.primary : Color.red)
                        .font(.callout)
                }
                Text("Instruction times are stored as what you were asked to do, not as verified labels.")
                    .font(.caption).foregroundStyle(.secondary)

                if controller.entry.definition.labeling.wearerNoFoodAttestation {
                    GroupBox("Confirm") {
                        if attested {
                            Label("Saved: you ate and chewed nothing during this recording.", systemImage: "checkmark")
                        } else {
                            Text("Did you eat, drink or chew anything (including gum) at any point during this recording?")
                            Button("No — nothing at all") {
                                do { try controller.attestNoFood(folder: folder); attested = true }
                                catch { attestError = error.localizedDescription }
                            }
                            .buttonStyle(.bordered)
                            .accessibilityIdentifier("attestNoFoodButton")
                            Text("If you did, skip this; the recording will be reviewed instead.").font(.caption)
                            if let attestError { Text(attestError).font(.caption).foregroundStyle(.red) }
                        }
                    }
                }

                UploadStatusView(services: services, folder: folder)

                if let repeatIDs = validation?.repeatActivityIDs, !repeatIDs.isEmpty {
                    Button {
                        onClose(GuidedRecordingController(
                            entry: controller.entry, recorder: services.recorder, quality: services.quality,
                            catalog: services.catalog, uploads: services.uploads,
                            onlyActivityIDs: repeatIDs, repeatOf: sessionID))
                    } label: {
                        Label("Repeat only the affected segments (\(repeatIDs.count))", systemImage: "arrow.counterclockwise")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("repeatSegmentsButton")
                }
                Button { onClose(nil) } label: { Text("Done").frame(maxWidth: .infinity) }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("guidedDoneButton")
            }
            .padding(20)
        }
        .background(.regularMaterial)
    }
}

/// "Send to Mac" with progress and processing status for one recording.
struct UploadStatusView: View {
    @ObservedObject var services: CollectServices
    let folder: URL
    @State private var showCoarseReview = false

    var body: some View {
        let item = services.uploads.item(for: folder)
        VStack(alignment: .leading, spacing: 6) {
            if UploadManager.needsCoarseReview(folder) {
                Text("Next: watch the recording and mark when you actually chewed. The instructions do not create labels.")
                Button("Review chewing intervals") { showCoarseReview = true }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("reviewGuidedCoarseButton")
                Text("Finish labelling before sending. This keeps the Mac's original recording and labels together.")
                    .font(.caption)
            } else if services.connection.paired == nil {
                Label("Pair with your Mac to send recordings automatically.", systemImage: "link")
                    .font(.footnote)
            } else if let item {
                switch item.state {
                case .uploading, .preparing:
                    ProgressView(value: item.progress) { Text("Sending to Mac…") }
                case .received, .processing, .processed:
                    Label(item.message ?? "Received by the Mac", systemImage: item.state == .processed ? "checkmark.circle.fill" : "tray.and.arrow.down")
                case .queued:
                    Label(item.message ?? "Waiting for the Mac…", systemImage: "clock")
                case .needsAttention, .failed:
                    Label(item.message ?? "Upload failed", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                    sendButton(title: "Try again")
                }
            } else {
                sendButton(title: "Send to Mac")
            }
        }
        .font(.callout)
        .sheet(isPresented: $showCoarseReview) {
            RecordingDetailView(folder: folder)
                .environmentObject(services)
                .environmentObject(services.recorder)
        }
    }

    private func sendButton(title: String) -> some View {
        Button { services.uploads.send(folder) } label: {
            Label(title, systemImage: "paperplane.fill").frame(maxWidth: .infinity)
        }
        .buttonStyle(.borderedProminent)
        .accessibilityIdentifier("sendToMacButton")
    }
}
