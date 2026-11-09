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

/// Links between the Chew Sense apps. Each app registers its own scheme:
/// `chewsense-collect://experiment/<id>` and `chewsense-collect://status` (iPhone),
/// `chewsense-live://status` (iPhone), and
/// `chewsense-review://session/<id>?workspace=blind|assisted` (Mac).
/// Links only navigate: none starts a recording, a sensor or a model check.
nonisolated enum ChewSenseLink: Equatable, Sendable {
    case status
    case experiment(String)
    case session(String, workspace: String)

    static let schemes = ["collect": "chewsense-collect", "live": "chewsense-live", "review": "chewsense-review"]

    static func url(app: String, _ link: ChewSenseLink) -> URL? {
        guard let scheme = schemes[app] else { return nil }
        var parts = URLComponents()
        parts.scheme = scheme
        switch link {
        case .status:
            parts.host = "status"
        case .experiment(let id):
            parts.host = "experiment"
            parts.path = "/" + id
        case .session(let id, let workspace):
            parts.host = "session"
            parts.path = "/" + id
            parts.queryItems = [URLQueryItem(name: "workspace", value: workspace)]
        }
        return parts.url
    }

    /// The app (`collect`, `live` or `review`) and link a URL names, if it is one of ours.
    static func parse(_ url: URL) -> (app: String, link: ChewSenseLink)? {
        guard let scheme = url.scheme?.lowercased(),
              let app = schemes.first(where: { $0.value == scheme })?.key,
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        let value = parts.path.split(separator: "/").first.map(String.init)
        switch parts.host {
        case "status":
            return (app, .status)
        case "experiment":
            guard let value, !value.isEmpty, app == "collect" else { return nil }
            return (app, .experiment(value))
        case "session":
            let workspace = parts.queryItems?.first { $0.name == "workspace" }?.value ?? "assisted"
            guard let value, !value.isEmpty, app == "review", ["assisted", "blind"].contains(workspace) else { return nil }
            return (app, .session(value, workspace: workspace))
        default:
            return nil
        }
    }

    /// Where a shared next step can be opened. Blind reviews open the blind workspace.
    static func forStep(_ step: ProjectStatus.Step) -> (app: String, link: ChewSenseLink)? {
        switch step.app {
        case "collect":
            return ("collect", step.experimentId.map { .experiment($0) } ?? .status)
        case "live":
            return ("live", .status)
        case "review":
            guard let id = step.sessionId else { return nil }
            return ("review", .session(id, workspace: step.id.hasPrefix("blind-") ? "blind" : "assisted"))
        default:
            return nil
        }
    }

    static func appName(_ app: String) -> String {
        switch app {
        case "collect": "Chew Sense Collect"
        case "live": "Chew Sense Live"
        case "review": "Chew Sense Review"
        default: "the Mac"
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
    /// Returns an action when this app can open a step (in itself or in an installed companion app).
    var opener: ((ProjectStatus.Step) -> (label: String, action: () -> Void)?)? = nil

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
                            if let open = opener?(step) {
                                Button(open.label, action: open.action)
                                    .font(.caption.weight(.semibold))
                                    .buttonStyle(.borderless)
                                    .accessibilityIdentifier("open-step-\(step.id)")
                            } else {
                                Text(ProjectStatus.place(of: step.app, in: app))
                                    .font(.caption2.weight(.bold))
                                    .foregroundStyle(step.app == app ? Color.accentColor : Color.secondary)
                            }
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
