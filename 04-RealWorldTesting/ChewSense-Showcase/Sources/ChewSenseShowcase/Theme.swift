import SwiftUI

enum Theme {
    static let accent = Color(red: 0.18, green: 0.66, blue: 0.55)        // chewing green
    static let accentSoft = Color(red: 0.18, green: 0.66, blue: 0.55).opacity(0.18)
    static let warn = Color(red: 0.95, green: 0.66, blue: 0.20)
    static let danger = Color(red: 0.86, green: 0.30, blue: 0.30)
    static let surface = Color(nsColor: .windowBackgroundColor)
    static let card = Color(nsColor: .controlBackgroundColor)
    static let muted = Color.secondary

    static let cornerRadius: CGFloat = 14
    static let pad: CGFloat = 16
    static let padLg: CGFloat = 24

    static func card<V: View>(@ViewBuilder _ content: () -> V) -> some View {
        content()
            .padding(pad)
            .background(card, in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(Color.primary.opacity(0.06), lineWidth: 1)
            )
    }
}
