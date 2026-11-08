import SwiftUI

/// The loop service's shared "where we are" message (`project_status.json`,
/// also sent as `pipeline.project` in `GET /v1/status`). Collect, Live and
/// Review show it verbatim, so the phase and next steps never disagree.
nonisolated struct ProjectStatus: Codable, Equatable, Sendable {
    struct Stage: Codable, Equatable, Identifiable, Sendable {
        let id: String
        let title: String
        let app: String
        let current: Bool
        let detail: String
    }

    struct Step: Codable, Equatable, Identifiable, Sendable {
        let id: String
        let stage: String
        let app: String
        let title: String
        let detail: String
        let optional: Bool?
        let experimentId: String?
        let sessionId: String?
    }

    struct Check: Codable, Equatable, Identifiable, Sendable {
        let id: String
        let question: String
        let state: String
        let answer: String
    }

    let schemaVersion: Int
    let updatedAt: String
    let phase: String
    let headline: String
    let summary: String
    let stages: [Stage]
    let steps: [Step]
    let checks: [Check]

    static func decode(_ data: Data) throws -> ProjectStatus {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(ProjectStatus.self, from: data)
    }

    func encoded() throws -> Data {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return try encoder.encode(self)
    }

    var updatedDate: Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: updatedAt) ?? ISO8601DateFormatter().date(from: updatedAt)
    }

    /// Where a step is done, from the point of view of `app`.
    static func place(of stepApp: String, in app: String) -> String {
        if stepApp == app { return "This app" }
        switch stepApp {
        case "collect": return "In Collect"
        case "review": return "In Review on the Mac"
        case "live": return "In Live"
        default: return "Automatic on the Mac"
        }
    }
}

/// Stage strip, headline, numbered next steps and the three readiness answers.
struct ProjectStatusCard: View {
    let status: ProjectStatus
    /// "collect", "live" or "review": steps for this app are marked "This app".
    let app: String
    var stale: Bool = false
    var maxSteps: Int = 4

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 4) {
                ForEach(Array(status.stages.enumerated()), id: \.element.id) { index, stage in
                    Text("\(index + 1) \(stage.title)")
                        .font(.caption2.weight(stage.current ? .bold : .regular))
                        .padding(.horizontal, 6).padding(.vertical, 3)
                        .background(stage.current ? Color.accentColor.opacity(0.2) : Color.secondary.opacity(0.1),
                                    in: Capsule())
                        .foregroundStyle(stage.current ? Color.accentColor : Color.secondary)
                        .help(stage.detail)
                        .accessibilityLabel("\(stage.title): \(stage.detail)\(stage.current ? ", current step" : "")")
                }
            }
            Text(status.headline).font(.headline)
            Text(status.summary).font(.subheadline).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 8) {
                ForEach(Array(status.steps.prefix(maxSteps).enumerated()), id: \.element.id) { index, step in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("\(index + 1).").font(.subheadline.weight(.semibold)).monospacedDigit()
                        VStack(alignment: .leading, spacing: 2) {
                            Text(step.title).font(.subheadline.weight(.semibold))
                            Text(step.detail).font(.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Text(ProjectStatus.place(of: step.app, in: app))
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(step.app == app ? Color.accentColor : Color.secondary)
                        }
                    }
                }
            }
            Divider()
            ForEach(status.checks) { check in
                VStack(alignment: .leading, spacing: 2) {
                    Label(check.question, systemImage: icon(check.state))
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(color(check.state))
                    Text(check.answer).font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if let date = status.updatedDate {
                Text((stale ? "Last received " : "Updated ") + date.formatted(.relative(presentation: .named))
                     + ". Same message in Collect, Review and Live.")
                    .font(.caption2).foregroundStyle(stale ? Color.orange : Color.secondary)
            }
        }
        .accessibilityIdentifier("projectStatusCard")
    }

    private func icon(_ state: String) -> String {
        switch state {
        case "ok": "checkmark.circle.fill"
        case "partial": "circle.lefthalf.filled"
        default: "exclamationmark.circle.fill"  // needs action before moving on
        }
    }

    private func color(_ state: String) -> Color {
        switch state {
        case "ok": .green
        case "partial": .orange
        default: .red
        }
    }
}
