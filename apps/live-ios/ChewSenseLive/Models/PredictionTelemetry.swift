//
//  PredictionTelemetry.swift
//  ChewSenseLive
//
//  Small read-only helpers retained after removal of the obsolete in-app
//  annotation editor.
//

import Foundation

struct PredictionSpan: Identifiable, Codable, Equatable {
    var id: UUID
    var startTime: Double
    var endTime: Double
    var isChewing: Bool
    var probability: Double?
    var smoothedProbability: Double?

    init(
        id: UUID = UUID(),
        startTime: Double,
        endTime: Double,
        isChewing: Bool,
        probability: Double? = nil,
        smoothedProbability: Double? = nil
    ) {
        self.id = id
        self.startTime = startTime
        self.endTime = endTime
        self.isChewing = isChewing
        self.probability = probability
        self.smoothedProbability = smoothedProbability
    }
}

enum PredictionSpanResolver {
    static let coverageEpsilon = 0.05

    static func coverageSpans(
        from predictions: [PredictionSpan],
        duration: Double
    ) -> [PredictionSpan] {
        let sorted = predictions
            .filter { $0.endTime > $0.startTime }
            .sorted {
                if abs($0.endTime - $1.endTime) > coverageEpsilon {
                    return $0.endTime < $1.endTime
                }
                return $0.startTime < $1.startTime
            }
        let safeDuration = max(duration, sorted.map(\.endTime).max() ?? 0)
        guard safeDuration > coverageEpsilon else { return [] }
        guard !sorted.isEmpty else {
            return [PredictionSpan(startTime: 0, endTime: safeDuration, isChewing: false)]
        }

        var result: [PredictionSpan] = []
        var cursor = 0.0
        for span in sorted {
            let end = min(max(span.endTime, cursor), safeDuration)
            guard end > cursor + coverageEpsilon else { continue }
            append(
                PredictionSpan(
                    startTime: cursor,
                    endTime: end,
                    isChewing: span.isChewing,
                    probability: span.probability,
                    smoothedProbability: span.smoothedProbability
                ),
                to: &result
            )
            cursor = end
        }
        if cursor < safeDuration {
            append(
                PredictionSpan(
                    startTime: cursor,
                    endTime: safeDuration,
                    isChewing: result.last?.isChewing ?? false
                ),
                to: &result
            )
        }
        return result
    }

    private static func append(_ span: PredictionSpan, to result: inout [PredictionSpan]) {
        if var last = result.last, last.isChewing == span.isChewing {
            last.endTime = max(last.endTime, span.endTime)
            last.probability = span.probability ?? last.probability
            last.smoothedProbability = span.smoothedProbability ?? last.smoothedProbability
            result[result.count - 1] = last
        } else {
            result.append(span)
        }
    }
}

struct SessionCSVTable {
    var header: [String]
    var rows: [[String]]
}

enum SessionCSV {
    static func parse(_ text: String) -> SessionCSVTable {
        let lines = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
            .split(separator: "\n", omittingEmptySubsequences: false)
            .map(String.init)
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        guard let first = lines.first else {
            return SessionCSVTable(header: [], rows: [])
        }
        return SessionCSVTable(
            header: parseLine(first),
            rows: lines.dropFirst().map(parseLine)
        )
    }

    private static func parseLine(_ line: String) -> [String] {
        var result: [String] = []
        var field = ""
        var inQuotes = false
        var index = line.startIndex
        while index < line.endIndex {
            let character = line[index]
            if character == "\"" {
                let next = line.index(after: index)
                if inQuotes, next < line.endIndex, line[next] == "\"" {
                    field.append("\"")
                    index = line.index(after: next)
                    continue
                }
                inQuotes.toggle()
            } else if character == ",", !inQuotes {
                result.append(field)
                field = ""
            } else {
                field.append(character)
            }
            index = line.index(after: index)
        }
        result.append(field)
        return result
    }
}
