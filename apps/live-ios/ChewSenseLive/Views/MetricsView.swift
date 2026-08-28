//
//  MetricsView.swift
//  ChewSenseLive
//
//  Cadence-only live/post-meal summary. Bite and chews-per-bite UI stays
//  absent until the independently evaluated food-entry capability is promoted.
//

import SwiftUI

struct MetricsView: View {
    @ObservedObject var metrics: MealMetricsTracker

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                if !ModelCapabilities.bundled.temporalChewEventHead {
                    Label(
                        "The bundled model is legacy eating detection. Individual-chew coaching is disabled.",
                        systemImage: "exclamationmark.shield"
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                if metrics.latestCue == .slowDown {
                    Label("Try a slightly slower, steadier pace", systemImage: "tortoise")
                        .font(.headline)
                        .foregroundStyle(.orange)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
                }

                summaryGrid

                Text(
                    ModelCapabilities.bundled.foodEntryCoachingEnabled
                        ? "Food-entry grouping is available for this promoted model."
                        : "Bite count and chews-per-bite are hidden until a food-entry model passes its independent evaluation."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding()
        }
        .navigationTitle("Meal Cadence")
    }

    private var summaryGrid: some View {
        let columns = [GridItem(.flexible()), GridItem(.flexible())]
        return LazyVGrid(columns: columns, spacing: 12) {
            metricCard(label: "Detected chews", value: "\(metrics.chewCount)")
            metricCard(label: "Texture", value: metrics.texture.title)
            metricCard(
                label: "Median interval",
                value: metrics.medianInterChewIntervalSec.map { String(format: "%.2f s", $0) } ?? "—"
            )
            metricCard(
                label: "Cadence",
                value: metrics.cadencePerMinute.map { String(format: "%.0f / min", $0) } ?? "—"
            )
            metricCard(
                label: "Confidence coverage",
                value: String(format: "%.0f%%", 100 * metrics.confidenceCoverage)
            )
            metricCard(
                label: "Excluded uncertain",
                value: String(format: "%.1f s", metrics.uncertainTimeSec)
            )
        }
    }

    private func metricCard(label: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.system(.title2, design: .rounded).weight(.semibold))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.gray.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }
}
