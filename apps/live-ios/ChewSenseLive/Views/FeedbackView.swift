//
//  FeedbackView.swift
//  ChewSenseLive
//
//  Inline controls for logging prediction failures into the active
//  field-test session bundle.
//

import SwiftUI

struct FeedbackBar: View {
    let isChewing: Bool
    let feedbackCount: Int
    let onSubmit: (FeedbackEventType, String) -> Void

    @State private var showNoteSheet = false
    @State private var pendingEvent: FeedbackEventType?
    @State private var noteText = ""
    @State private var lastFeedbackTime: Date = .distantPast

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Feedback")
                    .font(.headline)
                Spacer()
                Text("\(feedbackCount)")
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.secondary)
            }

            HStack(spacing: 8) {
                feedbackButton(.falsePositive, tint: .red)
                feedbackButton(.falseNegative, tint: .orange)
                feedbackButton(.correct, tint: .green)
            }

            Text("Predicted: \(isChewing ? "chewing" : "idle")")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .sheet(isPresented: $showNoteSheet) {
            VStack(spacing: 16) {
                Text(pendingEvent?.title ?? "Feedback")
                    .font(.headline)

                TextField("Optional note", text: $noteText)
                    .textFieldStyle(.roundedBorder)
                    .padding(.horizontal)

                HStack {
                    Button("Cancel") {
                        showNoteSheet = false
                        noteText = ""
                    }
                    Spacer()
                    Button("Submit") {
                        submitPending()
                    }
                    .buttonStyle(.borderedProminent)
                }
                .padding(.horizontal)
            }
            .padding()
            .frame(minWidth: 320)
        }
    }

    private func feedbackButton(_ event: FeedbackEventType, tint: Color) -> some View {
        Button {
            guard Date().timeIntervalSince(lastFeedbackTime) > 1.0 else { return }
            pendingEvent = event
            if event == .correct {
                submit(event: event, note: "")
            } else {
                showNoteSheet = true
            }
        } label: {
            Text(event.title)
                .font(.caption)
                .fontWeight(.semibold)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(tint.opacity(0.14))
                .foregroundColor(tint)
                .cornerRadius(8)
        }
        .buttonStyle(.plain)
    }

    private func submitPending() {
        guard let event = pendingEvent else { return }
        submit(event: event, note: noteText)
        showNoteSheet = false
        noteText = ""
    }

    private func submit(event: FeedbackEventType, note: String) {
        lastFeedbackTime = Date()
        onSubmit(event, note)
    }
}
