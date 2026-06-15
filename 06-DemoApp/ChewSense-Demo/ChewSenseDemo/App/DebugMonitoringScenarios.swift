#if DEBUG
import Foundation

@MainActor
extension MonitoringCoordinator {
    static func makeForLaunchEnvironment() -> MonitoringCoordinator {
        let arguments = ProcessInfo.processInfo.arguments
        guard
            let marker = arguments.firstIndex(of: "--ui-scenario"),
            arguments.indices.contains(marker + 1)
        else {
            return MonitoringCoordinator()
        }

        let scenario = arguments[marker + 1]
        let timing = MonitoringTiming(
            availabilityPollSeconds: 0.02,
            firstSampleTimeoutSeconds: 0.2,
            staleSampleTimeoutSeconds: 0.3
        )

        switch scenario {
        case "waiting":
            return MonitoringCoordinator(
                motion: DebugMotionSource(isAvailable: false, emitsSamples: false),
                model: DebugModel(probability: 0.1),
                notifier: DebugNotifier(),
                timing: timing
            )
        case "chewing":
            return MonitoringCoordinator(
                motion: DebugMotionSource(isAvailable: true, emitsSamples: true),
                model: DebugModel(probability: 0.99),
                notifier: DebugNotifier(),
                timing: timing
            )
        case "streaming":
            return MonitoringCoordinator(
                motion: DebugMotionSource(isAvailable: true, emitsSamples: true),
                model: DebugModel(probability: 0.05),
                notifier: DebugNotifier(),
                timing: timing
            )
        default:
            return MonitoringCoordinator()
        }
    }
}

@MainActor
private final class DebugMotionSource: MotionStreaming {
    var isAvailable: Bool
    private let emitsSamples: Bool
    private var sampleTask: Task<Void, Never>?

    init(isAvailable: Bool, emitsSamples: Bool) {
        self.isAvailable = isAvailable
        self.emitsSamples = emitsSamples
    }

    func refreshAvailability() -> Bool {
        isAvailable
    }

    func start(
        onSample: @escaping (MotionSample) -> Void,
        onError: @escaping (Error) -> Void
    ) -> Bool {
        guard isAvailable else { return false }
        guard emitsSamples else { return true }

        sampleTask?.cancel()
        sampleTask = Task {
            var timestamp = 0.0
            while !Task.isCancelled {
                let phase = timestamp * 2 * Double.pi * 2
                onSample(
                    MotionSample(
                        timestamp: timestamp,
                        ax: 0.04 * sin(phase),
                        ay: 0.02 * cos(phase),
                        az: 0.01 * sin(phase * 0.5),
                        gx: 0.02 * sin(phase),
                        gy: 0.01 * cos(phase),
                        gz: 0.01 * sin(phase)
                    )
                )
                timestamp += 0.02
                try? await Task.sleep(nanoseconds: 20_000_000)
            }
        }
        return true
    }

    func stop() {
        sampleTask?.cancel()
        sampleTask = nil
    }
}

@MainActor
private final class DebugModel: ChewProbabilityPredicting {
    let probability: Double
    let loadError: String? = nil

    init(probability: Double) {
        self.probability = probability
    }

    func predictChewProbability(features: [Double]) throws -> Double {
        probability
    }
}

@MainActor
private final class DebugNotifier: ChewEpisodeNotifying {
    func handleEpisodeStarted() {}
    func handleEpisodeEnded() {}
}
#endif
