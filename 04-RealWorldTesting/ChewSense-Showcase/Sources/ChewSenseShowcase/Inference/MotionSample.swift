import Foundation

/// A single motion sample from AirPods (accelerometer + gyroscope, in CMHeadphone frame).
public struct MotionSample {
    public let timestamp: TimeInterval
    public let ax: Double
    public let ay: Double
    public let az: Double
    public let gx: Double
    public let gy: Double
    public let gz: Double

    public init(timestamp: TimeInterval, ax: Double, ay: Double, az: Double, gx: Double, gy: Double, gz: Double) {
        self.timestamp = timestamp
        self.ax = ax; self.ay = ay; self.az = az
        self.gx = gx; self.gy = gy; self.gz = gz
    }
}
