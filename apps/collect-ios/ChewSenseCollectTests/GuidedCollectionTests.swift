import Foundation
import Testing
@testable import ChewSenseCollect

/// Guided experiment parsing, timing semantics, interruption policies,
/// validation and the sidecar contract shared with the Mac.
struct GuidedCollectionTests {
    @Test func staleHeadphoneStreamRetriesWithoutACapabilityChange() {
        var health = HeadphoneMotionHealth()
        #expect(health.shouldRestart(at: 100))
        health.didRestart(at: 100)
        #expect(!health.isFresh(at: 100.1))
        let acceptedSample1 = health.didReceive(sampleTime: 100.1, at: 100.12)
        #expect(acceptedSample1)
        #expect(health.isFresh(at: 100.4))
        #expect(!health.isFresh(at: 101))
        #expect(!health.shouldRestart(at: 101))
        #expect(health.shouldRestart(at: 102.5))
        health.didRestart(at: 102.5)
        #expect(!health.shouldRestart(at: 104.9))
        #expect(health.shouldRestart(at: 105))
        health.didRestart(at: 105)
        let acceptedSample2 = health.didReceive(sampleTime: 105.1, at: 105.12)
        #expect(acceptedSample2)
        #expect(health.isFresh(at: 105.4))
        #expect(!health.shouldRestart(at: 105.4))
    }

    @Test func cachedOrInvalidMotionCannotMakeReconnectionReady() {
        var health = HeadphoneMotionHealth()
        health.didRestart(at: 200)
        let acceptedSample3 = health.didReceive(sampleTime: 190, at: 200.1)
        #expect(!acceptedSample3)
        let acceptedSample4 = health.didReceive(sampleTime: .nan, at: 200.1)
        #expect(!acceptedSample4)
        let acceptedSample5 = health.didReceive(sampleTime: 205, at: 200.1)
        #expect(!acceptedSample5)
        #expect(!health.isFresh(at: 200.2))
        let acceptedSample6 = health.didReceive(sampleTime: 200.2, at: 200.21)
        #expect(acceptedSample6)
        let acceptedSample7 = health.didReceive(sampleTime: 200.2, at: 200.22)
        #expect(!acceptedSample7)
        health.invalidateSamples()
        #expect(!health.isFresh(at: 200.23))
    }

    @Test func restoredMotionResumesGuidedTimerAndRetainsInvalidInterval() throws {
        let engine = GuidedRunEngine(definition: try entry("quiet-sitting").definition)
        _ = run(engine) { elapsed in
            var quality = QualitySnapshot.good
            if elapsed > 20 && elapsed < 28 { quality.motionFresh = false }
            return quality
        }
        #expect(engine.phase == .finished)
        #expect(engine.events.contains { $0.type == "activity_paused" })
        #expect(engine.events.contains { $0.type == "activity_resumed" })
        let payload = try engine.sidecar(sessionID: "reconnect-test", experimentSHA256: "test",
            rawDefinition: try entry("quiet-sitting").rawDefinition,
            catalogVersion: 1, videoClockOrigin: 1000, motionCoverage: 0.9, repeatOf: nil)
        let object = try #require(try JSONSerialization.jsonObject(with: payload) as? [String: Any])
        #expect((object["invalid_intervals"] as? [[String: Any]])?.isEmpty == false)
    }

    @Test func captureDimensionsMatchCardinalRotations() {
        for angle: CGFloat in [0, 180, 360] {
            let size = VideoIMURecorder.captureDimensions(rotationAngle: angle)
            #expect(size.width == 1920 && size.height == 1080)
        }
        for angle: CGFloat in [90, 270, -90] {
            let size = VideoIMURecorder.captureDimensions(rotationAngle: angle)
            #expect(size.width == 1080 && size.height == 1920)
        }
    }

    @Test func guidedUploadWaitsForCoarseReview() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        #expect(!UploadManager.needsCoarseReview(folder))
        try Data("{}".utf8).write(to: folder.appendingPathComponent("guided_session.json"))
        #expect(UploadManager.needsCoarseReview(folder))
        try FileManager.default.createDirectory(at: folder.appendingPathComponent("annotations"), withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: folder.appendingPathComponent("annotations/coarse_labels.json"))
        #expect(!UploadManager.needsCoarseReview(folder))
    }

    @Test func pipelineStatusDecodesMacContract() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let data = Data(#"{"review_tasks":2,"attention_count":0,"training_ready":false,"research_targets_met":false,"blockers":["0 verified sessions with chewing; need 20"],"next_action":"Complete the pending reviews."}"#.utf8)
        let status = try decoder.decode(MacConnection.PipelineStatus.self, from: data)
        #expect(status.reviewTasks == 2)
        #expect(!status.trainingReady)
        #expect(status.blockers.count == 1)
    }

    @Test func pipelineStatusDecodesEvidenceAndRecommendedRecording() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let data = Data(#"{"review_tasks":2,"attention_count":0,"training_ready":false,"research_targets_met":false,"blockers":[],"next_action":"Inspect timing on the Mac","goals":[{"id":"positive","title":"Verified chewing recordings","current":0,"target":20}],"stages":[{"id":"timing","title":"Check timing","state":"needs_action","detail":"No human confirmation"}],"actions":[{"id":"timing","kind":"review","destination":"mac","title":"Inspect timing","detail":"Compare existing nods","session_id":"diagnostic-1"}],"received_sessions":3,"complete_sessions":2,"incomplete_sessions":1,"available_review_tasks":2,"next_experiment_id":"baseline-90s"}"#.utf8)
        let status = try decoder.decode(MacConnection.PipelineStatus.self, from: data)
        #expect(status.goals?.first?.target == 20)
        #expect(status.stages?.first?.state == "needs_action")
        #expect(status.actions?.first?.sessionId == "diagnostic-1")
        #expect(status.actions?.first?.experimentId == nil)
        #expect(status.receivedSessions == 3)
        #expect(status.completeSessions == 2)
        #expect(status.incompleteSessions == 1)
        #expect(status.nextExperimentId == "baseline-90s")
    }

    private static let repo = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent()
    private static let seedURL = repo.appendingPathComponent(
        "apps/collect-ios/ChewSenseCollect/Resources/SeedExperimentCatalog.json")

    /// Rows in the bundled catalog, counted without the parser under test.
    private static var seedExperimentCount: Int {
        let root = (try? JSONSerialization.jsonObject(with: Data(contentsOf: seedURL))) as? [String: Any]
        return (root?["experiments"] as? [Any])?.count ?? -1
    }

    private func catalog() throws -> ExperimentCatalog {
        try ExperimentCatalog.parse(Data(contentsOf: Self.seedURL))
    }

    private func entry(_ id: String) throws -> CatalogEntry {
        try #require(try catalog().entries.first { $0.definition.experimentID == id })
    }

    /// Runs the engine at 10 Hz with a quality function of time since start.
    private func run(_ engine: GuidedRunEngine, start: Double = 1000, limit: Double = 600,
                     quality: (Double) -> QualitySnapshot = { _ in .good }) -> Double {
        engine.start(at: start)
        var t = start
        while engine.phase != .finished && t - start < limit {
            t += 0.1
            engine.tick(at: t, quality: quality(t - start))
        }
        return t
    }

    @Test func seedCatalogParsesAllExperiments() throws {
        let parsed = try catalog()
        #expect(Self.seedExperimentCount >= 12)
        #expect(parsed.entries.count == Self.seedExperimentCount)
        let baseline = try entry("baseline-90s").definition
        #expect(baseline.activities.map(\.durationS) == [5, 15, 20, 10, 5, 15, 10, 5, 5])
        #expect(baseline.totalSeconds == 90)
        #expect(baseline.required)
        #expect(try entry("walking-no-food").definition.safetyNotes.isEmpty == false)
    }

    @Test func unknownFieldsAreIgnoredButBadSchemasRejected() throws {
        var root = try #require(try JSONSerialization.jsonObject(with: Data(contentsOf: Self.seedURL)) as? [String: Any])
        var rows = try #require(root["experiments"] as? [[String: Any]])
        var definition = try #require(rows[0]["definition"] as? [String: Any])
        definition["future_field"] = ["anything": 1]
        rows[0]["definition"] = definition
        root["experiments"] = rows
        let parsed = try ExperimentCatalog.parse(JSONSerialization.data(withJSONObject: root))
        #expect(parsed.entries.count == Self.seedExperimentCount)
        // The verbatim definition, including unknown fields, travels with the recording.
        let raw = try #require(try JSONSerialization.jsonObject(with: parsed.entries[0].rawDefinition) as? [String: Any])
        #expect(raw["future_field"] != nil)
        root["schema_version"] = "guided-catalog/99"
        #expect(throws: ExperimentCatalog.ParseError.self) {
            try ExperimentCatalog.parse(JSONSerialization.data(withJSONObject: root))
        }
    }

    @Test func invalidCatalogNeverReplacesCache() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let store = ExperimentCatalogStore(directory: directory)
        try store.apply(Data(contentsOf: Self.seedURL))
        #expect(throws: (any Error).self) { try store.apply(Data("{\"schema_version\":1}".utf8)) }
        #expect(store.catalog.entries.count == Self.seedExperimentCount)
        let reopened = ExperimentCatalogStore(directory: directory)
        #expect(reopened.catalog.entries.count == Self.seedExperimentCount)
        #expect(reopened.source != .bundledSeed)
    }

    @Test func goodQualityFollowsScheduleWithActualTimestamps() throws {
        let definition = try entry("baseline-90s").definition
        let engine = GuidedRunEngine(definition: definition)
        let end = run(engine)
        #expect(engine.phase == .finished)
        #expect(abs((end - 1000) - (definition.leadInS + 90)) < 0.5)
        #expect(engine.attempts.count == 9)
        for attempt in engine.attempts {
            let started = try #require(attempt.started)
            let ended = try #require(attempt.ended)
            #expect(abs(ended - started - definition.activities[attempt.activityIndex].durationS) < 0.15)
            #expect(attempt.outcome == "completed")
        }
        let types = engine.events.map(\.type)
        #expect(types.first == "user_acknowledged")
        #expect(types.filter { $0 == "instruction_presented" }.count == 9)
        #expect(types.last == "experiment_completed")
        #expect(engine.validate(motionCoverage: 0.99).complete)
    }

    @Test func faceLossPausesTimerAndExtendsActualDuration() throws {
        let definition = try entry("baseline-90s").definition
        let engine = GuidedRunEngine(definition: definition)
        // Face missing for 4 s during the first chewing activity (starts ~23 s).
        _ = run(engine) { t in
            var q = QualitySnapshot.good
            if t > 30 && t < 34 { q.faceVisible = false }
            return q
        }
        let chew = try #require(engine.attempts.first { $0.activityID == "chew-1" })
        #expect(chew.outcome == "completed")
        #expect(chew.pausedSeconds > 3)
        #expect(abs(chew.validSeconds - 20) < 0.2)
        let actual = try #require(chew.ended) - (try #require(chew.started))
        #expect(actual > 23.5)
        #expect(engine.invalidIntervals.contains { $0.reasons.contains("face_missing") })
        #expect(engine.events.contains { $0.type == "activity_paused" })
        #expect(engine.events.contains { $0.type == "activity_resumed" })
        let times = engine.events.map(\.hostTime)
        #expect(times == times.sorted())
    }

    @Test func repeatPolicyRestartsSyncMarker() throws {
        let definition = try entry("baseline-90s").definition
        let engine = GuidedRunEngine(definition: definition)
        _ = run(engine) { t in
            var q = QualitySnapshot.good
            if t > 4.5 && t < 6.5 { q.motionFresh = false }  // during the first nod
            return q
        }
        let nods = engine.attempts.filter { $0.activityID == "nod-start" }
        #expect(nods.count == 2)
        #expect(nods[0].outcome == "interrupted")
        #expect(nods[1].outcome == "completed")
        #expect(engine.events.contains { $0.type == "activity_restarted" })
        #expect(engine.validate(motionCoverage: 0.95).complete)
    }

    @Test func markPolicyContinuesWithUncertainty() throws {
        let definition = try entry("walking-no-food").definition
        let engine = GuidedRunEngine(definition: definition)
        _ = run(engine) { t in
            var q = QualitySnapshot.good
            if t > 30 && t < 40 { q.motionFresh = false }  // during "Walk normally"
            return q
        }
        let walk = try #require(engine.attempts.first { $0.activityID == "walk-1" })
        #expect(walk.outcome == "completed_with_uncertainty")
        #expect(walk.pausedSeconds == 0)
        #expect(engine.events.contains { $0.type == "activity_marked_uncertain" })
        let validation = engine.validate(motionCoverage: 0.85)
        #expect(!validation.complete)
        #expect(validation.repeatActivityIDs.contains("walk-1"))
    }

    @Test func abandonedRunReportsAffectedSegmentsAndRepeatBracketsMarkers() throws {
        let definition = try entry("baseline-90s").definition
        let engine = GuidedRunEngine(definition: definition)
        engine.start(at: 0)
        var t = 0.0
        while t < 40 { t += 0.1; engine.tick(at: t, quality: .good) }
        engine.abandon(at: t)
        let validation = engine.validate(motionCoverage: 1)
        #expect(!validation.complete)
        #expect(validation.repeatActivityIDs.contains("chew-2"))
        #expect(engine.attempts.last?.outcome == "abandoned")
        let repeatRun = GuidedRunEngine(definition: definition, onlyActivityIDs: ["chew-2", "talk-1"])
        #expect(repeatRun.plan.map { definition.activities[$0].id } == ["nod-start", "chew-2", "talk-1", "nod-end"])
    }

    @Test func noTimerProgressWithoutRequiredEvidence() throws {
        let definition = try entry("baseline-90s").definition
        let engine = GuidedRunEngine(definition: definition)
        _ = run(engine, limit: 30) { _ in
            var q = QualitySnapshot.good
            q.faceVisible = false
            return q
        }
        #expect(engine.phase == .waitingForEvidence(index: 0))
        #expect(engine.attempts.first?.started == nil)
        #expect(engine.isTimerPaused)
    }

    @Test func sidecarMatchesMacContract() throws {
        let entry = try entry("baseline-90s")
        let engine = GuidedRunEngine(definition: entry.definition)
        _ = run(engine) { t in
            var q = QualitySnapshot.good
            if t > 30 && t < 32 { q.brightness = 0.02 }
            return q
        }
        let data = try engine.sidecar(sessionID: "Guided-baseline-90s-test", experimentSHA256: entry.definitionSHA256,
                                      rawDefinition: entry.rawDefinition, catalogVersion: 0,
                                      videoClockOrigin: 999.5, motionCoverage: 0.97, repeatOf: nil)
        let object = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        for key in ["schema_version", "session_id", "experiment_id", "experiment_version", "definition_sha256",
                    "definition", "video_clock_origin_host_time_s", "events", "attempts", "invalid_intervals",
                    "quality", "validation", "labels_status"] {
            #expect(object[key] != nil, "missing \(key)")
        }
        #expect(object["labels_status"] as? String == "intended_only_not_verified")
        // Hand the file to the Python contract test (pipelines/vision/tests/test_loop.py).
        let out = Self.repo.appendingPathComponent("build/contract/guided_session.swift.json")
        try? FileManager.default.createDirectory(at: out.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: out)
    }
}
