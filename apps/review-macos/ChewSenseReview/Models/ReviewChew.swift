import Foundation

enum ChewMarker: String, CaseIterable, Identifiable, Hashable {
    case start
    case peak
    case end

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .start: "Closed start"
        case .peak: "Maximum open"
        case .end: "Closed end"
        }
    }
}

struct ReviewChew: Identifiable, Hashable {
    enum Source: String, Hashable {
        case candidate
        case added
        case edited
    }

    var id: String
    var startTS: Double
    var peakTS: Double
    var endTS: Double
    var confidence: Double
    var source: Source
    var disposition: ReviewDisposition?

    var duration: Double { max(0, endTS - startTS) }

    func contains(_ videoTime: Double) -> Bool {
        videoTime >= startTS && videoTime <= endTS
    }

    func distance(to videoTime: Double) -> Double {
        if contains(videoTime) { return 0 }
        return min(abs(videoTime - startTS), abs(videoTime - endTS))
    }

    func timestamp(for marker: ChewMarker) -> Double {
        switch marker {
        case .start: startTS
        case .peak: peakTS
        case .end: endTS
        }
    }
}
