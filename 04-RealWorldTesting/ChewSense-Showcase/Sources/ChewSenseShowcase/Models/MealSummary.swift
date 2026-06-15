import Foundation

/// A single chewing episode (a "bite") observed during a session.
struct BiteEvent: Identifiable, Hashable {
    let id = UUID()
    let startSeconds: TimeInterval
    let endSeconds: TimeInterval
    let chewCount: Int

    var durationSeconds: TimeInterval { max(endSeconds - startSeconds, 0) }
    var chewRateHz: Double {
        durationSeconds > 0 ? Double(chewCount) / durationSeconds : 0
    }
}

/// Aggregate statistics for a session, ready to feed the meal report UI.
struct MealSummary: Identifiable {
    let id = UUID()
    let label: String
    let totalSeconds: TimeInterval
    let bites: [BiteEvent]

    var biteCount: Int { bites.count }
    var totalChews: Int { bites.reduce(0) { $0 + $1.chewCount } }
    var avgChewsPerBite: Double {
        biteCount > 0 ? Double(totalChews) / Double(biteCount) : 0
    }
    var avgChewRateHz: Double {
        let rates = bites.map(\.chewRateHz).filter { $0 > 0 }
        return rates.isEmpty ? 0 : rates.reduce(0, +) / Double(rates.count)
    }
    var rushedBiteCount: Int {
        let threshold = RuntimeConfig.rushedChewsPerBite
        return bites.filter { $0.chewCount < threshold }.count
    }

    var qualityGrade: QualityGrade {
        // Composite score in [0, 100] mirroring the spirit of ChewQualityEvaluator:
        //   chews-per-bite contributes up to 60 (target 12+).
        //   rushed-bite fraction subtracts up to 40.
        guard biteCount > 0 else { return .insufficientData }
        let chewScore = min(60.0, (avgChewsPerBite / 12.0) * 60.0)
        let rushedFrac = Double(rushedBiteCount) / Double(biteCount)
        let rushedPenalty = rushedFrac * 40.0
        let score = max(0.0, min(100.0, 60.0 - rushedPenalty + chewScore - 20.0))
        switch score {
        case 85...:    return .excellent
        case 70..<85:  return .good
        case 50..<70:  return .fair
        default:       return .needsWork
        }
    }
}

enum QualityGrade: String {
    case excellent     = "EXCELLENT"
    case good          = "GOOD"
    case fair          = "FAIR"
    case needsWork     = "NEEDS WORK"
    case insufficientData = "—"
}
