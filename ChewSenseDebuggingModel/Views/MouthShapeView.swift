//
//  MouthShapeView.swift
//  ChewSenseDebuggingModel
//
//  Real-time 2D mouth shape visualization driven by model output.
//  Renders an abstract mouth shape based on 4 estimated parameters.
//

import SwiftUI

/// Displays a stylized 2D mouth shape driven by MouthShapeState.
struct MouthShapeView: View {
    let state: MouthShapeState
    
    /// Size of the drawing canvas
    var canvasSize: CGFloat = 200
    
    var body: some View {
        VStack(spacing: 12) {
            // Mouth drawing
            Canvas { context, size in
                let cx = size.width / 2
                let cy = size.height / 2
                
                // Map parameters to visual dimensions
                let baseWidth: CGFloat = size.width * 0.25
                let maxExtraWidth: CGFloat = size.width * 0.25
                let mouthHalfW = baseWidth + maxExtraWidth * CGFloat(state.mouthWidth)
                
                let maxOpen: CGFloat = size.height * 0.35
                let openAmount = maxOpen * CGFloat(state.mouthOpenness)
                
                let jawShift = CGFloat(state.jawDisplacement) * size.height * 0.08
                let lipPress = CGFloat(state.lipCompression) * size.height * 0.06
                
                // Upper lip curve
                let upperPath = Path { p in
                    p.move(to: CGPoint(x: cx - mouthHalfW, y: cy))
                    p.addQuadCurve(
                        to: CGPoint(x: cx + mouthHalfW, y: cy),
                        control: CGPoint(x: cx, y: cy - openAmount * 0.4 - lipPress)
                    )
                }
                
                // Lower lip curve (shifted down by jaw displacement)
                let lowerPath = Path { p in
                    p.move(to: CGPoint(x: cx + mouthHalfW, y: cy))
                    p.addQuadCurve(
                        to: CGPoint(x: cx - mouthHalfW, y: cy),
                        control: CGPoint(x: cx, y: cy + openAmount + jawShift + lipPress)
                    )
                }
                
                // Combined closed shape for fill
                var fillPath = Path()
                fillPath.move(to: CGPoint(x: cx - mouthHalfW, y: cy))
                fillPath.addQuadCurve(
                    to: CGPoint(x: cx + mouthHalfW, y: cy),
                    control: CGPoint(x: cx, y: cy - openAmount * 0.4 - lipPress)
                )
                fillPath.addQuadCurve(
                    to: CGPoint(x: cx - mouthHalfW, y: cy),
                    control: CGPoint(x: cx, y: cy + openAmount + jawShift + lipPress)
                )
                fillPath.closeSubpath()
                
                // Draw
                context.fill(fillPath, with: .color(.pink.opacity(0.15)))
                context.stroke(upperPath, with: .color(.red.opacity(0.8)), lineWidth: 3)
                context.stroke(lowerPath, with: .color(.red.opacity(0.8)), lineWidth: 3)
            }
            .frame(width: canvasSize, height: canvasSize)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color.gray.opacity(0.05))
            )
            
            // Parameter readouts
            VStack(alignment: .leading, spacing: 4) {
                ParameterRow(label: "Openness", value: state.mouthOpenness)
                ParameterRow(label: "Width", value: state.mouthWidth)
                ParameterRow(label: "Jaw", value: state.jawDisplacement)
                ParameterRow(label: "Lip Press", value: state.lipCompression)
            }
            .font(.system(.caption, design: .monospaced))
        }
    }
}

// MARK: - Parameter Row

private struct ParameterRow: View {
    let label: String
    let value: Double
    
    var body: some View {
        HStack(spacing: 8) {
            Text(label)
                .frame(width: 70, alignment: .trailing)
                .foregroundColor(.secondary)
            
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Color.gray.opacity(0.15))
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Color.blue.opacity(0.5))
                        .frame(width: max(0, geo.size.width * CGFloat(min(value, 1.0))))
                }
            }
            .frame(height: 10)
            
            Text(String(format: "%.2f", value))
                .frame(width: 40, alignment: .trailing)
        }
    }
}

#Preview {
    MouthShapeView(state: MouthShapeState(
        mouthOpenness: 0.4,
        mouthWidth: 0.6,
        jawDisplacement: 0.2,
        lipCompression: 0.1
    ))
    .padding()
}
