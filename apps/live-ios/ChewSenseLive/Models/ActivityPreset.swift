//
//  ActivityPreset.swift
//  ChewSenseLive
//
//  Activity presets that tag every field-test session with a structured
//  activity slug consumed by the Python retraining pipeline (see
//  pipeline/field_tests.py and chewsense-motion --activity).
//
//  Detection parameters are NOT user-tunable in this app — they ship with
//  the deployed model. These presets exist purely to drive dataset coverage
//  and labeling during real-world data collection.
//

import Foundation

/// Coarse class the user expects this preset to produce. Used to gate the
/// "Mark as benchmark candidate" toggle and to surface guidance in the UI.
enum ExpectedLabelClass: String, Codable {
    /// Eating / chewing — should produce mostly positive labels.
    case positive
    /// Not eating — should produce mostly negative labels (useful negatives).
    case negative
    /// Mouth movement that is *not* eating; can confuse the model. Mixed.
    case mixed
    /// User-defined. We don't know what to expect.
    case unknown
}

enum ActivityPreset: String, CaseIterable, Identifiable, Codable {
    case eatingSoft
    case eatingHard
    case drinkingWater
    case baseline
    case walking
    case headShake
    case nodding
    case talking
    case singing
    case custom

    var id: String { rawValue }

    static func preset(forActivitySlug slug: String) -> ActivityPreset? {
        let normalized = slug.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return allCases.first { $0.slug == normalized }
    }

    /// Stable string written into session_metadata.json. Matches the
    /// vocabulary expected by the Python pipeline's per-activity comparator
    /// and balance gate.
    var slug: String {
        switch self {
        case .eatingSoft:    return "eating_soft"
        case .eatingHard:    return "eating_hard"
        case .drinkingWater: return "drinking_water"
        case .baseline:      return "not_eating"
        case .walking:       return "walking"
        case .headShake:     return "head_shake"
        case .nodding:       return "nodding"
        case .talking:       return "talking"
        case .singing:       return "singing"
        case .custom:        return "custom"
        }
    }

    var displayName: String {
        switch self {
        case .eatingSoft:    return "Eating soft food"
        case .eatingHard:    return "Eating hard food"
        case .drinkingWater: return "Drinking water"
        case .baseline:      return "Not eating / baseline"
        case .walking:       return "Walking"
        case .headShake:     return "Shaking head"
        case .nodding:       return "Nodding"
        case .talking:       return "Talking"
        case .singing:       return "Singing"
        case .custom:        return "Custom activity"
        }
    }

    var systemImage: String {
        switch self {
        case .eatingSoft:    return "leaf"
        case .eatingHard:    return "fork.knife"
        case .drinkingWater: return "drop"
        case .baseline:      return "moon"
        case .walking:       return "figure.walk"
        case .headShake:     return "arrow.left.and.right"
        case .nodding:       return "arrow.up.and.down"
        case .talking:       return "bubble.left.and.bubble.right"
        case .singing:       return "music.mic"
        case .custom:        return "pencil"
        }
    }

    var helpText: String {
        switch self {
        case .eatingSoft:
            return "Soft foods (yogurt, banana, bread). Should produce mostly chewing positives."
        case .eatingHard:
            return "Hard / crunchy foods (apple, chips, carrot). Strong chewing signal."
        case .drinkingWater:
            return "Drinking only. Mouth moves, but not chewing — useful negative example."
        case .baseline:
            return "Sitting quietly, not eating. Baseline negative."
        case .walking:
            return "Walking around without eating. Motion robustness negative."
        case .headShake:
            return "Repeated head shakes (no, no, no). High-value benchmark negative."
        case .nodding:
            return "Repeated nods. Useful negative — similar rhythm to chewing."
        case .talking:
            return "Talking at a normal pace. Mixed: jaw moves but is not chewing."
        case .singing:
            return "Singing or humming. Mixed: jaw and head movement, not chewing."
        case .custom:
            return "Free-form activity. Add a short description for the dataset."
        }
    }

    var expectedLabelClass: ExpectedLabelClass {
        switch self {
        case .eatingSoft, .eatingHard:
            return .positive
        case .drinkingWater, .baseline, .walking, .headShake, .nodding:
            return .negative
        case .talking, .singing:
            return .mixed
        case .custom:
            return .unknown
        }
    }

    /// Presets that the user explicitly called out as high-value benchmark
    /// negatives in the project plan. The benchmark-candidate toggle is
    /// pre-checked when one of these is selected.
    var defaultGoldCandidate: Bool {
        switch self {
        case .headShake, .baseline:
            return true
        default:
            return false
        }
    }
}
