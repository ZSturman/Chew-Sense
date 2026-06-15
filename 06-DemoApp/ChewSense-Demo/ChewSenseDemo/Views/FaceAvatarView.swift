//
//  FaceAvatarView.swift
//  ChewSenseDemo
//
//  Custom-drawn cartoon face whose mouth opens and closes from a 0…1
//  driver value. Drawn entirely with SwiftUI shapes so it renders
//  identically on all iOS versions and produces a consistent
//  "Genmoji-style" mascot. Eyes blink occasionally for charm; mouth color
//  shifts subtly when the classifier is active.
//

import SwiftUI

struct FaceAvatarView: View {

    /// Mouth aperture in [0, 1]. Drives both the lips path and a soft glow.
    let mouthOpenness: Double

    /// True when the classifier reports an active eating episode. Tints
    /// the cheeks and pulses a subtle outer ring.
    let isEating: Bool

    @State private var blink: Bool = false

    var body: some View {
        GeometryReader { geo in
            let size = min(geo.size.width, geo.size.height)
            let r = size * 0.42
            let center = CGPoint(x: geo.size.width / 2, y: geo.size.height / 2)

            ZStack {
                // Outer pulse when eating.
                if isEating {
                    Circle()
                        .stroke(
                            LinearGradient(colors: [Color.green.opacity(0.55),
                                                    Color.green.opacity(0.0)],
                                           startPoint: .top, endPoint: .bottom),
                            lineWidth: 6
                        )
                        .frame(width: r * 2.2, height: r * 2.2)
                        .scaleEffect(isEating ? 1.05 : 1.0)
                        .opacity(0.9)
                        .animation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true),
                                   value: isEating)
                }

                // Head
                Circle()
                    .fill(
                        RadialGradient(colors: [Color(red: 1.0, green: 0.93, blue: 0.62),
                                                Color(red: 0.99, green: 0.83, blue: 0.40)],
                                       center: .topLeading, startRadius: 4, endRadius: r * 1.2)
                    )
                    .frame(width: r * 2, height: r * 2)
                    .shadow(color: .black.opacity(0.12), radius: 8, x: 0, y: 6)

                // Cheeks (more saturated when eating)
                Group {
                    cheek(at: CGPoint(x: center.x - r * 0.55, y: center.y + r * 0.12),
                          radius: r * 0.18)
                    cheek(at: CGPoint(x: center.x + r * 0.55, y: center.y + r * 0.12),
                          radius: r * 0.18)
                }
                .opacity(isEating ? 0.85 : 0.45)
                .animation(.easeInOut(duration: 0.4), value: isEating)

                // Eyes (blink occasionally).
                eye(at: CGPoint(x: center.x - r * 0.40, y: center.y - r * 0.30), radius: r * 0.10)
                eye(at: CGPoint(x: center.x + r * 0.40, y: center.y - r * 0.30), radius: r * 0.10)

                // Mouth.
                MouthShape(openness: mouthOpenness)
                    .fill(
                        LinearGradient(colors: [Color(red: 0.42, green: 0.07, blue: 0.10),
                                                Color(red: 0.78, green: 0.18, blue: 0.20)],
                                       startPoint: .top, endPoint: .bottom)
                    )
                    .overlay(
                        MouthShape(openness: mouthOpenness)
                            .stroke(Color.black.opacity(0.35), lineWidth: 1.2)
                    )
                    .frame(width: r * 0.95, height: r * 0.55)
                    .position(x: center.x, y: center.y + r * 0.42)
                    .animation(.easeOut(duration: 0.06), value: mouthOpenness)
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .onAppear { startBlinkLoop() }
    }

    @ViewBuilder
    private func eye(at p: CGPoint, radius: CGFloat) -> some View {
        Capsule()
            .fill(Color.black.opacity(0.85))
            .frame(width: radius * 1.6, height: blink ? 2 : radius * 1.6)
            .position(p)
            .animation(.easeInOut(duration: 0.12), value: blink)
    }

    @ViewBuilder
    private func cheek(at p: CGPoint, radius: CGFloat) -> some View {
        Circle()
            .fill(
                RadialGradient(colors: [Color.pink.opacity(0.55),
                                        Color.pink.opacity(0.0)],
                               center: .center, startRadius: 1, endRadius: radius)
            )
            .frame(width: radius * 2, height: radius * 2)
            .position(p)
    }

    private func startBlinkLoop() {
        Task { @MainActor in
            while true {
                let wait = UInt64.random(in: 2_500_000_000...5_500_000_000)
                try? await Task.sleep(nanoseconds: wait)
                blink = true
                try? await Task.sleep(nanoseconds: 130_000_000)
                blink = false
            }
        }
    }
}

/// Mouth as a smooth lozenge whose vertical opening tracks `openness`.
/// At 0 → a subtle smile curve. At 1 → a wide oval.
private struct MouthShape: Shape {
    var openness: Double
    var animatableData: Double {
        get { openness }
        set { openness = newValue }
    }

    func path(in rect: CGRect) -> Path {
        var p = Path()
        let w = rect.width
        let h = rect.height
        let opening = max(0.04, min(1.0, openness))

        let topY = rect.midY - (h / 2) * opening
        let botY = rect.midY + (h / 2) * opening * 0.85   // slight asymmetry — bottom lip wider
        // Smile curve at low openness adds a subtle corner-up bias.
        let smileLift = (1.0 - opening) * h * 0.18
        let leftX = rect.minX + w * 0.05
        let rightX = rect.maxX - w * 0.05

        p.move(to: CGPoint(x: leftX, y: rect.midY + smileLift))
        p.addQuadCurve(to: CGPoint(x: rightX, y: rect.midY + smileLift),
                       control: CGPoint(x: rect.midX, y: topY))
        p.addQuadCurve(to: CGPoint(x: leftX, y: rect.midY + smileLift),
                       control: CGPoint(x: rect.midX, y: botY))
        p.closeSubpath()
        return p
    }
}

#Preview {
    VStack(spacing: 24) {
        FaceAvatarView(mouthOpenness: 0.05, isEating: false)
            .frame(width: 220, height: 220)
        FaceAvatarView(mouthOpenness: 0.55, isEating: true)
            .frame(width: 220, height: 220)
    }
    .padding()
}
