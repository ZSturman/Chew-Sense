import Foundation

struct AlignedLabel: Identifiable {
    var id: Int { rowIndex }
    let rowIndex: Int

    let timestamp: Double
    let videoTS: Double
    var eating: Int
    var chewPhase: String
    var chewID: String
    var videoConfidence: Double
    var mouthState: String = "unknown"
    var biteEvent: Int = 0
    var biteID: String = ""
    var boutID: String = ""
    var labelValid: Int = 0
    var labelSource: String = "automatic"
    var modelID: String = ""
    var annotationRevision: Int = 0
}

extension AlignedLabel {
    static let csvHeader = [
        "timestamp", "video_ts", "eating", "mouth_state", "chew_phase",
        "chew_id", "bite_event", "bite_id", "bout_id", "label_valid",
        "label_source", "model_id", "annotation_revision", "video_confidence"
    ].joined(separator: ",")

    func toCSVRow() -> String {
        [
            "\(timestamp)", "\(videoTS)", "\(eating)", mouthState, chewPhase,
            chewID, "\(biteEvent)", biteID, boutID, "\(labelValid)",
            labelSource, modelID, "\(annotationRevision)", "\(videoConfidence)"
        ].joined(separator: ",")
    }
}
