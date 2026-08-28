//
//  CoachingContracts.swift
//  ChewSenseLive
//
//  Personalized, wellness-only coaching contracts. No universal medical
//  thresholds are encoded here.
//

import Foundation

enum MealTexture: String, CaseIterable, Codable, Identifiable {
    case soft
    case mixed
    case hard
    case unknown

    var id: String { rawValue }
    var title: String { rawValue.capitalized }

    /// Food-entry grouping is required before a chew-count goal is meaningful.
    var allowsChewCountGoal: Bool { self != .unknown }
}

struct VerifiedCalibrationMeal: Codable, Equatable {
    /// Stable bundle identity prevents one recording from being counted more
    /// than once when the wearer taps Verify repeatedly.
    let sessionID: String?
    let medianInterChewIntervalSec: Double
    let medianChewsPerVerifiedFoodEntry: Double?
}

struct PersonalizedCoachingGoals: Codable, Equatable {
    var minimumMedianInterChewIntervalSec: Double
    var chewsPerFoodEntry: Int?
}

struct PersonalBaselineBook: Codable, Equatable {
    private(set) var meals: [MealTexture: [VerifiedCalibrationMeal]] = [:]
    private(set) var goalOverrides: [MealTexture: PersonalizedCoachingGoals] = [:]

    @discardableResult
    mutating func addVerifiedMeal(
        texture: MealTexture,
        medianInterChewIntervalSec: Double,
        medianChewsPerVerifiedFoodEntry: Double? = nil,
        sessionID: String? = nil
    ) -> Bool {
        guard medianInterChewIntervalSec > 0 else { return false }
        if let sessionID,
           meals[texture, default: []].contains(where: { $0.sessionID == sessionID }) {
            return false
        }
        meals[texture, default: []].append(
            VerifiedCalibrationMeal(
                sessionID: sessionID,
                medianInterChewIntervalSec: medianInterChewIntervalSec,
                medianChewsPerVerifiedFoodEntry: medianChewsPerVerifiedFoodEntry
            )
        )
        return true
    }

    func verifiedMealCount(for texture: MealTexture) -> Int {
        meals[texture]?.count ?? 0
    }

    func defaultGoals(
        for texture: MealTexture,
        foodEntryCapabilityPromoted: Bool
    ) -> PersonalizedCoachingGoals? {
        guard let meals = meals[texture], meals.count >= 3 else { return nil }
        let cadence = Self.median(meals.map(\.medianInterChewIntervalSec))
        let chewGoal: Int?
        if foodEntryCapabilityPromoted && texture.allowsChewCountGoal {
            let counts = meals.compactMap(\.medianChewsPerVerifiedFoodEntry)
            chewGoal = counts.count >= 3 ? Int(Self.median(counts).rounded()) : nil
        } else {
            chewGoal = nil
        }
        return PersonalizedCoachingGoals(
            minimumMedianInterChewIntervalSec: cadence,
            chewsPerFoodEntry: chewGoal
        )
    }

    func goals(
        for texture: MealTexture,
        foodEntryCapabilityPromoted: Bool
    ) -> PersonalizedCoachingGoals? {
        guard var goals = goalOverrides[texture]
            ?? defaultGoals(
                for: texture,
                foodEntryCapabilityPromoted: foodEntryCapabilityPromoted
            ) else { return nil }
        if !foodEntryCapabilityPromoted || !texture.allowsChewCountGoal {
            goals.chewsPerFoodEntry = nil
        }
        return goals
    }

    mutating func setGoals(_ goals: PersonalizedCoachingGoals, for texture: MealTexture) {
        guard verifiedMealCount(for: texture) >= 3,
              goals.minimumMedianInterChewIntervalSec > 0 else { return }
        goalOverrides[texture] = goals
    }

    private static func median(_ values: [Double]) -> Double {
        let sorted = values.sorted()
        let middle = sorted.count / 2
        return sorted.count.isMultiple(of: 2)
            ? (sorted[middle - 1] + sorted[middle]) / 2
            : sorted[middle]
    }
}

final class PersonalBaselineStore {
    static let shared = PersonalBaselineStore()

    private let defaults: UserDefaults
    private let storageKey: String
    private(set) var book: PersonalBaselineBook

    init(
        defaults: UserDefaults = .standard,
        storageKey: String = "coaching.personalBaselineBook.v2"
    ) {
        self.defaults = defaults
        self.storageKey = storageKey
        if let data = defaults.data(forKey: storageKey),
           let decoded = try? JSONDecoder().decode(PersonalBaselineBook.self, from: data) {
            self.book = decoded
        } else {
            self.book = PersonalBaselineBook()
        }
    }

    func goals(
        for texture: MealTexture,
        capabilities: ModelCapabilities
    ) -> PersonalizedCoachingGoals? {
        guard capabilities.cadenceCoachingEnabled else { return nil }
        return book.goals(
            for: texture,
            foodEntryCapabilityPromoted: capabilities.foodEntryCoachingEnabled
        )
    }

    @discardableResult
    func addVerifiedMeal(
        texture: MealTexture,
        medianInterChewIntervalSec: Double,
        medianChewsPerVerifiedFoodEntry: Double? = nil,
        sessionID: String? = nil
    ) -> Bool {
        let inserted = book.addVerifiedMeal(
            texture: texture,
            medianInterChewIntervalSec: medianInterChewIntervalSec,
            medianChewsPerVerifiedFoodEntry: medianChewsPerVerifiedFoodEntry,
            sessionID: sessionID
        )
        if inserted { persist() }
        return inserted
    }

    func setGoals(_ goals: PersonalizedCoachingGoals, for texture: MealTexture) {
        book.setGoals(goals, for: texture)
        persist()
    }

    private func persist() {
        guard let data = try? JSONEncoder().encode(book) else { return }
        defaults.set(data, forKey: storageKey)
    }
}

struct ModelCapabilities: Equatable {
    let temporalChewEventHead: Bool
    let foodEntryHead: Bool
    /// The current five-output CoreML runtime has no food-entry decoder yet.
    /// Keeping this separate from registry promotion prevents a future flag
    /// flip from exposing bite coaching before the runtime can consume it.
    let foodEntryRuntimeImplemented: Bool

    init(
        temporalChewEventHead: Bool,
        foodEntryHead: Bool,
        foodEntryRuntimeImplemented: Bool = false
    ) {
        self.temporalChewEventHead = temporalChewEventHead
        self.foodEntryHead = foodEntryHead
        self.foodEntryRuntimeImplemented = foodEntryRuntimeImplemented
    }

    static var bundled: ModelCapabilities {
        ModelCapabilities(
            temporalChewEventHead: NormalizationConstants.MODE == "temporal_tcn"
                && NormalizationConstants.CHEW_EVENT_HEAD_PROMOTED,
            foodEntryHead: NormalizationConstants.MODE == "temporal_tcn"
                && NormalizationConstants.FOOD_ENTRY_HEAD_PROMOTED,
            foodEntryRuntimeImplemented: false
        )
    }

    var cadenceCoachingEnabled: Bool { temporalChewEventHead }
    var foodEntryCoachingEnabled: Bool {
        temporalChewEventHead && foodEntryHead && foodEntryRuntimeImplemented
    }

    func chewCountGoalEnabled(texture: MealTexture, verifiedMeals: Int) -> Bool {
        foodEntryCoachingEnabled && texture.allowsChewCountGoal && verifiedMeals >= 3
    }
}

struct CompletedMealCalibration: Equatable {
    let sessionID: String
    let texture: MealTexture
    let detectedChews: Int
    let medianInterChewIntervalSec: Double
    let confidenceCoverage: Double
    let chewEventModelPromoted: Bool

    static func load(from sessionURL: URL) throws -> CompletedMealCalibration {
        let metricsURL = sessionURL.appendingPathComponent("meal_metrics.json")
        let data = try Data(contentsOf: metricsURL)
        guard let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let textureRaw = payload["texture"] as? String,
              let texture = MealTexture(rawValue: textureRaw),
              let detectedChews = (payload["detected_chews"] as? NSNumber)?.intValue,
              let interval = payload["median_inter_chew_interval_sec"] as? Double,
              interval > 0 else {
            throw CalibrationVerificationError.missingConfidentCadence
        }
        return CompletedMealCalibration(
            sessionID: sessionURL.lastPathComponent,
            texture: texture,
            detectedChews: detectedChews,
            medianInterChewIntervalSec: interval,
            confidenceCoverage: payload["confidence_coverage"] as? Double ?? 0,
            chewEventModelPromoted: payload["chew_event_model_promoted"] as? Bool ?? false
        )
    }

    static func isVerified(at sessionURL: URL) -> Bool {
        FileManager.default.fileExists(
            atPath: sessionURL.appendingPathComponent("calibration_verification.json").path
        )
    }
}

struct CalibrationVerificationReceipt: Equatable {
    let texture: MealTexture
    let verifiedMealCount: Int
    let cadenceGoalActivated: Bool
    let alreadyVerified: Bool
}

struct CalibrationProgress: Equatable {
    let texture: MealTexture
    let verifiedMealCount: Int
    let cadenceGoalActivated: Bool
}

enum CalibrationVerificationError: LocalizedError {
    case missingConfidentCadence
    case unpromotedChewModel

    var errorDescription: String? {
        switch self {
        case .missingConfidentCadence:
            return "This session does not contain enough confident individual chews to establish a cadence baseline."
        case .unpromotedChewModel:
            return "Calibration requires a temporal chew-event model that has passed the locked promotion gates."
        }
    }
}

enum CalibrationMealVerifier {
    static func verify(
        sessionURL: URL,
        store: PersonalBaselineStore,
        verifiedAt: Date = Date()
    ) throws -> CalibrationVerificationReceipt {
        let meal = try CompletedMealCalibration.load(from: sessionURL)
        guard meal.chewEventModelPromoted else {
            throw CalibrationVerificationError.unpromotedChewModel
        }

        let markerURL = sessionURL.appendingPathComponent("calibration_verification.json")
        let wasVerified = CompletedMealCalibration.isVerified(at: sessionURL)
        let inserted = store.addVerifiedMeal(
            texture: meal.texture,
            medianInterChewIntervalSec: meal.medianInterChewIntervalSec,
            sessionID: meal.sessionID
        )
        let count = store.book.verifiedMealCount(for: meal.texture)
        if !wasVerified {
            let payload: [String: Any] = [
                "schema_version": "2.0.0",
                "session_id": meal.sessionID,
                "texture": meal.texture.rawValue,
                "median_inter_chew_interval_sec": meal.medianInterChewIntervalSec,
                "verified_meal_count_after": count,
                "verified_at": ISO8601DateFormatter().string(from: verifiedAt),
                "purpose": "personal_cadence_calibration"
            ]
            let data = try JSONSerialization.data(
                withJSONObject: payload,
                options: [.prettyPrinted, .sortedKeys]
            )
            try data.write(to: markerURL, options: .atomic)
        }

        return CalibrationVerificationReceipt(
            texture: meal.texture,
            verifiedMealCount: count,
            cadenceGoalActivated: count >= 3,
            alreadyVerified: wasVerified || !inserted
        )
    }
}

enum CadenceCue: Equatable {
    case slowDown
}

final class CadenceCoach {
    let rollingIntervalCount: Int
    let sustainedViolationCount: Int
    let cooldownSec: Double
    let minimumConfidence: Double
    var minimumMedianIntervalSec: Double?

    private(set) var confidentIntervals: [Double] = []
    private(set) var rollingMedianIntervalSec: Double?
    private var previousConfidentChewTimestamp: Double?
    private var consecutiveViolations = 0
    private var lastCueTimestamp = -Double.infinity

    init(
        minimumMedianIntervalSec: Double?,
        rollingIntervalCount: Int = 5,
        sustainedViolationCount: Int = 3,
        cooldownSec: Double = 20,
        minimumConfidence: Double = RuntimeConfig.confidentEventThreshold
    ) {
        self.minimumMedianIntervalSec = minimumMedianIntervalSec
        self.rollingIntervalCount = rollingIntervalCount
        self.sustainedViolationCount = sustainedViolationCount
        self.cooldownSec = cooldownSec
        self.minimumConfidence = minimumConfidence
    }

    func reset() {
        confidentIntervals.removeAll(keepingCapacity: true)
        rollingMedianIntervalSec = nil
        previousConfidentChewTimestamp = nil
        consecutiveViolations = 0
        lastCueTimestamp = -Double.infinity
    }

    @discardableResult
    func recordChew(timestamp: Double, confidence: Double) -> CadenceCue? {
        guard confidence >= minimumConfidence else { return nil }
        defer { previousConfidentChewTimestamp = timestamp }
        guard let previous = previousConfidentChewTimestamp,
              timestamp > previous else { return nil }

        confidentIntervals.append(timestamp - previous)
        if confidentIntervals.count > rollingIntervalCount {
            confidentIntervals.removeFirst(confidentIntervals.count - rollingIntervalCount)
        }
        guard confidentIntervals.count == rollingIntervalCount else { return nil }
        let median = Self.median(confidentIntervals)
        rollingMedianIntervalSec = median
        guard let goal = minimumMedianIntervalSec else { return nil }

        if median < goal {
            consecutiveViolations += 1
        } else {
            consecutiveViolations = 0
        }
        guard consecutiveViolations >= sustainedViolationCount,
              timestamp - lastCueTimestamp >= cooldownSec else { return nil }
        consecutiveViolations = 0
        lastCueTimestamp = timestamp
        return .slowDown
    }

    private static func median(_ values: [Double]) -> Double {
        let sorted = values.sorted()
        let middle = sorted.count / 2
        return sorted.count.isMultiple(of: 2)
            ? (sorted[middle - 1] + sorted[middle]) / 2
            : sorted[middle]
    }
}
