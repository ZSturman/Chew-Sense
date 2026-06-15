import CoreMotion
import Foundation

/// Owns one headphone motion manager for availability checks and streaming.
@MainActor
final class LiveMotionSource: MotionStreaming {
    private let manager: CMHeadphoneMotionManager

    init(manager: CMHeadphoneMotionManager = CMHeadphoneMotionManager()) {
        self.manager = manager
    }

    var isAvailable: Bool {
        manager.isDeviceMotionAvailable
    }

    @discardableResult
    func refreshAvailability() -> Bool {
        manager.isDeviceMotionAvailable
    }

    @discardableResult
    func start(
        onSample: @escaping (MotionSample) -> Void,
        onError: @escaping (Error) -> Void
    ) -> Bool {
        guard manager.isDeviceMotionAvailable else { return false }
        guard !manager.isDeviceMotionActive else { return true }

        manager.startDeviceMotionUpdates(to: .main) { motion, error in
            if let error {
                onError(error)
                return
            }
            guard let motion else { return }
            onSample(
                MotionSample(
                    timestamp: motion.timestamp,
                    ax: motion.userAcceleration.x,
                    ay: motion.userAcceleration.y,
                    az: motion.userAcceleration.z,
                    gx: motion.rotationRate.x,
                    gy: motion.rotationRate.y,
                    gz: motion.rotationRate.z
                )
            )
        }
        return true
    }

    func stop() {
        if manager.isDeviceMotionActive {
            manager.stopDeviceMotionUpdates()
        }
    }
}
