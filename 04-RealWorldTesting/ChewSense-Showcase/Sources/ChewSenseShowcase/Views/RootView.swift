import SwiftUI

enum ShowcaseTab: String, CaseIterable, Identifiable {
    case tour = "Pipeline Tour"
    case replay = "Replay Demo"
    case live = "Live Demo"
    case report = "Meal Report"
    var id: String { rawValue }

    var systemImage: String {
        switch self {
        case .tour:   return "map"
        case .replay: return "play.rectangle"
        case .live:   return "waveform.badge.mic"
        case .report: return "chart.bar.doc.horizontal"
        }
    }
}

struct RootView: View {
    @State private var selection: ShowcaseTab? = .tour
    @StateObject private var session = SessionStore()

    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("ChewSense")
                        .font(.system(.title2, design: .rounded).weight(.bold))
                    Text("Showcase")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 10)

                ForEach(ShowcaseTab.allCases) { tab in
                    Button {
                        selection = tab
                    } label: {
                        Label(tab.rawValue, systemImage: tab.systemImage)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                            .padding(.horizontal, 10)
                            .padding(.vertical, 9)
                            .foregroundStyle(selection == tab ? Theme.accent : Color.primary)
                            .background(
                                RoundedRectangle(cornerRadius: 8)
                                    .fill(selection == tab ? Theme.accentSoft : Color.clear)
                            )
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("sidebar.\(tab.id)")
                }

                Spacer()

                Text(NormalizationConstants.MODEL_ID)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.tertiary)
                    .lineLimit(2)
                    .padding(.horizontal, 8)
            }
            .padding(14)
            .frame(width: 220)
            .frame(maxHeight: .infinity)
            .background(Color(nsColor: .controlBackgroundColor))

            Divider()

            detail
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.surface)
        }
    }

    @ViewBuilder
    private var detail: some View {
        switch selection {
        case .some(.tour):
            PipelineTourView(onTryIt: { selection = .replay })
        case .some(.replay):
            ReplayDemoView(session: session, onShowReport: { selection = .report })
        case .some(.live):
            LiveDemoView(session: session, onShowReport: { selection = .report })
        case .some(.report):
            MealReportView(session: session)
        case .none:
            ContentUnavailableView(
                "Choose a demo",
                systemImage: "sidebar.left",
                description: Text("Select a destination in the sidebar.")
            )
        }
    }
}

/// Holds the most-recent meal summary so any tab can open it in the report view.
final class SessionStore: ObservableObject {
    @Published var latestSummary: MealSummary?
}
