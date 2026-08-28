import CryptoKit
import Foundation

/// Immutable schema-v2 vision proposals consumed by Review.
///
/// Review never normalizes this payload. Its `content_sha256` is the stable
/// lineage key shared with Python audit/finalization.
struct CandidateBundle: Decodable {
    static let schemaVersion = "2.0.0"
    static let artifactType = "vision_candidates"

    let decodedSchemaVersion: String
    let decodedArtifactType: String
    let sessionID: String
    let candidateRevision: Int
    let generatedAt: String
    let modelVersions: [String: String]
    let coarseLabelState: String
    let requiresFullCoarseReview: Bool
    let chews: [Chew]
    let foodEntries: [FoodEntry]
    let contentSHA256: String

    /// Optional display metadata. It is accepted when present but is not part
    /// of the required contract.
    let fps: Double?
    let durationS: Double?

    struct Chew: Decodable, Identifiable, Hashable {
        let id: String
        let startClosedTS: Double
        let openPeakTS: Double
        let endClosedTS: Double
        let confidence: Double

        enum CodingKeys: String, CodingKey {
            case id
            case startClosedTS = "start_closed_ts"
            case openPeakTS = "open_peak_ts"
            case endClosedTS = "end_closed_ts"
            case confidence
        }
    }

    struct FoodEntry: Decodable, Identifiable, Hashable {
        let id: String
        let foodEntryTS: Double
        let confidence: Double

        enum CodingKeys: String, CodingKey {
            case id
            case foodEntryTS = "food_entry_ts"
            case confidence
        }
    }

    enum CodingKeys: String, CodingKey {
        case decodedSchemaVersion = "schema_version"
        case decodedArtifactType = "artifact_type"
        case sessionID = "session_id"
        case candidateRevision = "candidate_revision"
        case generatedAt = "generated_at"
        case modelVersions = "model_versions"
        case coarseLabelState = "coarse_label_state"
        case requiresFullCoarseReview = "requires_full_coarse_review"
        case chews
        case foodEntries = "food_entries"
        case contentSHA256 = "content_sha256"
        case fps
        case durationS = "duration_s"
    }

    func validateContract(expectedSessionID: String) throws {
        guard decodedSchemaVersion == Self.schemaVersion else {
            throw CandidateContractError.unsupportedSchema(decodedSchemaVersion)
        }
        guard decodedArtifactType == Self.artifactType else {
            throw CandidateContractError.invalidArtifactType(decodedArtifactType)
        }
        guard sessionID == expectedSessionID else {
            throw CandidateContractError.sessionMismatch(expected: expectedSessionID, actual: sessionID)
        }
        guard candidateRevision > 0 else {
            throw CandidateContractError.invalidRevision(candidateRevision)
        }
        let allowedCoarseStates = ["reviewed_intervals", "all_negative", "missing"]
        guard allowedCoarseStates.contains(coarseLabelState) else {
            throw CandidateContractError.invalidCoarseLabelState(coarseLabelState)
        }
        if coarseLabelState == "missing", !requiresFullCoarseReview {
            throw CandidateContractError.missingCoarseReviewFlag
        }
        if coarseLabelState == "all_negative", !chews.isEmpty || !foodEntries.isEmpty {
            throw CandidateContractError.allNegativeContainsCandidates
        }

        var chewIDs = Set<String>()
        var previousEnd = -Double.infinity
        for chew in chews {
            guard chewIDs.insert(chew.id).inserted else {
                throw CandidateContractError.duplicateID(chew.id)
            }
            guard chew.startClosedTS < chew.openPeakTS,
                  chew.openPeakTS < chew.endClosedTS else {
                throw CandidateContractError.invalidChewAnchors(chew.id)
            }
            guard chew.startClosedTS.isFinite,
                  chew.openPeakTS.isFinite,
                  chew.endClosedTS.isFinite,
                  chew.confidence.isFinite,
                  (0...1).contains(chew.confidence) else {
                throw CandidateContractError.invalidChewAnchors(chew.id)
            }
            // Equality is intentional: adjacent cycles can share one closed
            // boundary. Only a true overlap is invalid.
            guard chew.startClosedTS >= previousEnd else {
                throw CandidateContractError.overlap(chew.id)
            }
            previousEnd = chew.endClosedTS
        }

        var entryIDs = Set<String>()
        var previousEntry = -Double.infinity
        for entry in foodEntries {
            guard entryIDs.insert(entry.id).inserted else {
                throw CandidateContractError.duplicateID(entry.id)
            }
            guard entry.foodEntryTS > previousEntry else {
                throw CandidateContractError.unorderedFoodEntry(entry.id)
            }
            guard entry.foodEntryTS.isFinite,
                  entry.confidence.isFinite,
                  (0...1).contains(entry.confidence) else {
                throw CandidateContractError.unorderedFoodEntry(entry.id)
            }
            previousEntry = entry.foodEntryTS
        }
    }
}

enum CandidateContractError: LocalizedError, Equatable {
    case unsupportedSchema(String)
    case invalidArtifactType(String)
    case sessionMismatch(expected: String, actual: String)
    case invalidRevision(Int)
    case invalidCoarseLabelState(String)
    case missingCoarseReviewFlag
    case allNegativeContainsCandidates
    case missingContentHash
    case contentHashMismatch(expected: String, actual: String)
    case duplicateID(String)
    case invalidChewAnchors(String)
    case overlap(String)
    case unorderedFoodEntry(String)

    var errorDescription: String? {
        switch self {
        case .unsupportedSchema(let version):
            "Candidate schema \(version) is not supported. Run the explicit v1 migration and re-review."
        case .invalidArtifactType(let value):
            "Expected a vision_candidates artifact, found \(value)."
        case .sessionMismatch(let expected, let actual):
            "Candidate session mismatch: expected \(expected), found \(actual)."
        case .invalidRevision(let value):
            "Candidate revision must be positive; found \(value)."
        case .invalidCoarseLabelState(let value):
            "Unknown coarse-label state: \(value)."
        case .missingCoarseReviewFlag:
            "Missing coarse labels must require a full coarse review."
        case .allNegativeContainsCandidates:
            "An explicitly all-negative session must contain zero automatic candidates."
        case .missingContentHash:
            "Candidate content_sha256 is missing. Rerun vision to create an immutable v2 artifact."
        case .contentHashMismatch(let expected, let actual):
            "Candidate hash is invalid (declared \(expected), computed \(actual)). Rerun vision; saving is blocked."
        case .duplicateID(let id):
            "Duplicate candidate ID \(id). Rerun vision before reviewing."
        case .invalidChewAnchors(let id):
            "\(id) must satisfy Closed start < Maximum open < Closed end."
        case .overlap(let id):
            "\(id) overlaps the preceding chew. Review will not trim or drop it; fix candidate generation."
        case .unorderedFoodEntry(let id):
            "Food-entry candidate \(id) is not time-ordered."
        }
    }
}

/// Python-compatible compact, key-sorted JSON hashing used by the canonical
/// v2 contract. `content_sha256` is excluded before hashing.
enum CandidateContentHasher {
    static func hash(data: Data) throws -> String {
        guard var object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw CandidateContractError.missingContentHash
        }
        object.removeValue(forKey: "content_sha256")
        let canonical = try CanonicalJSON.data(object)
        return SHA256.hash(data: canonical).map { String(format: "%02x", $0) }.joined()
    }

    static func loadAndValidate(from url: URL, expectedSessionID: String) throws -> (CandidateBundle, String) {
        let data = try Data(contentsOf: url)
        let bundle = try JSONDecoder().decode(CandidateBundle.self, from: data)
        try bundle.validateContract(expectedSessionID: expectedSessionID)
        let declared = bundle.contentSHA256.lowercased()
        guard declared.count == 64,
              declared.allSatisfy({ $0.isHexDigit }) else {
            throw CandidateContractError.missingContentHash
        }
        let computed = try hash(data: data)
        guard declared == computed else {
            throw CandidateContractError.contentHashMismatch(expected: declared, actual: computed)
        }
        return (bundle, computed)
    }
}

private enum CanonicalJSON {
    static func data(_ value: Any) throws -> Data {
        Data(try string(value).utf8)
    }

    private static func string(_ value: Any) throws -> String {
        if value is NSNull { return "null" }
        if let value = value as? String { return try escaped(value) }
        if let value = value as? [Any] {
            return "[" + (try value.map(string)).joined(separator: ",") + "]"
        }
        if let value = value as? [String: Any] {
            let members = try value.keys.sorted().map { key in
                try escaped(key) + ":" + string(value[key] as Any)
            }
            return "{" + members.joined(separator: ",") + "}"
        }
        if let number = value as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                return number.boolValue ? "true" : "false"
            }
            let type = String(cString: number.objCType)
            if ["c", "s", "i", "l", "q", "C", "S", "I", "L", "Q"].contains(type) {
                return number.stringValue
            }
            let double = number.doubleValue
            guard double.isFinite else {
                throw NSError(domain: "CanonicalJSON", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "Non-finite JSON number"])
            }
            // Swift preserves the explicit decimal for integral doubles, as
            // Python's json.dumps does (1.0 rather than 1).
            return String(double)
        }
        throw NSError(domain: "CanonicalJSON", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Unsupported JSON value"])
    }

    private static func escaped(_ value: String) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: [value], options: [.withoutEscapingSlashes])
        let encoded = String(decoding: data, as: UTF8.self)
        return String(encoded.dropFirst().dropLast())
    }
}

struct ReviewFoodEntry: Identifiable, Equatable {
    enum Source: Equatable { case candidate, added, edited }

    var id: String
    var ts: Double
    var confidence: Double
    var source: Source
}
