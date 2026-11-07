import Foundation

/// Distinguishes support for motion from actual recent sample delivery. A
/// capability flag can stay true throughout a disconnect/reconnect.
nonisolated struct HeadphoneMotionHealth {
    private(set) var lastSampleTime: Double?
    private(set) var lastArrivalTime: Double?
    private(set) var lastRestartTime: Double?
    static let freshSeconds = 0.5
    static let retrySeconds = 2.5

    func isFresh(at now: Double) -> Bool {
        guard let sample = lastSampleTime, let arrival = lastArrivalTime, now.isFinite else { return false }
        return now - sample >= -0.05 && now - sample <= Self.freshSeconds
            && now - arrival >= 0 && now - arrival <= Self.freshSeconds
    }

    func shouldRestart(at now: Double) -> Bool {
        !isFresh(at: now) && (lastRestartTime.map { now - $0 >= Self.retrySeconds } ?? true)
    }

    mutating func didRestart(at now: Double) {
        lastRestartTime = now
        invalidateSamples()
    }

    @discardableResult
    mutating func didReceive(sampleTime: Double, at now: Double) -> Bool {
        guard sampleTime.isFinite, now.isFinite, now - sampleTime >= -0.05,
              now - sampleTime <= Self.freshSeconds,
              lastSampleTime.map({ sampleTime > $0 }) ?? true else { return false }
        lastSampleTime = sampleTime
        lastArrivalTime = now
        return true
    }

    mutating func invalidateSamples() {
        lastSampleTime = nil
        lastArrivalTime = nil
    }
}

/// The same short, quiet connection cue in Collect and Live.
nonisolated enum AirPodsConnectionTone {
    static func data() -> Data {
        let rate = 22_050, count = rate * 3 / 10
        var data = Data()
        func word<T: FixedWidthInteger>(_ value: T) {
            var little = value.littleEndian
            withUnsafeBytes(of: &little) { data.append(contentsOf: $0) }
        }
        data.append(Data("RIFF".utf8)); word(UInt32(36 + count * 2)); data.append(Data("WAVEfmt ".utf8))
        word(UInt32(16)); word(UInt16(1)); word(UInt16(1)); word(UInt32(rate)); word(UInt32(rate * 2))
        word(UInt16(2)); word(UInt16(16)); data.append(Data("data".utf8)); word(UInt32(count * 2))
        for index in 0..<count {
            let fade = min(1, min(Double(index), Double(count - index - 1)) / 220)
            word(Int16(sin(2 * .pi * 660 * Double(index) / Double(rate)) * fade * 6_000))
        }
        return data
    }

}
