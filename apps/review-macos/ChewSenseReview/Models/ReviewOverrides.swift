import Foundation

enum ReviewDisposition: String, Codable, CaseIterable {
    case accepted
    case edited
    case rejected
    case unobservable
    case uncertain
    case added

    var isReviewed: Bool { true }
    var contributesPhaseSupervision: Bool { self == .accepted || self == .edited || self == .added }
}

/// Mutable v2 review draft. It contains reviewer decisions only and is bound
/// to one immutable candidate revision/hash.
struct ReviewDraft: Codable, Equatable {
    static let schemaVersion = "2.0.0"
    static let artifactType = "review_draft"

    var decodedSchemaVersion: String = Self.schemaVersion
    var decodedArtifactType: String = Self.artifactType
    var sessionID: String
    var candidateRevision: Int
    var candidateSHA256: String
    var reviewer: String?
    var updatedAt: String
    var verified: Bool
    var fullVideoPassCompleted: Bool
    var chewDecisions: [ChewDecision]
    var foodEntryDecisions: [FoodEntryDecision]
    var addedChews: [AddedChew]
    var addedFoodEntries: [AddedFoodEntry]
    var notes: String?

    struct ChewDecision: Codable, Equatable, Identifiable {
        var candidateID: String
        var disposition: ReviewDisposition
        var startClosedTS: Double?
        var openPeakTS: Double?
        var endClosedTS: Double?
        var note: String?

        var id: String { candidateID }

        enum CodingKeys: String, CodingKey {
            case candidateID = "candidate_id"
            case disposition
            case startClosedTS = "start_closed_ts"
            case openPeakTS = "open_peak_ts"
            case endClosedTS = "end_closed_ts"
            case note
        }
    }

    struct FoodEntryDecision: Codable, Equatable, Identifiable {
        var candidateID: String
        var disposition: ReviewDisposition
        var foodEntryTS: Double?
        var note: String?

        var id: String { candidateID }

        enum CodingKeys: String, CodingKey {
            case candidateID = "candidate_id"
            case disposition
            case foodEntryTS = "food_entry_ts"
            case note
        }
    }

    struct AddedChew: Codable, Equatable, Identifiable {
        var id: String
        var startClosedTS: Double
        var openPeakTS: Double
        var endClosedTS: Double
        var disposition: ReviewDisposition = .added
        var confidence: Double = 1.0
        var note: String?

        enum CodingKeys: String, CodingKey {
            case id
            case startClosedTS = "start_closed_ts"
            case openPeakTS = "open_peak_ts"
            case endClosedTS = "end_closed_ts"
            case disposition
            case confidence
            case note
        }
    }

    struct AddedFoodEntry: Codable, Equatable, Identifiable {
        var id: String
        var foodEntryTS: Double
        var disposition: ReviewDisposition = .added
        var confidence: Double = 1.0
        var note: String?

        enum CodingKeys: String, CodingKey {
            case id
            case foodEntryTS = "food_entry_ts"
            case disposition
            case confidence
            case note
        }
    }

    enum CodingKeys: String, CodingKey {
        case decodedSchemaVersion = "schema_version"
        case decodedArtifactType = "artifact_type"
        case sessionID = "session_id"
        case candidateRevision = "candidate_revision"
        case candidateSHA256 = "candidate_sha256"
        case reviewer
        case updatedAt = "updated_at"
        case verified
        case fullVideoPassCompleted = "full_video_pass_completed"
        case chewDecisions = "chew_decisions"
        case foodEntryDecisions = "food_entry_decisions"
        case addedChews = "added_chews"
        case addedFoodEntries = "added_food_entries"
        case notes
    }

    init(
        sessionID: String,
        candidateRevision: Int,
        candidateSHA256: String,
        reviewer: String? = nil,
        updatedAt: String = ISO8601DateFormatter().string(from: Date()),
        verified: Bool = false,
        fullVideoPassCompleted: Bool = false,
        chewDecisions: [ChewDecision] = [],
        foodEntryDecisions: [FoodEntryDecision] = [],
        addedChews: [AddedChew] = [],
        addedFoodEntries: [AddedFoodEntry] = [],
        notes: String? = nil
    ) {
        self.sessionID = sessionID
        self.candidateRevision = candidateRevision
        self.candidateSHA256 = candidateSHA256
        self.reviewer = reviewer
        self.updatedAt = updatedAt
        self.verified = verified
        self.fullVideoPassCompleted = fullVideoPassCompleted
        self.chewDecisions = chewDecisions
        self.foodEntryDecisions = foodEntryDecisions
        self.addedChews = addedChews
        self.addedFoodEntries = addedFoodEntries
        self.notes = notes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        decodedSchemaVersion = try c.decode(String.self, forKey: .decodedSchemaVersion)
        decodedArtifactType = try c.decode(String.self, forKey: .decodedArtifactType)
        sessionID = try c.decode(String.self, forKey: .sessionID)
        candidateRevision = try c.decode(Int.self, forKey: .candidateRevision)
        candidateSHA256 = try c.decode(String.self, forKey: .candidateSHA256)
        reviewer = try c.decodeIfPresent(String.self, forKey: .reviewer)
        updatedAt = try c.decode(String.self, forKey: .updatedAt)
        verified = try c.decodeIfPresent(Bool.self, forKey: .verified) ?? false
        fullVideoPassCompleted = try c.decodeIfPresent(Bool.self, forKey: .fullVideoPassCompleted) ?? false
        chewDecisions = try c.decodeIfPresent([ChewDecision].self, forKey: .chewDecisions) ?? []
        foodEntryDecisions = try c.decodeIfPresent([FoodEntryDecision].self, forKey: .foodEntryDecisions) ?? []
        addedChews = try c.decodeIfPresent([AddedChew].self, forKey: .addedChews) ?? []
        addedFoodEntries = try c.decodeIfPresent([AddedFoodEntry].self, forKey: .addedFoodEntries) ?? []
        notes = try c.decodeIfPresent(String.self, forKey: .notes)
    }

    func validateLineage(bundle: CandidateBundle, computedHash: String) throws {
        guard decodedSchemaVersion == Self.schemaVersion else {
            throw ReviewDraftError.unsupportedSchema(decodedSchemaVersion)
        }
        guard decodedArtifactType == Self.artifactType else {
            throw ReviewDraftError.invalidArtifactType(decodedArtifactType)
        }
        guard sessionID == bundle.sessionID else {
            throw ReviewDraftError.sessionMismatch
        }
        guard candidateRevision == bundle.candidateRevision else {
            throw ReviewDraftError.staleRevision(expected: bundle.candidateRevision, actual: candidateRevision)
        }
        guard candidateSHA256.lowercased() == computedHash.lowercased() else {
            throw ReviewDraftError.staleHash(expected: computedHash, actual: candidateSHA256)
        }
    }

    static func empty(sessionID: String, candidateRevision: Int, candidateSHA256: String) -> ReviewDraft {
        ReviewDraft(
            sessionID: sessionID,
            candidateRevision: candidateRevision,
            candidateSHA256: candidateSHA256
        )
    }
}

enum ReviewDraftError: LocalizedError, Equatable {
    case unsupportedSchema(String)
    case invalidArtifactType(String)
    case sessionMismatch
    case staleRevision(expected: Int, actual: Int)
    case staleHash(expected: String, actual: String)
    case missingCandidate(String)
    case duplicateDecision(String)
    case invalidDisposition(String)
    case invalidAnchors(String)
    case overlap(String)

    var errorDescription: String? {
        switch self {
        case .unsupportedSchema(let value):
            "Review schema \(value) cannot be opened as v2. Migrate explicitly and re-review."
        case .invalidArtifactType(let value):
            "Expected a review_draft artifact, found \(value)."
        case .sessionMismatch:
            "The review draft belongs to a different session."
        case .staleRevision(let expected, let actual):
            "Review is linked to candidate revision \(actual), but revision \(expected) is open. Reopen the linked artifact or begin a fresh review."
        case .staleHash(let expected, let actual):
            "Review candidate hash is stale (review \(actual), artifact \(expected)). Saving and finalization are blocked."
        case .missingCandidate(let id):
            "Decision references missing candidate \(id)."
        case .duplicateDecision(let id):
            "Candidate \(id) has more than one decision."
        case .invalidDisposition(let id):
            "Candidate \(id) has a disposition that is not valid for a proposal."
        case .invalidAnchors(let id):
            "\(id) must satisfy Closed start < Maximum open < Closed end."
        case .overlap(let id):
            "\(id) overlaps the preceding chew. Review will not repair it silently."
        }
    }
}

// Retain the old type name inside the app while the UI is migrated. The JSON
// representation is exclusively the v2 ReviewDraft contract above.
typealias ReviewOverrides = ReviewDraft
