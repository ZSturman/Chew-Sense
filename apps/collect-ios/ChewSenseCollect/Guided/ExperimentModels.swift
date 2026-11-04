import Foundation

/// Mirrors `guided-experiment/1` (pipelines/vision/chewsense/loop/experiments.py).
/// Decoding ignores unknown keys so a newer Mac can add fields; the raw JSON is
/// kept separately and written verbatim into each recording's sidecar.
nonisolated struct ExperimentDefinition: Codable, Hashable, Identifiable, Sendable {
    struct Food: Codable, Hashable, Sendable {
        var kind: String
        var recommended: String?
        var category: String?

        var needsFoodOrDrink: Bool { kind != "none" }
    }

    struct AirPods: Codable, Hashable, Sendable {
        var required: Bool
        var configuration: String
    }

    struct Camera: Codable, Hashable, Sendable {
        var position: String
        var faceRequired: Bool
        var jawRequired: Bool
        var minBrightness: Double
        var maxPhoneMotionG: Double

        enum CodingKeys: String, CodingKey {
            case position
            case faceRequired = "face_required"
            case jawRequired = "jaw_required"
            case minBrightness = "min_brightness"
            case maxPhoneMotionG = "max_phone_motion_g"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            position = try c.decode(String.self, forKey: .position)
            faceRequired = try c.decodeIfPresent(Bool.self, forKey: .faceRequired) ?? true
            jawRequired = try c.decodeIfPresent(Bool.self, forKey: .jawRequired) ?? true
            minBrightness = try c.decodeIfPresent(Double.self, forKey: .minBrightness) ?? 0.15
            maxPhoneMotionG = try c.decodeIfPresent(Double.self, forKey: .maxPhoneMotionG) ?? 0.25
        }
    }

    enum QualityPolicy: String, Codable, Sendable {
        case pause, mark, repeatActivity = "repeat"
    }

    struct Activity: Codable, Hashable, Identifiable, Sendable {
        var id: String
        var kind: String
        var instruction: String
        var detail: String?
        var durationS: Double
        var expectedBehavior: String
        var intendedLabel: String
        var requiresFace: Bool
        var requiresMotion: Bool
        var onQualityLoss: QualityPolicy
        var minValidFraction: Double
        var syncMarker: Bool
        var repeatable: Bool

        enum CodingKeys: String, CodingKey {
            case id, kind, instruction, detail, repeatable
            case durationS = "duration_s"
            case expectedBehavior = "expected_behavior"
            case intendedLabel = "intended_label"
            case requiresFace = "requires_face"
            case requiresMotion = "requires_motion"
            case onQualityLoss = "on_quality_loss"
            case minValidFraction = "min_valid_fraction"
            case syncMarker = "sync_marker"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = try c.decode(String.self, forKey: .id)
            kind = try c.decode(String.self, forKey: .kind)
            instruction = try c.decode(String.self, forKey: .instruction)
            detail = try c.decodeIfPresent(String.self, forKey: .detail)
            durationS = try c.decode(Double.self, forKey: .durationS)
            expectedBehavior = try c.decode(String.self, forKey: .expectedBehavior)
            intendedLabel = try c.decode(String.self, forKey: .intendedLabel)
            requiresFace = try c.decodeIfPresent(Bool.self, forKey: .requiresFace) ?? true
            requiresMotion = try c.decodeIfPresent(Bool.self, forKey: .requiresMotion) ?? true
            onQualityLoss = try c.decodeIfPresent(QualityPolicy.self, forKey: .onQualityLoss) ?? .pause
            minValidFraction = try c.decodeIfPresent(Double.self, forKey: .minValidFraction) ?? 0.8
            syncMarker = try c.decodeIfPresent(Bool.self, forKey: .syncMarker) ?? false
            repeatable = try c.decodeIfPresent(Bool.self, forKey: .repeatable) ?? true
        }
    }

    struct Completion: Codable, Hashable, Sendable {
        var minActivityValidFraction: Double
        var minMotionCoverage: Double
        var minFaceVisibleFraction: Double
        var requiredSyncMarkers: Int

        enum CodingKeys: String, CodingKey {
            case minActivityValidFraction = "min_activity_valid_fraction"
            case minMotionCoverage = "min_motion_coverage"
            case minFaceVisibleFraction = "min_face_visible_fraction"
            case requiredSyncMarkers = "required_sync_markers"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            minActivityValidFraction = try c.decodeIfPresent(Double.self, forKey: .minActivityValidFraction) ?? 0.8
            minMotionCoverage = try c.decodeIfPresent(Double.self, forKey: .minMotionCoverage) ?? 0.9
            minFaceVisibleFraction = try c.decodeIfPresent(Double.self, forKey: .minFaceVisibleFraction) ?? 0.8
            requiredSyncMarkers = try c.decodeIfPresent(Int.self, forKey: .requiredSyncMarkers) ?? 2
        }

        init(minActivityValidFraction: Double = 0.8, minMotionCoverage: Double = 0.9,
             minFaceVisibleFraction: Double = 0.8, requiredSyncMarkers: Int = 2) {
            self.minActivityValidFraction = minActivityValidFraction
            self.minMotionCoverage = minMotionCoverage
            self.minFaceVisibleFraction = minFaceVisibleFraction
            self.requiredSyncMarkers = requiredSyncMarkers
        }
    }

    struct Labeling: Codable, Hashable, Sendable {
        var wearerNoFoodAttestation: Bool

        enum CodingKeys: String, CodingKey { case wearerNoFoodAttestation = "wearer_no_food_attestation" }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            wearerNoFoodAttestation = try c.decodeIfPresent(Bool.self, forKey: .wearerNoFoodAttestation) ?? false
        }

        init(wearerNoFoodAttestation: Bool = false) { self.wearerNoFoodAttestation = wearerNoFoodAttestation }
    }

    struct Request: Codable, Hashable, Sendable {
        var source: String
        var summary: String
    }

    var experimentID: String
    var version: Int
    var title: String
    var summary: String
    var purpose: String
    var request: Request
    var priority: String
    var required: Bool
    var targetRecordings: Int
    var estimatedMinutes: Double?
    var preparation: [String]
    var equipment: [String]
    var food: Food
    var airpods: AirPods
    var camera: Camera
    var safetyNotes: [String]
    var leadInS: Double
    var activities: [Activity]
    var completion: Completion
    var labeling: Labeling

    var id: String { "\(experimentID)@\(version)" }
    var totalSeconds: Double { activities.reduce(0) { $0 + $1.durationS } }

    enum CodingKeys: String, CodingKey {
        case version, title, summary, purpose, request, priority, required, preparation, equipment
        case food, airpods, camera, activities, completion, labeling
        case experimentID = "experiment_id"
        case targetRecordings = "target_recordings"
        case estimatedMinutes = "estimated_minutes"
        case safetyNotes = "safety_notes"
        case leadInS = "lead_in_s"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        experimentID = try c.decode(String.self, forKey: .experimentID)
        version = try c.decode(Int.self, forKey: .version)
        title = try c.decode(String.self, forKey: .title)
        summary = try c.decode(String.self, forKey: .summary)
        purpose = try c.decode(String.self, forKey: .purpose)
        request = try c.decode(Request.self, forKey: .request)
        priority = try c.decodeIfPresent(String.self, forKey: .priority) ?? "medium"
        required = try c.decodeIfPresent(Bool.self, forKey: .required) ?? false
        targetRecordings = try c.decode(Int.self, forKey: .targetRecordings)
        estimatedMinutes = try c.decodeIfPresent(Double.self, forKey: .estimatedMinutes)
        preparation = try c.decode([String].self, forKey: .preparation)
        equipment = try c.decodeIfPresent([String].self, forKey: .equipment) ?? []
        food = try c.decodeIfPresent(Food.self, forKey: .food) ?? Food(kind: "none")
        airpods = try c.decodeIfPresent(AirPods.self, forKey: .airpods)
            ?? AirPods(required: true, configuration: "both_in_ear")
        camera = try c.decode(Camera.self, forKey: .camera)
        safetyNotes = try c.decodeIfPresent([String].self, forKey: .safetyNotes) ?? []
        leadInS = try c.decodeIfPresent(Double.self, forKey: .leadInS) ?? 3
        activities = try c.decode([Activity].self, forKey: .activities)
        completion = try c.decodeIfPresent(Completion.self, forKey: .completion) ?? Completion()
        labeling = try c.decodeIfPresent(Labeling.self, forKey: .labeling) ?? Labeling()
        guard !activities.isEmpty, activities.allSatisfy({ $0.durationS > 0 && $0.durationS.isFinite }) else {
            throw DecodingError.dataCorruptedError(forKey: .activities, in: c,
                                                   debugDescription: "activities must have positive durations")
        }
    }
}

/// One catalog row. `rawDefinition` is the exact JSON object received from the Mac.
nonisolated struct CatalogEntry: Identifiable, Hashable, Sendable {
    struct Progress: Hashable, Sendable {
        var target: Int
        var completedValid: Int
        var remaining: Int
    }

    struct RequestReason: Hashable, Sendable {
        var summary: String
        var priority: String
    }

    var definition: ExperimentDefinition
    var definitionSHA256: String
    var rawDefinition: Data
    var progress: Progress
    var effectivePriority: String
    var requests: [RequestReason]

    var id: String { definition.id }

    /// Explains why the Mac is asking for this recording.
    var whyNeeded: String {
        requests.first?.summary ?? definition.request.summary
    }
}

nonisolated struct ExperimentCatalog: Sendable {
    var catalogVersion: Int
    var generatedAt: String
    var entries: [CatalogEntry]

    static let schema = "guided-catalog/1"

    enum ParseError: LocalizedError {
        case schema(String)
        case malformed(String)

        var errorDescription: String? {
            switch self {
            case .schema(let s): "Unsupported experiment catalog schema \(s); update Collect."
            case .malformed(let s): "The experiment catalog is invalid: \(s)"
            }
        }
    }

    /// Parses the whole payload or nothing, so a bad update never replaces a good cache.
    static func parse(_ data: Data) throws -> ExperimentCatalog {
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ParseError.malformed("not an object")
        }
        guard let schema = root["schema_version"] as? String, schema == Self.schema else {
            throw ParseError.schema(root["schema_version"] as? String ?? "missing")
        }
        guard let version = root["catalog_version"] as? Int,
              let rows = root["experiments"] as? [[String: Any]] else {
            throw ParseError.malformed("missing catalog_version or experiments")
        }
        var entries: [CatalogEntry] = []
        for row in rows {
            guard let raw = row["definition"] as? [String: Any],
                  let digest = row["definition_sha256"] as? String,
                  let progress = row["progress"] as? [String: Any] else {
                throw ParseError.malformed("incomplete experiment entry")
            }
            let rawData = try JSONSerialization.data(withJSONObject: raw, options: [.sortedKeys])
            let definition = try JSONDecoder().decode(ExperimentDefinition.self, from: rawData)
            let requests = (row["requests"] as? [[String: Any]] ?? []).compactMap { item -> CatalogEntry.RequestReason? in
                guard let summary = item["summary"] as? String else { return nil }
                return .init(summary: summary, priority: item["priority"] as? String ?? definition.priority)
            }
            entries.append(CatalogEntry(
                definition: definition,
                definitionSHA256: digest,
                rawDefinition: rawData,
                progress: .init(target: progress["target"] as? Int ?? definition.targetRecordings,
                                completedValid: progress["completed_valid"] as? Int ?? 0,
                                remaining: progress["remaining"] as? Int ?? definition.targetRecordings),
                effectivePriority: row["effective_priority"] as? String ?? definition.priority,
                requests: requests
            ))
        }
        return ExperimentCatalog(catalogVersion: version,
                                 generatedAt: root["generated_at"] as? String ?? "",
                                 entries: entries)
    }
}
