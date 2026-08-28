import Foundation

/// Launch arguments used only by XCTest. They keep UI tests deterministic and
/// prevent a simulator from requesting real camera, microphone, or AirPods
/// access while exercising the capture flow.
enum CollectionTestMode {
    static var isEnabled: Bool {
        ProcessInfo.processInfo.arguments.contains("--uitesting")
    }

    static var simulatedMotionAvailable: Bool {
        ProcessInfo.processInfo.arguments.contains("--ui-motion-available")
    }

    static func resetAppStateIfNeeded() {
        guard isEnabled else { return }
        UserDefaults.standard.set(true, forKey: "hasAcceptedDisclaimer")
        UserDefaults.standard.removeObject(forKey: "RecordingSessionMetadata")
        UserDefaults.standard.removeObject(forKey: "CaptureLocalReviewAudio")

        guard let documents = FileManager.default.urls(
            for: .documentDirectory,
            in: .userDomainMask
        ).first,
        let contents = try? FileManager.default.contentsOfDirectory(
            at: documents,
            includingPropertiesForKeys: nil,
            options: .skipsHiddenFiles
        ) else { return }

        for item in contents {
            try? FileManager.default.removeItem(at: item)
        }
    }
}
