import SwiftUI
import UIKit

/// "Collect Required Data": the experiments the Mac currently needs.
struct CollectRequiredDataView: View {
    @ObservedObject var services: CollectServices
    @State private var openedExperiment: String?

    var body: some View {
        List {
            Section {
                NavigationLink {
                    MacConnectionView(services: services)
                } label: {
                    MacStatusRow(connection: services.connection)
                }
                .accessibilityIdentifier("macConnectionRow")
            }
            Section {
                if let pipeline = services.connection.pipeline {
                    VStack(alignment: .leading, spacing: 8) {
                        if let project = services.connection.project {
                            ProjectStatusCard(status: project, app: "collect", stale: isOffline, opener: opener)
                        } else {
                            Text(pipeline.nextAction).font(.callout.weight(.semibold))
                        }
                        if let id = pipeline.nextExperimentId,
                           let entry = services.catalog.catalog.entries.first(where: { $0.definition.experimentID == id }) {
                            NavigationLink {
                                ExperimentPreparationView(services: services, entry: entry)
                            } label: {
                                Label("Start \(entry.definition.title)", systemImage: "video.badge.plus")
                            }
                            .buttonStyle(.borderedProminent)
                            .accessibilityIdentifier("startRecommendedExperiment")
                        }
                        DisclosureGroup("Details") {
                            if let received = pipeline.receivedSessions {
                                Text("\(received) received · \(pipeline.completeSessions ?? 0) complete · \(pipeline.incompleteSessions ?? 0) incomplete")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Text("\(pipeline.availableReviewTasks ?? pipeline.reviewTasks) reviews ready on your Mac · \(pipeline.attentionCount) processing issues")
                                .font(.caption).foregroundStyle(.secondary)
                            if case .offline = services.connection.state {
                                Text("Last received status. Reconnect to update.").font(.caption).foregroundStyle(.orange)
                            }
                            Text(pipeline.researchTargetsMet ? "Vision research targets met" : "Vision research targets still need verification")
                                .font(.caption.weight(.semibold))
                            Text(pipeline.motionAssessmentPassed == true ? "AirPods model test gates passed" : "AirPods model still needs training and test verification")
                                .font(.caption.weight(.semibold))
                            if pipeline.trainingReady {
                                Text("Data gates passed. Experimental training is eligible; model verification and promotion remain separate.")
                                    .font(.caption)
                            }
                            ForEach(pipeline.goals ?? []) { goal in
                                HStack(alignment: .firstTextBaseline) {
                                    Text(goal.title)
                                    Spacer()
                                    Text("\(goal.current) / \(goal.target)").monospacedDigit().fixedSize()
                                }
                                .font(.caption)
                            }
                            if services.connection.project == nil {
                                ForEach(Array((pipeline.actions ?? []).dropFirst().prefix(2))) { action in
                                    Label(action.title + " · " + (action.destination == "mac" ? "On your Mac" : "In Collect"),
                                          systemImage: action.destination == "mac" ? "desktopcomputer" : "video")
                                        .font(.caption)
                                }
                            }
                            DisclosureGroup("Pipeline progress") {
                                ForEach(pipeline.stages ?? []) { stage in
                                    VStack(alignment: .leading, spacing: 2) {
                                        Label(stage.title, systemImage: stage.state == "complete" ? "checkmark.circle.fill" : stage.state == "running" ? "gearshape.2" : "circle.dashed")
                                        Text(stage.detail).foregroundStyle(.secondary)
                                    }
                                    .font(.caption).padding(.vertical, 3)
                                }
                            }
                            DisclosureGroup("Why training is waiting") {
                                ForEach(pipeline.blockers, id: \.self) { blocker in
                                    Label(blocker, systemImage: "circle.dashed").font(.caption)
                                }
                            }
                            Text("A completed recording is not a verified label. Review and clock confirmation happen on the Mac.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        .font(.caption)
                    }
                    .accessibilityIdentifier("pipelineNextSteps")
                } else {
                    Text("Connect to your Mac to see review tasks and exactly what is still needed before training.")
                        .font(.callout)
                }
            } header: {
                Text("What happens next")
            }
            Section {
                ForEach(services.catalog.sortedEntries) { entry in
                    NavigationLink {
                        ExperimentPreparationView(services: services, entry: entry)
                    } label: {
                        ExperimentRow(entry: entry,
                                      completed: services.catalog.completed(for: entry),
                                      remaining: services.catalog.remaining(for: entry))
                    }
                    .accessibilityIdentifier("experiment-\(entry.definition.experimentID)")
                }
            } header: {
                Text("Available experiments")
            } footer: {
                Text(catalogFooter)
            }
        }
        .navigationDestination(item: $openedExperiment) { id in
            if let entry = services.catalog.catalog.entries.first(where: { $0.definition.experimentID == id }) {
                ExperimentPreparationView(services: services, entry: entry)
            }
        }
        .navigationTitle("Collect Required Data")
        .refreshable { await services.synchronize() }
        .task { await services.synchronize() }
    }

    /// Collect steps open their experiment here; Live steps open Live when it is installed.
    private func opener(_ step: ProjectStatus.Step) -> (label: String, action: () -> Void)? {
        guard let (app, link) = ChewSenseLink.forStep(step) else { return nil }
        switch (app, link) {
        case ("collect", .experiment(let id)):
            guard services.catalog.catalog.entries.contains(where: { $0.definition.experimentID == id }) else { return nil }
            return ("Prepare in this app", { openedExperiment = id })
        case ("live", _):
            guard let url = ChewSenseLink.url(app: "live", link), UIApplication.shared.canOpenURL(url) else { return nil }
            return ("Open Chew Sense Live", { UIApplication.shared.open(url) })
        default:
            return nil
        }
    }

    private var isOffline: Bool {
        if case .offline = services.connection.state { return true }
        return false
    }

    private var catalogFooter: String {
        switch services.catalog.source {
        case .bundledSeed:
            "Showing the starter experiments built into Collect. Pair with the Mac to receive current requests."
        case .synchronized(let date):
            "Catalog v\(services.catalog.catalog.catalogVersion) from the Mac, updated \(date.formatted(.relative(presentation: .named))). Works offline."
        }
    }
}

struct MacStatusRow: View {
    @ObservedObject var connection: MacConnection

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon).foregroundStyle(color)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var icon: String {
        switch connection.state {
        case .connected: "desktopcomputer"
        case .connecting: "arrow.triangle.2.circlepath"
        case .offline: "wifi.exclamationmark"
        case .unpaired: "link.badge.plus"
        }
    }

    private var color: Color {
        switch connection.state {
        case .connected: .green
        case .connecting: .secondary
        case .offline: .orange
        case .unpaired: .blue
        }
    }

    private var title: String {
        switch connection.state {
        case .connected: "Connected to \(connection.paired?.name ?? "Mac")"
        case .connecting: "Connecting…"
        case .offline: "Mac not reachable right now"
        case .unpaired: "Connect to your Mac"
        }
    }

    private var detail: String {
        switch connection.state {
        case .connected(let pending): pending > 0 ? "\(pending) recording(s) waiting to process" : "Ready to receive recordings"
        case .connecting: "Checking your Mac…"
        case .offline: connection.isSearching ? "Looking for your Mac…"
            : "Recording works. New recordings are sent when the Mac is back."
        case .unpaired: "Pair once to send recordings and receive requests"
        }
    }
}

struct ExperimentRow: View {
    let entry: CatalogEntry
    let completed: Int
    let remaining: Int

    var body: some View {
        let d = entry.definition
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(d.title).font(.headline)
                Spacer()
                PriorityBadge(priority: entry.effectivePriority, required: d.required)
            }
            Text(d.summary).font(.subheadline)
            Text("Why: \(entry.whyNeeded)").font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 12) {
                Label(Self.duration(d), systemImage: "clock")
                Label(d.food.needsFoodOrDrink ? (d.food.recommended ?? "Food") : "No food", systemImage: d.food.needsFoodOrDrink ? "fork.knife" : "nosign")
                Label("\(completed)/\(entry.progress.target) recorded", systemImage: remaining == 0 ? "checkmark.circle.fill" : "circle.dashed")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .labelStyle(.titleAndIcon)
        }
        .padding(.vertical, 4)
        .opacity(remaining == 0 ? 0.6 : 1)
    }

    static func duration(_ d: ExperimentDefinition) -> String {
        let seconds = Int(d.totalSeconds.rounded())
        return seconds < 120 ? "\(seconds) s" : "\(seconds / 60) min"
    }
}

struct PriorityBadge: View {
    let priority: String
    let required: Bool

    var body: some View {
        Text(required ? "Required" : priority.capitalized)
            .font(.caption2.weight(.bold))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.18), in: Capsule())
            .foregroundStyle(color)
    }

    private var color: Color {
        if required { return .red }
        switch priority {
        case "high": return .orange
        case "low": return .secondary
        default: return .blue
        }
    }
}
