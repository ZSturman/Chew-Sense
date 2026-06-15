import Foundation
import CoreMotion
import Combine

/// Live AirPods motion source. Emits MotionSamples on the main actor.
@MainActor
final class LiveMotionSource: ObservableObject {

    @Published private(set) var isAvailable: Bool = false
    @Published private(set) var isStarting: Bool = false
    @Published private(set) var isRunning: Bool = false
    @Published private(set) var connectionStatus: String = "Idle"

    private let manager = CMHeadphoneMotionManager()
    private var availabilityTask: Task<Void, Never>?
    private var startTimeoutTask: Task<Void, Never>?
    var onSample: ((MotionSample) -> Void)?

    var isActive: Bool { isStarting || isRunning }

    init() {
        refreshAvailability()
        availabilityTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(500))
                self?.refreshAvailability()
            }
        }
    }

    func start() {
        refreshAvailability()
        guard manager.isDeviceMotionAvailable else {
            connectionStatus = "AirPods Pro/Max not detected"
            return
        }
        guard !manager.isDeviceMotionActive else { return }
        connectionStatus = "Starting motion stream"
        isStarting = true
        isRunning = false
        manager.startDeviceMotionUpdates(to: .main) { [weak self] motion, error in
            guard let self else { return }
            if let error {
                self.connectionStatus = "Error: \(error.localizedDescription)"
                self.isStarting = false
                self.isRunning = false
                return
            }
            guard let motion else { return }
            self.startTimeoutTask?.cancel()
            self.isStarting = false
            self.isRunning = true
            self.connectionStatus = "Streaming"
            let sample = MotionSample(
                timestamp: motion.timestamp,
                ax: motion.userAcceleration.x,
                ay: motion.userAcceleration.y,
                az: motion.userAcceleration.z,
                gx: motion.rotationRate.x,
                gy: motion.rotationRate.y,
                gz: motion.rotationRate.z
            )
            self.onSample?(sample)
        }
        startTimeoutTask?.cancel()
        startTimeoutTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(3))
            guard let self, self.isStarting else { return }
            self.manager.stopDeviceMotionUpdates()
            self.isStarting = false
            self.connectionStatus = "No motion samples received"
        }
    }

    func stop() {
        startTimeoutTask?.cancel()
        if manager.isDeviceMotionActive {
            manager.stopDeviceMotionUpdates()
        }
        isStarting = false
        isRunning = false
        connectionStatus = "Stopped"
    }

    private func refreshAvailability() {
        isAvailable = manager.isDeviceMotionAvailable
    }
}
