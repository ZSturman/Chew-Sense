import SwiftUI
import Charts

struct MealReportView: View {
    @ObservedObject var session: SessionStore

    var body: some View {
        ScrollView {
            if let s = session.latestSummary {
                report(for: s)
            } else {
                emptyState
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 16) {
            Image(systemName: "chart.bar.doc.horizontal")
                .font(.system(size: 64, weight: .light))
                .foregroundStyle(.secondary)
            Text("No session yet")
                .font(.title2)
            Text("Run the Replay or Live demo, then tap **Stop & Review** to generate a meal report.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .frame(maxWidth: 380)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(60)
    }

    private func report(for s: MealSummary) -> some View {
        VStack(alignment: .leading, spacing: Theme.padLg) {
            header(s)
            heroStats(s)
            qualitySection(s)
            if !s.bites.isEmpty {
                chewsPerBiteChart(s)
                paceChart(s)
            }
            caveatFooter
        }
        .padding(Theme.padLg)
    }

    private func header(_ s: MealSummary) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Meal Report").font(.system(.title, design: .rounded).weight(.semibold))
            Text(s.label + " · " + String(format: "%.1f s recorded", s.totalSeconds))
                .foregroundStyle(.secondary)
        }
    }

    private func heroStats(_ s: MealSummary) -> some View {
        let cols = [GridItem(.flexible()), GridItem(.flexible()),
                    GridItem(.flexible()), GridItem(.flexible())]
        return LazyVGrid(columns: cols, spacing: 12) {
            StatTile(label: "Bites", value: "\(s.biteCount)", systemImage: "circle.grid.2x2")
            StatTile(label: "Total chews", value: "\(s.totalChews)", systemImage: "wave.3.right",
                     accent: .blue)
            StatTile(label: "Avg chews / bite", value: String(format: "%.1f", s.avgChewsPerBite),
                     systemImage: "chart.bar", accent: .purple)
            StatTile(label: "Avg chew rate", value: String(format: "%.2f Hz", s.avgChewRateHz),
                     systemImage: "metronome", accent: .orange)
        }
    }

    private func qualitySection(_ s: MealSummary) -> some View {
        Theme.card {
            HStack(spacing: 16) {
                ZStack {
                    Circle().fill(gradeColor(s.qualityGrade).opacity(0.15))
                        .frame(width: 90, height: 90)
                    Text(gradeInitial(s.qualityGrade))
                        .font(.system(.largeTitle, design: .rounded).weight(.bold))
                        .foregroundStyle(gradeColor(s.qualityGrade))
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(s.qualityGrade.rawValue)
                        .font(.system(.title2, design: .rounded).weight(.semibold))
                        .foregroundStyle(gradeColor(s.qualityGrade))
                    Text(gradeBlurb(s))
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
            }
        }
    }

    private func chewsPerBiteChart(_ s: MealSummary) -> some View {
        Theme.card {
            VStack(alignment: .leading, spacing: 12) {
                Text("Chews per bite")
                    .font(.system(.title3, design: .rounded).weight(.semibold))
                Chart(Array(s.bites.enumerated()), id: \.offset) { idx, bite in
                    BarMark(
                        x: .value("Bite", idx + 1),
                        y: .value("Chews", bite.chewCount)
                    )
                    .foregroundStyle(bite.chewCount < RuntimeConfig.rushedChewsPerBite ? Theme.warn : Theme.accent)
                    .cornerRadius(4)
                }
                .frame(height: 200)
                Text("Bars below \(RuntimeConfig.rushedChewsPerBite) chews are flagged as rushed.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func paceChart(_ s: MealSummary) -> some View {
        Theme.card {
            VStack(alignment: .leading, spacing: 12) {
                Text("Pace timeline")
                    .font(.system(.title3, design: .rounded).weight(.semibold))
                Chart(Array(s.bites.enumerated()), id: \.offset) { idx, bite in
                    BarMark(
                        xStart: .value("Start", bite.startSeconds),
                        xEnd:   .value("End",   bite.endSeconds),
                        y:      .value("Bite",  idx + 1)
                    )
                    .foregroundStyle(Theme.accent.opacity(0.7))
                    .cornerRadius(3)
                }
                .chartYAxis(.hidden)
                .frame(height: max(80, CGFloat(s.bites.count) * 14 + 20))
            }
        }
    }

    private var caveatFooter: some View {
        Theme.card {
            VStack(alignment: .leading, spacing: 6) {
                Label("About these numbers", systemImage: "info.circle")
                    .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Text("""
                The chewing detector is the deployed binary ChewNet (motion-only). \
                Per-bite chew counts use a heuristic peak detector on acceleration magnitude — \
                a neural chew-phase head is in training (see ROADMAP Phase 3) and will replace \
                the heuristic once the labeled video corpus is large enough.
                """)
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func gradeColor(_ g: QualityGrade) -> Color {
        switch g {
        case .excellent:        return Theme.accent
        case .good:             return .blue
        case .fair:             return Theme.warn
        case .needsWork:        return Theme.danger
        case .insufficientData: return .secondary
        }
    }

    private func gradeInitial(_ g: QualityGrade) -> String {
        switch g {
        case .excellent: return "A"
        case .good:      return "B"
        case .fair:      return "C"
        case .needsWork: return "D"
        case .insufficientData: return "—"
        }
    }

    private func gradeBlurb(_ s: MealSummary) -> String {
        switch s.qualityGrade {
        case .excellent: return "Steady pace and thorough chewing across this session."
        case .good:      return "Mostly thorough chewing — a few rushed bites."
        case .fair:      return "Room to slow down — multiple bites under the chew-count target."
        case .needsWork: return "Many rushed bites or low chew counts. Try smaller bites."
        case .insufficientData:
            return "Not enough chewing was detected to grade this session."
        }
    }
}
