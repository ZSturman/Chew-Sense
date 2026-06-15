import Foundation
import Combine

/// Reads a CSV (`timestamp,dtNs,ax,ay,az,gx,gy,gz,label`) and replays samples
/// into a callback at a controllable rate.
final class ReplayPlayer: ObservableObject {

    enum Speed: Double, CaseIterable, Identifiable {
        case half = 0.5, one = 1, two = 2, four = 4
        var id: Double { rawValue }
        var label: String {
            switch self {
            case .half: return "0.5×"
            case .one:  return "1×"
            case .two:  return "2×"
            case .four: return "4×"
            }
        }
    }

    @Published var speed: Speed = .one
    @Published private(set) var isPlaying = false
    @Published private(set) var progress: Double = 0   // 0...1
    @Published private(set) var currentTime: TimeInterval = 0
    @Published private(set) var totalTime: TimeInterval = 0

    private(set) var samples: [MotionSample] = []
    private var index: Int = 0
    private var task: Task<Void, Never>?
    var onSample: ((MotionSample) -> Void)?
    var onFinished: (() -> Void)?

    /// Load a CSV from the package resources.
    func load(resource: String, withExtension ext: String = "csv") throws {
        guard let url = Bundle.module.url(forResource: resource, withExtension: ext) else {
            throw NSError(domain: "ReplayPlayer", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Resource \(resource).\(ext) not found in bundle"])
        }
        try load(url: url)
    }

    func load(url: URL) throws {
        let raw = try String(contentsOf: url, encoding: .utf8)
        let rows = raw.split(whereSeparator: \.isNewline)
        guard rows.count > 1 else { throw NSError(domain: "ReplayPlayer", code: 2) }

        let header = rows[0].lowercased().split(separator: ",").map(String.init)
        let idx: (String) -> Int? = { name in header.firstIndex(of: name) }
        guard let tIdx = idx("timestamp"),
              let axI = idx("ax"), let ayI = idx("ay"), let azI = idx("az"),
              let gxI = idx("gx"), let gyI = idx("gy"), let gzI = idx("gz") else {
            throw NSError(domain: "ReplayPlayer", code: 3,
                          userInfo: [NSLocalizedDescriptionKey: "CSV missing required columns"])
        }

        var parsed: [MotionSample] = []
        parsed.reserveCapacity(rows.count - 1)
        for row in rows.dropFirst() {
            let cols = row.split(separator: ",", omittingEmptySubsequences: false).map(String.init)
            guard cols.count > max(tIdx, gzI) else { continue }
            guard let t = Double(cols[tIdx]),
                  let ax = Double(cols[axI]), let ay = Double(cols[ayI]), let az = Double(cols[azI]),
                  let gx = Double(cols[gxI]), let gy = Double(cols[gyI]), let gz = Double(cols[gzI])
            else { continue }
            parsed.append(MotionSample(timestamp: t, ax: ax, ay: ay, az: az, gx: gx, gy: gy, gz: gz))
        }

        // Re-base timestamps so the first sample is t = 0.
        if let first = parsed.first {
            parsed = parsed.map {
                MotionSample(timestamp: $0.timestamp - first.timestamp,
                             ax: $0.ax, ay: $0.ay, az: $0.az,
                             gx: $0.gx, gy: $0.gy, gz: $0.gz)
            }
        }

        samples = parsed
        index = 0
        currentTime = 0
        totalTime = parsed.last?.timestamp ?? 0
        progress = 0
    }

    func play() {
        guard !isPlaying, !samples.isEmpty else { return }
        isPlaying = true
        task = Task { [weak self] in
            await self?.runLoop()
        }
    }

    func pause() {
        isPlaying = false
        task?.cancel()
        task = nil
    }

    func restart() {
        pause()
        index = 0
        currentTime = 0
        progress = 0
    }

    private func runLoop() async {
        var lastSampleT: TimeInterval? = nil
        while !Task.isCancelled, isPlaying, index < samples.count {
            let sample = samples[index]
            // Wait the appropriate inter-sample delay (scaled by playback speed).
            if let prev = lastSampleT {
                let dt = max(0, (sample.timestamp - prev) / speed.rawValue)
                if dt > 0 {
                    let ns = UInt64(dt * 1_000_000_000)
                    try? await Task.sleep(nanoseconds: ns)
                }
            }
            if Task.isCancelled { break }
            await MainActor.run { [weak self] in
                guard let self else { return }
                self.onSample?(sample)
                self.currentTime = sample.timestamp
                self.totalTime = self.samples.last?.timestamp ?? 0
                self.progress = self.totalTime > 0 ? sample.timestamp / self.totalTime : 0
            }
            lastSampleT = sample.timestamp
            index += 1
        }
        if !Task.isCancelled, index >= samples.count {
            await MainActor.run { [weak self] in
                self?.isPlaying = false
                self?.onFinished?()
            }
        }
    }
}
