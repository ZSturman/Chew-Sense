//
//  ChewEpisodeNotifier.swift
//  ChewSenseDemo
//
//  Schedules a local notification once a chewing episode has been
//  sustained for `confirmationGraceSeconds`. This filters out single
//  bites, yawns, or coughs that the classifier momentarily flags.
//

import Foundation
import UserNotifications
import UIKit

@MainActor
final class ChewEpisodeNotifier: ChewEpisodeNotifying {

    /// Grace period after `startedEpisode` before we fire the notification.
    /// Combined with ChewDetector's `minStartWindows = 3` × 0.25 s ≈ 0.75 s,
    /// total latency is ~3.75 s which keeps the demo feeling responsive
    /// while filtering single bites.
    let confirmationGraceSeconds: TimeInterval = 3.0

    /// Don't fire more often than this — a single meal should produce one
    /// notification, not one per bite.
    let cooldownSeconds: TimeInterval = 60

    private var pendingTask: Task<Void, Never>?
    private var lastFiredAt: Date?

    func handleEpisodeStarted() {
        // If a confirmation is already in flight, leave it alone.
        if pendingTask != nil { return }

        if let last = lastFiredAt, Date().timeIntervalSince(last) < cooldownSeconds {
            return
        }

        pendingTask = Task { [weak self] in
            guard let self else { return }
            try? await Task.sleep(nanoseconds: UInt64(self.confirmationGraceSeconds * 1_000_000_000))
            guard !Task.isCancelled else { return }
            await self.fireNotification()
            self.pendingTask = nil
        }
    }

    func handleEpisodeEnded() {
        // Episode died before we could confirm — cancel the pending fire.
        pendingTask?.cancel()
        pendingTask = nil
    }

    private func fireNotification() async {
        let content = UNMutableNotificationContent()
        content.title = "Looks like you're eating 🍎"
        content.body = "Open ChewSense to see your live mouth feed."
        content.sound = .default

        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 0.1, repeats: false)
        let request = UNNotificationRequest(identifier: "chew.episode.\(UUID().uuidString)",
                                            content: content,
                                            trigger: trigger)
        do {
            try await UNUserNotificationCenter.current().add(request)
            lastFiredAt = Date()

            // Subtle haptic when the app is in foreground (no-op when locked).
            let generator = UINotificationFeedbackGenerator()
            generator.notificationOccurred(.success)
        } catch {
            NSLog("ChewEpisodeNotifier failed to schedule: \(error)")
        }
    }

    // MARK: Permissions helper

    static func requestAuthorization() async -> Bool {
        do {
            return try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])
        } catch {
            return false
        }
    }

    static func currentAuthorization() async -> UNAuthorizationStatus {
        await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
    }
}
