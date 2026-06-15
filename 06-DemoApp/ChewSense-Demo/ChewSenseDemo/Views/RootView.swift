import SwiftUI
import UserNotifications

struct RootView: View {
    @EnvironmentObject private var coordinator: MonitoringCoordinator
    @State private var notificationAuth: UNAuthorizationStatus = .notDetermined
    @State private var showModelInfo = false

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                background
                ScrollView {
                    VStack(spacing: 20) {
                        header
                        FaceAvatarView(
                            mouthOpenness: coordinator.mouthOpenness,
                            isEating: coordinator.eatingState == .chewing
                        )
                        .frame(width: avatarSize(in: geometry), height: avatarSize(in: geometry))
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("Live face avatar")
                        .accessibilityIdentifier("faceAvatar")

                        statusPill
                        subtitle
                        actionRow
                    }
                    .frame(maxWidth: 520)
                    .frame(minHeight: geometry.size.height - 32)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 16)
                    .frame(maxWidth: .infinity)
                }
            }
        }
        .task { await refreshNotificationAuth() }
        .sheet(isPresented: $showModelInfo) {
            ModelInfoView()
        }
    }

    private var background: some View {
        LinearGradient(
            colors: [
                Color(red: 0.98, green: 0.97, blue: 0.94),
                Color(red: 0.92, green: 0.95, blue: 0.99)
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
        .ignoresSafeArea()
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("ChewSense")
                    .font(.system(size: 24, weight: .heavy, design: .rounded))
                Text(coordinator.connectionStatus)
                    .font(.caption)
                    .foregroundStyle(connectionColor)
                    .lineLimit(2)
            }
            Spacer()
            Button {
                showModelInfo = true
            } label: {
                Image(systemName: "info.circle")
                    .font(.title2)
            }
            .accessibilityLabel("Model info")
        }
    }

    private var statusPill: some View {
        Text(statusLabel)
            .font(.system(size: 24, weight: .heavy, design: .rounded))
            .foregroundStyle(.white)
            .padding(.horizontal, 22)
            .padding(.vertical, 10)
            .background(Capsule().fill(statusColor))
            .shadow(color: statusColor.opacity(0.3), radius: 8, x: 0, y: 4)
            .animation(.spring(response: 0.35, dampingFraction: 0.7), value: statusLabel)
            .accessibilityIdentifier("eatingStatusPill")
    }

    private var subtitle: some View {
        VStack(spacing: 5) {
            Text(subtitleText)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            if coordinator.isMonitoring {
                Text(
                    String(
                        format: "p = %.2f  |  %.0f Hz  |  %@",
                        coordinator.smoothedProbability,
                        coordinator.sampleRateHz,
                        coordinator.sampleFreshnessText
                    )
                )
                .font(.caption2.monospaced())
                .foregroundStyle(.tertiary)
                .accessibilityIdentifier("streamMetrics")
            }
        }
    }

    @ViewBuilder
    private var actionRow: some View {
        VStack(spacing: 12) {
            if notificationAuth == .notDetermined {
                Button {
                    Task {
                        _ = await ChewEpisodeNotifier.requestAuthorization()
                        await refreshNotificationAuth()
                    }
                } label: {
                    Label("Allow notifications", systemImage: "bell.badge")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            }

            Button {
                coordinator.toggle()
            } label: {
                Label(actionLabel, systemImage: actionSystemImage)
                    .font(.headline)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .tint(coordinator.hasActiveSession ? .red : .accentColor)
            .accessibilityIdentifier("monitoringButton")

            if coordinator.connectionState == .waitingForAirPods {
                Text("Keep motion-capable AirPods in your ears and connected in iOS controls. ChewSense checks Core Motion every half second.")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("motionWaitingGuidance")
            }
        }
    }

    private var actionLabel: String {
        if coordinator.hasActiveSession { return "Stop monitoring" }
        if case .failed = coordinator.connectionState { return "Retry monitoring" }
        return "Start monitoring"
    }

    private var actionSystemImage: String {
        coordinator.hasActiveSession ? "stop.fill" : "play.fill"
    }

    private var statusLabel: String {
        switch coordinator.connectionState {
        case .streaming:
            return coordinator.eatingState == .chewing ? "CHEWING" : "NOT CHEWING"
        case .waitingForAirPods, .startingMotion:
            return "WAITING"
        case .failed:
            return "ERROR"
        case .idle:
            return "READY"
        }
    }

    private var statusColor: Color {
        switch coordinator.connectionState {
        case .streaming where coordinator.eatingState == .chewing:
            return .green
        case .streaming:
            return .gray
        case .waitingForAirPods, .startingMotion:
            return .orange
        case .failed:
            return .red
        case .idle:
            return .blue
        }
    }

    private var connectionColor: Color {
        switch coordinator.connectionState {
        case .streaming:
            return .green
        case .failed:
            return .red
        case .waitingForAirPods, .startingMotion:
            return .orange
        case .idle:
            return .secondary
        }
    }

    private var subtitleText: String {
        switch coordinator.connectionState {
        case .idle:
            return "Tap Start. ChewSense will wait for real AirPods motion samples."
        case .waitingForAirPods:
            return "Waiting for motion-capable AirPods. Monitoring has not started yet."
        case .startingMotion:
            return "AirPods found. Waiting for the first motion sample."
        case .streaming where coordinator.eatingState == .chewing:
            return "Chewing detected by the on-device model."
        case .streaming:
            return "Motion is live. Chew normally to test the model."
        case .failed(let message):
            return message
        }
    }

    private func avatarSize(in geometry: GeometryProxy) -> CGFloat {
        min(280, max(190, min(geometry.size.width - 48, geometry.size.height * 0.32)))
    }

    private func refreshNotificationAuth() async {
        notificationAuth = await ChewEpisodeNotifier.currentAuthorization()
    }
}

#Preview {
    RootView()
        .environmentObject(MonitoringCoordinator())
}
