import Foundation

/// One row of `frame_predictions.csv` reduced to the fields the reviewer
/// needs for the cycle-shape graph: a video timestamp and a single
/// "mouth-open" magnitude. Pose-invariant `jaw_open_bs` (MediaPipe blendshape)
/// is preferred when present; falls back to `jaw_open_smooth`, then
/// `inner_lip_open`, then `jaw_open`.
struct FramePrediction {
    let ts: Double
    let mouthOpen: Double
    var headSpeed: Double? = nil
}

enum FramePredictionsLoader {
    private static let preferredColumns = [
        "jaw_open_bs",
        "jaw_open_smooth",
        "inner_lip_open",
        "jaw_open",
    ]

    static func load(from url: URL) -> [FramePrediction] {
        guard let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else {
            return []
        }
        // Split into non-empty lines; tolerate \r\n.
        var lines = text.split(whereSeparator: \.isNewline)
        guard let headerLine = lines.first else { return [] }
        lines.removeFirst()

        let headers = parseCSVRow(String(headerLine))
        guard let tsIdx = headers.firstIndex(of: "ts") else { return [] }
        let openIdx: Int? = preferredColumns
            .lazy
            .compactMap { headers.firstIndex(of: $0) }
            .first
        guard let mouthIdx = openIdx else { return [] }

        let angleIndices = ["head_yaw", "head_pitch", "head_roll"].compactMap { headers.firstIndex(of: $0) }
        let faceIndex = headers.firstIndex(of: "face_detected")
        var previousAngles: [Double]?
        var previousTime: Double?
        var out: [FramePrediction] = []
        out.reserveCapacity(lines.count)
        for line in lines {
            let cols = parseCSVRow(String(line))
            if cols.count <= max(tsIdx, mouthIdx) { continue }
            guard let ts = Double(cols[tsIdx]),
                  let raw = Double(cols[mouthIdx]) else { continue }
            let mouth = raw.isFinite ? raw : 0
            var speed: Double?
            let visible = faceIndex.map { cols.indices.contains($0) && ["true", "1"].contains(cols[$0].lowercased()) } ?? true
            if visible, angleIndices.count == 3, angleIndices.allSatisfy({ cols.indices.contains($0) }) {
                let angles = angleIndices.compactMap { Double(cols[$0]) }
                if angles.count == 3, angles.allSatisfy(\.isFinite) {
                    if let previousAngles, let previousTime, ts > previousTime, ts - previousTime <= 0.1 {
                        let squared = zip(angles, previousAngles).map { pow($0 - $1, 2) }.reduce(0, +)
                        speed = sqrt(squared) / (ts - previousTime)
                    }
                    previousAngles = angles
                    previousTime = ts
                }
            } else {
                previousAngles = nil
                previousTime = nil
            }
            out.append(FramePrediction(ts: ts, mouthOpen: mouth, headSpeed: speed))
        }
        return out
    }

    /// Minimal CSV row parser. Handles double-quoted fields with embedded
    /// commas, but the pipeline never emits those for `frame_predictions.csv`
    /// — this is defensive only.
    private static func parseCSVRow(_ row: String) -> [String] {
        var fields: [String] = []
        var current = ""
        var inQuotes = false
        var iter = row.makeIterator()
        while let ch = iter.next() {
            if ch == "\"" {
                inQuotes.toggle()
            } else if ch == "," && !inQuotes {
                fields.append(current)
                current = ""
            } else {
                current.append(ch)
            }
        }
        fields.append(current)
        return fields
    }
}

/// Synchronized six-axis AirPods evidence shown beside the visual jaw signal.
/// These samples are read-only reviewer context; they are never written into
/// the human annotation artifact.
struct MotionEvidenceSample: Identifiable, Equatable {
    let row: Int
    let videoTS: Double
    let accelerationMagnitude: Double
    let rotationMagnitude: Double
    let continuitySegment: Int

    var id: Int { row }
}

enum MotionEvidenceError: LocalizedError {
    case missingVideoClockOrigin
    case missingColumns

    var errorDescription: String? {
        switch self {
        case .missingVideoClockOrigin:
            "AirPods evidence hidden: session.json has no video_clock_origin_host_time_s."
        case .missingColumns:
            "AirPods evidence hidden: motion CSV is missing timestamp or six-axis columns."
        }
    }
}

enum MotionEvidenceLoader {
    static func load(from csvURL: URL, sessionManifestURL: URL) throws -> [MotionEvidenceSample] {
        let text = try String(contentsOf: csvURL, encoding: .utf8)
        var lines = text.split(whereSeparator: \.isNewline).map(String.init)
        guard !lines.isEmpty else { return [] }
        let headers = parseCSVRow(lines.removeFirst())
        let index = Dictionary(uniqueKeysWithValues: headers.enumerated().map { ($1, $0) })
        let videoIndex = index["video_ts"]
        guard let timestampIndex = index["timestamp"],
              let ax = index["ax"], let ay = index["ay"], let az = index["az"],
              let gx = index["gx"], let gy = index["gy"], let gz = index["gz"] else {
            throw MotionEvidenceError.missingColumns
        }
        let origin: Double?
        if videoIndex != nil {
            origin = nil
        } else {
            origin = try loadVideoOrigin(from: sessionManifestURL)
        }

        var result: [MotionEvidenceSample] = []
        result.reserveCapacity(lines.count)
        var previousVideoTS: Double?
        var continuitySegment = 0
        for (row, line) in lines.enumerated() {
            let columns = parseCSVRow(line)
            func number(_ column: Int) -> Double? {
                guard columns.indices.contains(column),
                      let value = Double(columns[column]), value.isFinite else { return nil }
                return value
            }
            guard let timestamp = number(timestampIndex),
                  let xA = number(ax), let yA = number(ay), let zA = number(az),
                  let xG = number(gx), let yG = number(gy), let zG = number(gz) else { continue }
            let videoTS: Double
            if let videoIndex, let explicit = number(videoIndex) {
                videoTS = explicit
            } else if let origin {
                videoTS = timestamp - origin
            } else {
                throw MotionEvidenceError.missingVideoClockOrigin
            }
            if let previousVideoTS,
               videoTS <= previousVideoTS || videoTS - previousVideoTS > 0.05 {
                continuitySegment += 1
            }
            previousVideoTS = videoTS
            result.append(.init(
                row: row,
                videoTS: videoTS,
                accelerationMagnitude: sqrt(xA * xA + yA * yA + zA * zA),
                rotationMagnitude: sqrt(xG * xG + yG * yG + zG * zG),
                continuitySegment: continuitySegment
            ))
        }
        return result.sorted { $0.videoTS < $1.videoTS }
    }

    private static func loadVideoOrigin(from url: URL) throws -> Double {
        guard let data = try? Data(contentsOf: url),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let clock = root["clock_sync"] as? [String: Any],
              let origin = clock["video_clock_origin_host_time_s"] as? NSNumber else {
            throw MotionEvidenceError.missingVideoClockOrigin
        }
        return origin.doubleValue
    }

    private static func parseCSVRow(_ row: String) -> [String] {
        var fields: [String] = []
        var current = ""
        var inQuotes = false
        for character in row {
            if character == "\"" {
                inQuotes.toggle()
            } else if character == ",", !inQuotes {
                fields.append(current)
                current = ""
            } else {
                current.append(character)
            }
        }
        fields.append(current)
        return fields
    }
}
