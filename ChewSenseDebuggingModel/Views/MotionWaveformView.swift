//
//  MotionWaveformView.swift
//  ChewSenseDebuggingModel
//
//  Scrolling line chart of AirPod accelerometer axes (ax, ay, az) drawn
//  using Swift Charts. Intended for the live sensor debug panel shown
//  during recording.
//

import SwiftUI
import Charts

/// Three-axis accelerometer waveform that plots the last ~2 s of
/// `MotionSample` data streamed from the AirPods.
struct MotionWaveformView: View {

    let samples: [MotionSample]

    // Fixed Y domain for user-acceleration (m/s²). Values beyond ±2 are clipped
    // visually but don't crash — chewing motion stays well within ±1.5.
    private let yMin: Double = -2.0
    private let yMax: Double =  2.0

    var body: some View {
        Chart {
            ForEach(Array(samples.enumerated()), id: \.offset) { idx, s in
                let t = relativeTime(s.timestamp)
                LineMark(
                    x: .value("t", t),
                    y: .value("X", s.ax),
                    series: .value("Axis", "X")
                )
                .foregroundStyle(Color.red)
                .lineStyle(StrokeStyle(lineWidth: 1.5))

                LineMark(
                    x: .value("t", t),
                    y: .value("Y", s.ay),
                    series: .value("Axis", "Y")
                )
                .foregroundStyle(Color.green)
                .lineStyle(StrokeStyle(lineWidth: 1.5))

                LineMark(
                    x: .value("t", t),
                    y: .value("Z", s.az),
                    series: .value("Axis", "Z")
                )
                .foregroundStyle(Color.cyan)
                .lineStyle(StrokeStyle(lineWidth: 1.5))
            }

            // Zero-line for reference
            RuleMark(y: .value("Zero", 0))
                .foregroundStyle(Color.white.opacity(0.25))
                .lineStyle(StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
        }
        .chartXAxis(.hidden)
        .chartYAxis {
            AxisMarks(values: [-2, -1, 0, 1, 2]) { value in
                AxisGridLine()
                    .foregroundStyle(Color.white.opacity(0.1))
                AxisValueLabel {
                    if let v = value.as(Double.self) {
                        Text(String(format: "%+.0f", v))
                            .font(.system(size: 8, design: .monospaced))
                            .foregroundColor(.white.opacity(0.5))
                    }
                }
            }
        }
        .chartYScale(domain: yMin...yMax)
        .chartXScale(domain: xDomain)
        .chartLegend(.hidden)
        .overlay(alignment: .topLeading) { legend }
        .background(Color.black.opacity(0.01)) // ensures tap area
    }

    // MARK: - Helpers

    private var latestTimestamp: Double {
        samples.last?.timestamp ?? 0
    }

    private func relativeTime(_ t: TimeInterval) -> Double {
        t - latestTimestamp
    }

    /// X axis always spans the newest 2 seconds, anchored at 0 (= "now").
    private var xDomain: ClosedRange<Double> {
        -2.0...0.0
    }

    private var legend: some View {
        HStack(spacing: 6) {
            axisLabel("X", color: .red)
            axisLabel("Y", color: .green)
            axisLabel("Z", color: .cyan)
        }
        .padding(5)
        .background(Color.black.opacity(0.45))
        .clipShape(RoundedRectangle(cornerRadius: 5))
        .padding(6)
    }

    private func axisLabel(_ text: String, color: Color) -> some View {
        HStack(spacing: 3) {
            Circle()
                .fill(color)
                .frame(width: 6, height: 6)
            Text(text)
                .font(.system(size: 9, weight: .semibold, design: .monospaced))
                .foregroundColor(.white)
        }
    }
}
