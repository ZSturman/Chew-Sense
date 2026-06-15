import Foundation

/// Decoded shape of `chew_cycles.json` written by the Phase 2 bridge.
struct ChewCyclesPayload: Codable {
    let videoID: String?
    let durationS: Double?
    let fps: Double?
    let timeOffset: Double?
    let nMotionRows: Int?
    let episodes: [Episode]

    struct Episode: Codable, Identifiable {
        let id: String
        let startTS: Double
        let endTS: Double
        let confidence: Double
        let chews: [Chew]
        let bites: [Bite]

        enum CodingKeys: String, CodingKey {
            case id
            case startTS = "start_ts"
            case endTS = "end_ts"
            case confidence
            case chews
            case bites
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = try c.decode(String.self, forKey: .id)
            startTS = try c.decode(Double.self, forKey: .startTS)
            endTS = try c.decode(Double.self, forKey: .endTS)
            confidence = try c.decode(Double.self, forKey: .confidence)
            chews = try c.decodeIfPresent([Chew].self, forKey: .chews) ?? []
            bites = try c.decodeIfPresent([Bite].self, forKey: .bites) ?? []
        }
    }

    struct Bite: Codable, Identifiable {
        let id: String
        let ts: Double
        let confidence: Double
    }

    struct Chew: Codable, Identifiable {
        let id: String
        let startTS: Double
        let peakTS: Double
        let endTS: Double
        let amplitude: Double?
        let confidence: Double

        enum CodingKeys: String, CodingKey {
            case id
            case startTS = "start_ts"
            case peakTS = "peak_ts"
            case endTS = "end_ts"
            case amplitude
            case confidence
        }
    }

    enum CodingKeys: String, CodingKey {
        case videoID = "video_id"
        case durationS = "duration_s"
        case fps
        case timeOffset = "time_offset"
        case nMotionRows = "n_motion_rows"
        case episodes
    }
}

struct ReviewBite: Identifiable, Equatable {
    enum Source: Equatable { case auto, added, edited }

    var id: String
    var ts: Double
    var confidence: Double
    var source: Source
}
