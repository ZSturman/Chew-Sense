//
//  MetricsView.swift
//  ChewSenseLive
//
//  Live readout of motion-only meal metrics. Bind directly to the
//  `MealMetricsTracker` exposed by `MotionManager.mealMetrics` to see
//  updates as bites are committed during a recording session.
//
//  Designed as a standalone screen so it can be reached from a
//  toolbar item, a TabView, or embedded inside the recording composer
//  without further refactoring.

import SwiftUI

struct MetricsView: View {
    @ObservedObject var metrics: MealMetricsTracker

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                summaryGrid
                Divider()
                bitesList
            }
            .padding()
        }
        .navigationTitle("Live Metrics")
    }

    private var summaryGrid: some View {
        let columns = [GridItem(.flexible()), GridItem(.flexible())]
        return LazyVGrid(columns: columns, spacing: 12) {
            metricCard(label: "Bites", value: "\(metrics.biteCount)")
            metricCard(label: "Chews", value: "\(metrics.chewCount)")
            metricCard(
                label: "Chews / bite",
                value: String(format: "%.1f", metrics.avgChewsPerBite)
            )
            metricCard(
                label: "Bite rate (per min)",
                value: String(format: "%.2f", metrics.biteRatePerMinute)
            )
            metricCard(
                label: "Last IBI (s)",
                value: String(format: "%.1f", metrics.lastInterBiteIntervalSec)
            )
            metricCard(
                label: "Rushed bites",
                value: "\(metrics.rushedBiteCount)",
                tint: metrics.rushedBiteCount > 0 ? .orange : .secondary
            )
        }
    }

    private func metricCard(label: String, value: String, tint: Color = .primary) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.system(.title2, design: .rounded).weight(.semibold))
                .foregroundStyle(tint)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.gray.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder
    private var bitesList: some View {
        if metrics.bites.isEmpty {
            Text("No bites yet — start chewing to populate metrics.")
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.top, 24)
        } else {
            VStack(alignment: .leading, spacing: 6) {
                Text("Bites")
                    .font(.headline)
                ForEach(Array(metrics.bites.enumerated()), id: \.element.id) { idx, bite in
                    biteRow(index: idx + 1, bite: bite)
                }
            }
        }
    }

    private func biteRow(index: Int, bite: BiteEvent) -> some View {
        let rushed = bite.chewCount < RuntimeConfig.rushedChewsPerBite
        return HStack(spacing: 8) {
            Text("#\(index)")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
                .frame(width: 32, alignment: .leading)
            Text(String(format: "%.1f–%.1f s", bite.startTime, bite.endTime))
                .font(.caption.monospacedDigit())
            Spacer()
            Label(
                "\(bite.chewCount) chews",
                systemImage: rushed ? "exclamationmark.triangle.fill" : "circle.fill"
            )
            .labelStyle(.titleAndIcon)
            .font(.caption)
            .foregroundStyle(rushed ? .orange : .green)
        }
        .padding(.vertical, 4)
        .padding(.horizontal, 8)
        .background(.gray.opacity(0.05), in: RoundedRectangle(cornerRadius: 6))
    }
}
