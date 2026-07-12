import Foundation

enum CSVLoader {
    static func loadAlignedLabels(from url: URL) throws -> [AlignedLabel] {
        let raw = try String(contentsOf: url, encoding: .utf8)
        var rows = raw.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
        guard !rows.isEmpty else { return [] }
        let header = rows.removeFirst()
            .split(separator: ",", omittingEmptySubsequences: false)
            .map(String.init)
        let index = Dictionary(uniqueKeysWithValues: header.enumerated().map { ($1, $0) })

        func value(_ columns: [String], _ name: String, fallback: String = "") -> String {
            guard let i = index[name], columns.indices.contains(i) else { return fallback }
            return columns[i]
        }

        var out: [AlignedLabel] = []
        out.reserveCapacity(rows.count)
        for (i, line) in rows.enumerated() {
            let cols = line.split(separator: ",", omittingEmptySubsequences: false).map(String.init)
            guard !cols.isEmpty else { continue }
            out.append(AlignedLabel(
                rowIndex: i,
                timestamp: Double(value(cols, "timestamp", fallback: "0")) ?? 0,
                videoTS: Double(value(cols, "video_ts", fallback: "0")) ?? 0,
                eating: Int(value(cols, "eating", fallback: "0")) ?? 0,
                chewPhase: value(cols, "chew_phase", fallback: "idle"),
                chewID: value(cols, "chew_id"),
                videoConfidence: Double(value(cols, "video_confidence", fallback: "0")) ?? 0,
                mouthState: value(cols, "mouth_state", fallback: "unknown"),
                biteEvent: Int(value(cols, "bite_event", fallback: "0")) ?? 0,
                biteID: value(cols, "bite_id"),
                boutID: value(cols, "bout_id"),
                labelValid: Int(value(cols, "label_valid", fallback: "0")) ?? 0,
                labelSource: value(cols, "label_source", fallback: "automatic"),
                modelID: value(cols, "model_id"),
                annotationRevision: Int(value(cols, "annotation_revision", fallback: "0")) ?? 0
            ))
        }
        return out
    }

    static func writeAlignedLabels(_ rows: [AlignedLabel], to url: URL) throws {
        var text = AlignedLabel.csvHeader + "\n"
        text.reserveCapacity(rows.count * 64)
        for row in rows {
            text += row.toCSVRow()
            text += "\n"
        }
        try text.write(to: url, atomically: true, encoding: .utf8)
    }
}
