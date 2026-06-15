import SwiftUI

struct PipelineTourView: View {
    let onTryIt: () -> Void
    @State private var page: Int = 0

    private let cards: [TourCard] = [
        TourCard(
            stage: "01",
            title: "Collect",
            blurb: "AirPods Pro/Max stream accelerometer + gyroscope at 100 Hz. Our iOS app records labeled motion alongside an optional reference video.",
            statValue: "25+",
            statLabel: "raw sessions",
            symbol: "headphones",
            tint: .blue
        ),
        TourCard(
            stage: "02b",
            title: "Label",
            blurb: "A vision-only mouth-shape model auto-seeds chew cycles from the reference video. Humans verify in a custom macOS reviewer app.",
            statValue: "3-marker",
            statLabel: "open • peak • close",
            symbol: "eye",
            tint: .orange
        ),
        TourCard(
            stage: "02",
            title: "Train",
            blurb: "12 time- and frequency-domain features per 0.75 s window feed a 3-layer MLP. Tracked end-to-end in Weights & Biases.",
            statValue: "12",
            statLabel: "features per window",
            symbol: "brain",
            tint: .purple
        ),
        TourCard(
            stage: "03",
            title: "Evaluate",
            blurb: "Locked validation splits keep regressions visible. Each model gets a registry entry with metrics, dataset hash, and W&B run.",
            statValue: NormalizationConstants.MODEL_ID,
            statLabel: "model id",
            symbol: "checklist",
            tint: .teal
        ),
        TourCard(
            stage: "04",
            title: "Deploy",
            blurb: "CoreML export + auto-generated Swift constants ship the model to iOS / macOS. The hysteresis state machine turns probabilities into bites.",
            statValue: "CoreML",
            statLabel: "on-device, offline",
            symbol: "shippingbox",
            tint: Theme.accent
        )
    ]

    var body: some View {
        VStack(spacing: 0) {
            header
            TourCardView(
                card: cards[page],
                isLast: page == cards.count - 1,
                onTryIt: onTryIt
            )
            .id(page)
            .padding(Theme.padLg)
            .transition(.opacity.combined(with: .move(edge: .trailing)))
            .animation(.easeInOut(duration: 0.2), value: page)
            footer
        }
        .padding(Theme.padLg)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("ChewSense")
                .font(.system(.largeTitle, design: .rounded).weight(.bold))
            Text("Detecting eating behaviour from AirPods motion sensors.")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var footer: some View {
        HStack(spacing: 8) {
            ForEach(0..<cards.count, id: \.self) { i in
                Capsule()
                    .fill(i == page ? Theme.accent : Color.primary.opacity(0.15))
                    .frame(width: i == page ? 22 : 8, height: 8)
                    .animation(.spring(response: 0.3), value: page)
                    .onTapGesture { page = i }
            }
            Spacer()
            Button("Previous") { page = max(0, page - 1) }.disabled(page == 0)
            Button("Next")     { page = min(cards.count - 1, page + 1) }.disabled(page == cards.count - 1)
        }
        .padding(.top, Theme.pad)
    }
}

private struct TourCard: Identifiable {
    let id = UUID()
    let stage: String
    let title: String
    let blurb: String
    let statValue: String
    let statLabel: String
    let symbol: String
    let tint: Color
}

private struct TourCardView: View {
    let card: TourCard
    let isLast: Bool
    let onTryIt: () -> Void

    var body: some View {
        Theme.card {
            HStack(alignment: .top, spacing: Theme.padLg) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Stage \(card.stage)")
                        .font(.caption).foregroundStyle(.secondary)
                        .textCase(.uppercase)
                    Text(card.title)
                        .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                    Text(card.blurb)
                        .font(.body)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)

                    VStack(alignment: .leading, spacing: 0) {
                        Text(card.statValue)
                            .font(.system(.title, design: .monospaced).weight(.medium))
                            .foregroundStyle(card.tint)
                        Text(card.statLabel)
                            .font(.caption).foregroundStyle(.secondary)
                            .textCase(.uppercase)
                    }
                    .padding(.top, 8)

                    if isLast {
                        Button(action: onTryIt) {
                            Label("Try it →", systemImage: "play.fill")
                                .font(.system(.body, design: .rounded).weight(.semibold))
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(Theme.accent)
                        .padding(.top, 12)
                    }
                    Spacer(minLength: 0)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                ZStack {
                    RoundedRectangle(cornerRadius: 28, style: .continuous)
                        .fill(card.tint.opacity(0.12))
                    Image(systemName: card.symbol)
                        .font(.system(size: 96, weight: .light))
                        .foregroundStyle(card.tint)
                }
                .frame(width: 240, height: 240)
            }
        }
    }
}
