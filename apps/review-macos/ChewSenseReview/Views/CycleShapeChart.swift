import SwiftUI
import Charts

/// Compact graph showing a single chew cycle's mouth-open trace, with
/// closed-start / maximum-open / closed-end rules and a live playhead.
struct CycleShapeChart: View {
    @EnvironmentObject var state: ReviewerState
    @EnvironmentObject var playback: PlaybackController

    /// Padding (seconds) on each side of the cycle window so the rise and
    /// fall outside the official boundaries are visible.
    private let padSeconds: Double = 0.15

    var body: some View {
        if !state.isBlindReview {
            content(for: state.selectedChew)
                .frame(height: 130)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 8))
                .overlay(alignment: .topLeading) { legend.padding(8) }
        }
    }

    @ViewBuilder
    private func content(for chew: ReviewChew?) -> some View {
        let lo = max(0, chew.map { $0.startTS - padSeconds } ?? (playback.currentTime - 3))
        let hi = chew.map { $0.endTS + padSeconds } ?? max(6, playback.currentTime + 3)
        let series = windowedSamples(in: lo...hi)
        let head = state.framePredictions.filter { (lo...hi).contains($0.ts) && $0.headSpeed != nil }
        let headMax = max(head.compactMap(\.headSpeed).max() ?? 1, 1e-6)
        let yMax = max(series.map(\.v).max() ?? 1.0, 1e-6)
        let motion = windowedMotion(in: lo...hi)
        let accelerationMax = max(motion.map(\.accelerationMagnitude).max() ?? 1.0, 1e-6)
        let rotationMax = max(motion.map(\.rotationMagnitude).max() ?? 1.0, 1e-6)

        Chart {
            ForEach(Array(series.enumerated()), id: \.offset) { _, sample in
                LineMark(
                    x: .value("t", sample.t),
                    y: .value("open", sample.v / yMax)
                )
                .foregroundStyle(Color.cyan.opacity(0.95))
                .interpolationMethod(.monotone)
            }
            ForEach(motion) { sample in
                LineMark(
                    x: .value("t", sample.videoTS),
                    y: .value("AirPods acceleration", sample.accelerationMagnitude / accelerationMax),
                    series: .value("Acceleration segment", "a\(sample.continuitySegment)")
                )
                .foregroundStyle(Color.orange.opacity(0.85))
                .lineStyle(StrokeStyle(lineWidth: 1))
                LineMark(
                    x: .value("t", sample.videoTS),
                    y: .value("AirPods rotation", sample.rotationMagnitude / rotationMax),
                    series: .value("Rotation segment", "g\(sample.continuitySegment)")
                )
                .foregroundStyle(Color.pink.opacity(0.75))
                .lineStyle(StrokeStyle(lineWidth: 1))
            }
            ForEach(Array(head.enumerated()), id: \.offset) { _, sample in
                LineMark(x: .value("t", sample.ts), y: .value("Visual head speed", (sample.headSpeed ?? 0) / headMax),
                         series: .value("Head", "visual head speed"))
                    .foregroundStyle(Color.purple)
            }
            if let chew {
            RuleMark(x: .value("start", chew.startTS))
                .foregroundStyle(Color.green)
                .lineStyle(StrokeStyle(lineWidth: 1.5))
                .annotation(position: .top, alignment: .leading) {
                    Text("C₀").font(.caption2.bold()).foregroundStyle(.green)
                }
            RuleMark(x: .value("peak", chew.peakTS))
                .foregroundStyle(Color.yellow)
                .lineStyle(StrokeStyle(lineWidth: 1.5))
                .annotation(position: .top, alignment: .center) {
                    Text("O").font(.caption2.bold()).foregroundStyle(.yellow)
                }
            RuleMark(x: .value("end", chew.endTS))
                .foregroundStyle(Color.red)
                .lineStyle(StrokeStyle(lineWidth: 1.5))
                .annotation(position: .top, alignment: .trailing) {
                    Text("C₁").font(.caption2.bold()).foregroundStyle(.red)
                }
            }
            RuleMark(x: .value("playhead", playback.currentTime))
                .foregroundStyle(Color.white.opacity(0.85))
                .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 2]))
        }
        .chartXScale(domain: lo...hi)
        .chartYScale(domain: 0...1.05)
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { value in
                AxisGridLine().foregroundStyle(Color.white.opacity(0.08))
                AxisValueLabel {
                    if let t = value.as(Double.self) {
                        Text(String(format: "%.2f s", t))
                            .font(.caption2)
                            .foregroundStyle(.white.opacity(0.7))
                    }
                }
            }
        }
        .chartYAxis(.hidden)
    }

    private var legend: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 10) {
                if !state.framePredictions.isEmpty { legendItem(color: .cyan, text: "visual jaw-open") }
                if state.framePredictions.contains(where: { $0.headSpeed != nil }) { legendItem(color: .purple, text: "visual head speed") }
                if !state.motionEvidence.isEmpty {
                    legendItem(color: .orange, text: "AirPods accel")
                    legendItem(color: .pink, text: "AirPods rotation")
                }
            }
            if let message = state.motionEvidenceMessage {
                Text(message).font(.caption2).foregroundStyle(.orange)
            }
        }
    }

    private func legendItem(color: Color, text: String) -> some View {
        HStack(spacing: 3) {
            Capsule().fill(color).frame(width: 12, height: 2)
            Text(text)
        }
        .font(.caption2)
        .foregroundStyle(.white.opacity(0.7))
    }

    private func windowedSamples(in range: ClosedRange<Double>) -> [(t: Double, v: Double)] {
        let preds = state.framePredictions
        guard !preds.isEmpty else { return [] }
        // Linear scan; cycle windows are short (~1 s) so this is cheap.
        var out: [(Double, Double)] = []
        out.reserveCapacity(64)
        for p in preds {
            if p.ts < range.lowerBound { continue }
            if p.ts > range.upperBound { break }
            out.append((p.ts, p.mouthOpen))
        }
        return out
    }

    private func windowedMotion(in range: ClosedRange<Double>) -> [MotionEvidenceSample] {
        state.motionEvidence.filter { range.contains($0.videoTS) }
    }

}
