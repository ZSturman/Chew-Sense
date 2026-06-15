import SwiftUI

/// Reusable: animated 0–1 probability bar with high/low threshold tick-marks.
struct ProbabilityBar: View {
    let probability: Double
    let smoothed: Double
    let high: Double
    let low: Double

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.06))

                // Smoothed (filled, behind)
                RoundedRectangle(cornerRadius: 8)
                    .fill(LinearGradient(colors: [Theme.accent.opacity(0.55), Theme.accent],
                                         startPoint: .leading, endPoint: .trailing))
                    .frame(width: geo.size.width * CGFloat(max(0, min(1, smoothed))))
                    .animation(.easeOut(duration: 0.15), value: smoothed)

                // Raw probability (thin top stripe)
                Rectangle()
                    .fill(Color.primary.opacity(0.7))
                    .frame(width: 2, height: geo.size.height)
                    .offset(x: geo.size.width * CGFloat(max(0, min(1, probability))) - 1)

                // Threshold ticks
                threshold(geo: geo, x: low, color: Theme.warn)
                threshold(geo: geo, x: high, color: Theme.danger)
            }
        }
        .frame(height: 26)
    }

    private func threshold(geo: GeometryProxy, x: Double, color: Color) -> some View {
        Rectangle()
            .fill(color)
            .frame(width: 2, height: geo.size.height + 8)
            .offset(x: geo.size.width * CGFloat(x) - 1, y: -4)
    }
}

/// Reusable: state pill ("IDLE" / "CHEWING").
struct StatePill: View {
    let state: ChewState

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(state == .chewing ? Theme.accent : Color.secondary)
                .frame(width: 10, height: 10)
                .overlay(
                    Circle().stroke(Theme.accent, lineWidth: state == .chewing ? 2 : 0)
                        .scaleEffect(state == .chewing ? 1.6 : 1)
                        .opacity(state == .chewing ? 0 : 1)
                        .animation(.easeOut(duration: 1).repeatForever(autoreverses: false),
                                   value: state)
                )
            Text(state == .chewing ? "CHEWING" : "IDLE")
                .font(.system(.callout, design: .rounded).weight(.semibold))
                .foregroundStyle(state == .chewing ? Theme.accent : .secondary)
        }
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(
            Capsule().fill(state == .chewing ? Theme.accentSoft : Color.primary.opacity(0.05))
        )
    }
}

/// Reusable: simple line chart of the recent waveform (accel magnitude).
struct WaveformView: View {
    let samples: [Double]
    var color: Color = Theme.accent

    var body: some View {
        GeometryReader { geo in
            Path { path in
                guard samples.count > 1 else { return }
                let maxV = max(samples.max() ?? 1, 0.001)
                let dx = geo.size.width / CGFloat(samples.count - 1)
                for (i, v) in samples.enumerated() {
                    let x = CGFloat(i) * dx
                    let y = geo.size.height * (1 - CGFloat(v / maxV) * 0.95)
                    if i == 0 { path.move(to: CGPoint(x: x, y: y)) }
                    else      { path.addLine(to: CGPoint(x: x, y: y)) }
                }
            }
            .stroke(color, style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
        }
        .frame(height: 80)
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }
}

/// Reusable: a stat tile (big number + label).
struct StatTile: View {
    let label: String
    let value: String
    var systemImage: String? = nil
    var accent: Color = Theme.accent

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let systemImage {
                Image(systemName: systemImage)
                    .foregroundStyle(accent)
                    .font(.title3)
            }
            Text(value)
                .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                .foregroundStyle(.primary)
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Theme.pad)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Color.primary.opacity(0.06))
        )
    }
}
