import Foundation

enum MotionConnectionState: Equatable {
    case idle
    case waitingForAirPods
    case startingMotion
    case streaming
    case failed(String)

    var statusText: String {
        switch self {
        case .idle:
            return "Ready"
        case .waitingForAirPods:
            return "Waiting for AirPods motion"
        case .startingMotion:
            return "Starting motion stream"
        case .streaming:
            return "Streaming AirPods motion"
        case .failed(let message):
            return message
        }
    }

    var isAttemptingConnection: Bool {
        switch self {
        case .waitingForAirPods, .startingMotion:
            return true
        default:
            return false
        }
    }
}

struct MonitoringTiming {
    let availabilityPollSeconds: TimeInterval
    let firstSampleTimeoutSeconds: TimeInterval
    let staleSampleTimeoutSeconds: TimeInterval

    static let production = MonitoringTiming(
        availabilityPollSeconds: 0.5,
        firstSampleTimeoutSeconds: 3,
        staleSampleTimeoutSeconds: 2
    )
}

@MainActor
protocol MotionStreaming: AnyObject {
    var isAvailable: Bool { get }

    @discardableResult
    func refreshAvailability() -> Bool

    @discardableResult
    func start(
        onSample: @escaping (MotionSample) -> Void,
        onError: @escaping (Error) -> Void
    ) -> Bool

    func stop()
}

@MainActor
protocol ChewProbabilityPredicting: AnyObject {
    var loadError: String? { get }
    func predictChewProbability(features: [Double]) throws -> Double
}

@MainActor
protocol ChewEpisodeNotifying: AnyObject {
    func handleEpisodeStarted()
    func handleEpisodeEnded()
}
