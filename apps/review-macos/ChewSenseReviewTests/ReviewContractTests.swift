import AVFoundation
import Foundation
import XCTest
@testable import ChewSenseReview

final class ReviewContractTests: XCTestCase {
    private var savedWorkspacePreference: Any?

    override func setUp() {
        super.setUp()
        savedWorkspacePreference = UserDefaults.standard.object(forKey: "ChewSenseReview.workspaceRoot")
    }

    override func tearDown() {
        if let savedWorkspacePreference {
            UserDefaults.standard.set(savedWorkspacePreference, forKey: "ChewSenseReview.workspaceRoot")
        } else {
            UserDefaults.standard.removeObject(forKey: "ChewSenseReview.workspaceRoot")
        }
        super.tearDown()
    }

    @MainActor
    func testFrameStepsFollowSourceFramesAcrossPausedSelectionBoundary() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appending(path: "25-fps.mov")
        let writer = try AVAssetWriter(url: url, fileType: .mov)
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: 32, AVVideoHeightKey: 32,
        ])
        let pixels = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: 32, kCVPixelBufferHeightKey as String: 32,
        ])
        writer.add(input)
        XCTAssertTrue(writer.startWriting())
        writer.startSession(atSourceTime: .zero)
        let pool = try XCTUnwrap(pixels.pixelBufferPool)
        for frame in 0..<30 {
            try await waitForPlayback { input.isReadyForMoreMediaData }
            var buffer: CVPixelBuffer?
            XCTAssertEqual(CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer), kCVReturnSuccess)
            XCTAssertTrue(pixels.append(try XCTUnwrap(buffer), withPresentationTime: CMTime(value: Int64(frame), timescale: 25)))
        }
        input.markAsFinished()
        await writer.finishWriting()
        XCTAssertEqual(writer.status, .completed)

        let playback = PlaybackController()
        playback.load(url: url)
        let layer = AVPlayerLayer(player: playback.player)
        defer { layer.player = nil }
        try await waitForPlayback("media item ready") { playback.player.currentItem?.canStepForward == true }
        playback.setLoop(start: 0.1, end: 0.42, enabled: true)
        playback.seek(to: 0.4)
        playback.stepFrames(by: 1)
        try await waitForPlayback("queued next frame, \(playback.currentTime)") { abs(playback.currentTime - 0.44) < 0.001 }
        playback.stepFrames(by: 1)
        try await waitForPlayback("next frame, \(playback.currentTime)") { abs(playback.currentTime - 0.48) < 0.001 }
        playback.stepFrames(by: -1)
        try await waitForPlayback("previous frame, \(playback.currentTime)") { abs(playback.currentTime - 0.44) < 0.001 }
        XCTAssertFalse(playback.isPlaying)
    }

    @MainActor
    private func waitForPlayback(_ description: @autoclosure () -> String = "encoder ready", _ condition: () -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(3)
        while !condition(), ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(10))
        }
        _ = try XCTUnwrap(condition() ? true : nil, "Timed out waiting for \(description())")
    }

    func testSectionNavigationKeepsPhoneTruthSeparateFromActualInstructionTimes() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        try FileManager.default.createDirectory(at: directory.appendingPathComponent("annotations"), withIntermediateDirectories: true)
        let coarse = Data(#"{"intervals":[{"start_ts":20,"end_ts":40,"label":"chewing"}]}"#.utf8)
        try coarse.write(to: directory.appendingPathComponent("annotations/coarse_labels.json"))
        let guided = Data(#"{"video_clock_origin_host_time_s":100,"definition":{"activities":[{"id":"chew","kind":"chew","instruction":"Chew normally","duration_s":20}]},"attempts":[{"activity_id":"chew","outcome":"completed","started_host_s":125,"ended_host_s":148}]}"#.utf8)
        try guided.write(to: directory.appendingPathComponent("guided_session.json"))
        let assisted = ReviewSection.load(from: directory, includeInstructions: true)
        XCTAssertEqual(assisted.count, 2)
        XCTAssertEqual(assisted[0].source, .phone)
        XCTAssertEqual(assisted[0].start, 20)
        XCTAssertEqual(assisted[1].source, .instruction)
        XCTAssertEqual(assisted[1].start, 25)
        XCTAssertEqual(assisted[1].end, 48)
        let blind = ReviewSection.load(from: directory, includeInstructions: false)
        XCTAssertEqual(blind.count, 1)
        XCTAssertEqual(blind[0].source, .phone)
    }

    @MainActor
    func testManualChewDeletionAndUndoPreserveOtherAnnotations() throws {
        let fixture = try makeSessionFixture(chews: [chew("original", 0.1, 0.2, 0.3)])
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let state = ReviewerState()
        state.setWorkspaceRoot(fixture.root)
        state.loadSession(fixture.session)
        XCTAssertFalse(state.timelineEditingEnabled)
        state.addChew(start: 0.6, end: 0.9, peak: 0.75)
        XCTAssertEqual(state.overrides.addedChews.count, 1)
        state.deleteSelectedOrAt(videoTime: 0.75)
        XCTAssertTrue(state.overrides.addedChews.isEmpty)
        XCTAssertEqual(state.chews.map(\.id), ["original"])
        XCTAssertTrue(state.overrides.chewDecisions.isEmpty)
        state.undo()
        XCTAssertEqual(state.overrides.addedChews.count, 1)
        state.redo()
        XCTAssertTrue(state.overrides.addedChews.isEmpty)
    }

    @MainActor
    func testFocusedAcceptanceNeverVerifiesEventsOutsideTheReviewedSection() throws {
        let fixture = try makeSessionFixture(chews: [chew("inside", 0.1, 0.2, 0.3), chew("outside", 0.6, 0.7, 0.8)])
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let annotations = fixture.session.rawDir.appendingPathComponent("annotations")
        try FileManager.default.createDirectory(at: annotations, withIntermediateDirectories: true)
        try Data(#"{"intervals":[{"start_ts":0,"end_ts":0.4,"label":"chewing"}]}"#.utf8)
            .write(to: annotations.appendingPathComponent("coarse_labels.json"))
        let state = ReviewerState()
        state.setWorkspaceRoot(fixture.root)
        state.loadSession(fixture.session)
        state.focusedSectionID = "phone-0"
        XCTAssertEqual(state.reviewQueueChews.map(\.id), ["inside"])
        XCTAssertEqual(state.selectNextChew()?.id, "inside")
        XCTAssertEqual(state.selectNextChew()?.id, "inside")
        state.acceptReviewedSectionProposals()
        XCTAssertTrue(state.isVerified(id: "inside"))
        XCTAssertFalse(state.isVerified(id: "outside"))
        XCTAssertFalse(state.canFinalizeReview)
        let decisions = state.overrides.chewDecisions
        state.showPhoneSections = false
        state.showInstructionSections = false
        XCTAssertEqual(state.overrides.chewDecisions, decisions)
        state.undo()
        XCTAssertFalse(state.isVerified(id: "inside"))
    }

    func testExperimentContextRoundTripAndBlindProposalGuard() throws {
        let (bundle, hash) = try loadArtifact(makeCandidateArtifact(chews: [chew("c", 0.1, 0.2, 0.3)]))
        var draft = ReviewDraft.empty(sessionID: "golden_session", candidateRevision: bundle.candidateRevision, candidateSHA256: hash)
        draft.reviewContext = .init(mode: "assisted", activeSeconds: 12.5, syncVerified: false,
            excludedSpans: [.init(startTS: 1.0, endTS: 2.0, disposition: "unobservable")])
        let data = try JSONEncoder().encode(draft)
        XCTAssertEqual(try JSONDecoder().decode(ReviewDraft.self, from: data), draft)
        XCTAssertNoThrow(try ReviewContractValidator.validate(draft, candidates: bundle, candidateHash: hash))
        draft.reviewContext?.mode = "blind"
        XCTAssertThrowsError(try ReviewContractValidator.validate(draft, candidates: bundle, candidateHash: hash))
    }

    func testNestedDetectorFloatsMatchPythonWithoutDecimalRounding() throws {
        let data = Data(#"{"nested": {"confidence": 0.022380195558071136, "gap": 0.033334000000000086, "negative_zero": -0.0, "tiny": 1e-06, "integer": 12}}"#.utf8)
        XCTAssertEqual(try CandidateContentHasher.hash(data: data), "51115945d8593e6ce7f1ea65bd2566d753b58c50f3f143e05f339b22d0103760")
    }

    @MainActor
    func testWorkspaceSwitchSavesDraftAndClearsPreviouslyVisiblePredictions() throws {
        let fixture = try makeSessionFixture(chews: [chew("c", 0.1, 0.2, 0.3)])
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let state = ReviewerState()
        state.setWorkspaceRoot(fixture.root)
        state.loadSession(fixture.session)
        XCTAssertEqual(state.chews.count, 1)
        state.markChewUncertain(id: "c")
        let next = fixture.root.appendingPathComponent("blind")
        state.setWorkspaceRoot(next)
        XCTAssertNil(state.selectedSession)
        XCTAssertTrue(state.chews.isEmpty)
        XCTAssertTrue(state.framePredictions.isEmpty)
        let saved = try XCTUnwrap(OverridePersistence.loadIfPresent(fixture.session.overridesURL))
        XCTAssertEqual(saved.chewDecisions.first?.disposition, .uncertain)
        XCTAssertFalse(saved.verified)
    }

    func testGuidanceNamesTheRequiredReviewHandoff() {
        XCTAssertEqual(ReviewGuidanceStage.workspace.title, "Open a review workspace")
        XCTAssertTrue(ReviewGuidanceStage.finalize.nextStep.contains("finalize-session"))
        XCTAssertEqual(
            ReviewGuidanceStage.decisions(remainingStatus: "2 candidates remain").doneWhen,
            "Done when 2 candidates remain."
        )
    }

    func testCanonicalMarkerSemantics() {
        XCTAssertEqual(ChewMarker.start.displayName, "Closed start")
        XCTAssertEqual(ChewMarker.peak.displayName, "Maximum open")
        XCTAssertEqual(ChewMarker.end.displayName, "Closed end")
    }

    func testAdjacentCyclesMayShareClosedBoundary() throws {
        let artifact = try makeCandidateArtifact(chews: [
            chew("ch_0001", 0.10, 0.20, 0.30),
            chew("ch_0002", 0.30, 0.42, 0.55),
        ])
        let (bundle, hash) = try loadArtifact(artifact)
        XCTAssertEqual(bundle.chews[0].endClosedTS, bundle.chews[1].startClosedTS)
        XCTAssertEqual(hash, bundle.contentSHA256)
        XCTAssertNoThrow(try bundle.validateContract(expectedSessionID: "golden_session"))
    }

    func testOverlapIsRejectedInsteadOfSilentlyRepaired() throws {
        let artifact = try makeCandidateArtifact(chews: [
            chew("ch_0001", 0.10, 0.20, 0.30),
            chew("ch_0002", 0.29, 0.42, 0.55),
        ])
        let url = try temporaryFile(named: "candidates-v0001.json", data: artifact)
        XCTAssertThrowsError(
            try CandidateContentHasher.loadAndValidate(from: url, expectedSessionID: "golden_session")
        ) { error in
            XCTAssertEqual(error as? CandidateContractError, .overlap("ch_0002"))
        }
    }

    func testFoodEntryTimestampsMustBeStrictlyOrdered() throws {
        let artifact = try makeCandidateArtifact(
            chews: [],
            foodEntries: [
                ["id": "fe_0001", "food_entry_ts": 0.5, "confidence": 0.9,
                 "evidence": [String: Any]()],
                ["id": "fe_0002", "food_entry_ts": 0.5, "confidence": 0.8,
                 "evidence": [String: Any]()],
            ]
        )
        let url = try temporaryFile(named: "candidates-v0001.json", data: artifact)
        XCTAssertThrowsError(
            try CandidateContentHasher.loadAndValidate(from: url, expectedSessionID: "golden_session")
        ) { error in
            XCTAssertEqual(error as? CandidateContractError, .unorderedFoodEntry("fe_0002"))
        }
    }

    func testDraftRoundTripPreservesEveryDisposition() throws {
        let decisions: [ReviewDraft.ChewDecision] = [
            .init(candidateID: "a", disposition: .accepted),
            .init(candidateID: "b", disposition: .edited,
                  startClosedTS: 1.0, openPeakTS: 1.2, endClosedTS: 1.4),
            .init(candidateID: "c", disposition: .rejected),
            .init(candidateID: "d", disposition: .unobservable),
            .init(candidateID: "e", disposition: .uncertain),
        ]
        let draft = ReviewDraft(
            sessionID: "s",
            candidateRevision: 7,
            candidateSHA256: String(repeating: "a", count: 64),
            chewDecisions: decisions,
            addedChews: [.init(
                id: "manual_chew_0000",
                startClosedTS: 2.0,
                openPeakTS: 2.2,
                endClosedTS: 2.4
            )],
            addedFoodEntries: [.init(id: "manual_food_entry_0000", foodEntryTS: 1.8)]
        )
        let data = try JSONEncoder().encode(draft)
        let decoded = try JSONDecoder().decode(ReviewDraft.self, from: data)
        XCTAssertEqual(decoded, draft)
        XCTAssertEqual(Set(decoded.chewDecisions.map(\.disposition)),
                       Set([.accepted, .edited, .rejected, .unobservable, .uncertain]))
        XCTAssertEqual(decoded.addedChews.first?.disposition, .added)
    }

    func testStaleCandidateHashBlocksValidation() throws {
        let artifact = try makeCandidateArtifact(chews: [chew("ch_0001", 0.1, 0.2, 0.3)])
        let (bundle, hash) = try loadArtifact(artifact)
        let draft = ReviewDraft.empty(
            sessionID: bundle.sessionID,
            candidateRevision: bundle.candidateRevision,
            candidateSHA256: String(repeating: "0", count: 64)
        )
        XCTAssertThrowsError(try ReviewContractValidator.validate(
            draft, candidates: bundle, candidateHash: hash
        )) { error in
            guard case ReviewDraftError.staleHash = error else {
                return XCTFail("Expected staleHash, got \(error)")
            }
        }
    }

    func testSwiftMatchesPythonGoldenHash() throws {
        // Generated with chewsense.canonical.content_sha256. This includes
        // Python float edge cases (1e-06 and -0.0), Unicode, and an unescaped
        // slash so the canonical serializer cannot drift unnoticed.
        let json = #"""
        {
          "artifact_type": "vision_candidates",
          "candidate_revision": 7,
          "chews": [],
          "coarse_label_state": "all_negative",
          "content_sha256": "4268997a30ceac3978bab233311b8d8cb26c85d95b614b64c2a2f9af54f66a32",
          "debug_telemetry": {
            "discarded_raw_hypotheses": [
              {"anchors": [0.0, 0.125, 0.3], "kind": "chew", "reason": "outside reviewed chewing intervals"},
              {"anchors": [0.3, 1e-06, 0.9], "kind": "chew", "reason": "outside reviewed chewing intervals"},
              {"food_entry_ts": -0.0, "kind": "food_entry", "reason": "outside reviewed chewing intervals"}
            ],
            "path": "a/b",
            "unicode": "mâché"
          },
          "food_entries": [],
          "generated_at": "2026-07-12T00:00:00+00:00",
          "model_versions": {"jaw_fusion": "fixture/1"},
          "requires_full_coarse_review": false,
          "schema_version": "2.0.0",
          "session_id": "swift_python_golden"
        }
        """#
        let data = Data(json.utf8)
        // The literal's extra `unicode` field was included in the Python hash
        // below; using the fixed digest proves byte-level parity.
        XCTAssertEqual(
            try CandidateContentHasher.hash(data: data),
            "47297cbc41ab6fa8f2208029699b1a8e0eeef355f633d5b5d72582db08080f0e"
        )
    }

    func testTrackedPythonGoldenCandidateParity() throws {
        let repositoryRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // ChewSenseReviewTests
            .deletingLastPathComponent() // review-macos
            .deletingLastPathComponent() // apps
            .deletingLastPathComponent() // repository root
        let candidateURL = repositoryRoot
            .appending(path: "fixtures/golden/session-v2/derived/candidates-v0001.json")
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: candidateURL.path),
            "Python golden candidate fixture is missing at \(candidateURL.path)"
        )
        let data = try Data(contentsOf: candidateURL)
        let payload = try JSONDecoder().decode(CandidateBundle.self, from: data)
        let computed = try CandidateContentHasher.hash(data: data)
        XCTAssertEqual(computed, payload.contentSHA256)
        XCTAssertEqual(computed, "458b0545db2646bccdd2f5b9e3e09d8c2d93371f1a4ab5e82c9cbc4873195e11")
        XCTAssertNoThrow(try payload.validateContract(expectedSessionID: payload.sessionID))

        let reviewURL = repositoryRoot
            .appending(path: "fixtures/golden/session-v2/session/annotations/review-draft.json")
        let review = try JSONDecoder().decode(ReviewDraft.self, from: Data(contentsOf: reviewURL))
        XCTAssertNoThrow(try ReviewContractValidator.validate(
            review, candidates: payload, candidateHash: computed
        ))
        XCTAssertEqual(review.chewDecisions.map(\.disposition), [
            .accepted, .edited, .rejected, .unobservable, .uncertain,
        ])
        XCTAssertEqual(review.addedChews.first?.note, "Missed by proposal generator")
        XCTAssertEqual(review.addedFoodEntries.first?.note, "Visible food entry")
    }

    func testDraftPersistenceWritesDecisionArtifactOnly() throws {
        let root = FileManager.default.temporaryDirectory
            .appending(path: UUID().uuidString, directoryHint: .isDirectory)
        let raw = root.appending(path: "sessions/raw/s")
        let derived = root.appending(path: "derived/vision/s")
        try FileManager.default.createDirectory(at: derived, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let draftURL = raw.appending(path: "annotations/review-draft.json")
        let session = Session(
            id: "s", rawDir: raw, derivedDir: derived,
            videoURL: nil, annotatedVideoURL: nil, framePredictionsCSV: nil,
            motionCSV: nil, alignedCSV: nil, candidatesJSON: nil,
            overridesURL: draftURL, status: .inReview
        )
        let draft = ReviewDraft.empty(
            sessionID: "s", candidateRevision: 1,
            candidateSHA256: String(repeating: "f", count: 64)
        )
        try OverridePersistence.save(draft, session: session)
        XCTAssertEqual(try OverridePersistence.loadIfPresent(draftURL), draft)
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: derived.appending(path: "motion_aligned_labels.csv").path
        ))
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: raw.appending(path: "annotations/review-overrides-v0001.json").path
        ))
    }

    func testAirPodsEvidenceUsesManifestOriginAndMarksGaps() throws {
        let directory = FileManager.default.temporaryDirectory
            .appending(path: UUID().uuidString, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let csv = directory.appending(path: "motion.csv")
        let manifest = directory.appending(path: "session.json")
        try """
        timestamp,ax,ay,az,gx,gy,gz
        100.25,1,0,0,0,2,0
        100.27,0,2,0,0,0,3
        100.40,0,0,3,4,0,0
        """.write(to: csv, atomically: true, encoding: .utf8)
        let manifestData = try JSONSerialization.data(withJSONObject: [
            "schema_version": "2.0.0",
            "clock_sync": ["video_clock_origin_host_time_s": 100.0],
        ])
        try manifestData.write(to: manifest)

        let samples = try MotionEvidenceLoader.load(from: csv, sessionManifestURL: manifest)
        XCTAssertEqual(samples.count, 3)
        XCTAssertEqual(samples[0].videoTS, 0.25, accuracy: 1e-9)
        XCTAssertEqual(samples[1].videoTS, 0.27, accuracy: 1e-9)
        XCTAssertEqual(samples[2].videoTS, 0.40, accuracy: 1e-9)
        XCTAssertEqual(samples.map(\.continuitySegment), [0, 0, 1])
        XCTAssertEqual(samples[0].accelerationMagnitude, 1.0, accuracy: 1e-9)
        XCTAssertEqual(samples[1].rotationMagnitude, 3.0, accuracy: 1e-9)
    }

    @MainActor
    func testActionsAndCompletionInvalidation() throws {
        let fixture = try makeSessionFixture(chews: [
            chew("ch_0001", 0.10, 0.20, 0.30),
            chew("ch_0002", 0.30, 0.42, 0.55),
        ])
        defer { try? FileManager.default.removeItem(at: fixture.root) }

        let state = ReviewerState()
        state.loadSession(fixture.session)
        XCTAssertNil(state.candidateIntegrityError)
        XCTAssertEqual(state.chews.count, 2)

        state.acceptChew(id: "ch_0001")
        // An accepted neighboring cycle contributes training anchors and must
        // block an overlapping edit. Unobservable candidates intentionally do
        // not reserve timeline space because they are excluded from phase
        // supervision.
        state.acceptChew(id: "ch_0002")
        XCTAssertEqual(state.disposition(for: "ch_0001"), .accepted)
        XCTAssertEqual(state.disposition(for: "ch_0002"), .accepted)
        state.toggleFullVideoPassCompleted()
        XCTAssertTrue(state.canFinalizeReview)

        state.updateMarker(chewID: "ch_0001", marker: .peak, to: 0.22)
        XCTAssertEqual(state.disposition(for: "ch_0001"), .edited)
        XCTAssertFalse(state.overrides.fullVideoPassCompleted)
        XCTAssertFalse(state.canFinalizeReview)

        let beforeBlockedEdit = state.chews.first { $0.id == "ch_0001" }
        state.updateMarker(chewID: "ch_0001", marker: .end, to: 0.31)
        XCTAssertEqual(state.chews.first { $0.id == "ch_0001" }, beforeBlockedEdit)
        XCTAssertTrue(state.statusMessage.contains("Edit blocked"))

        // Adding at the exact previous closed boundary is legal.
        state.addChew(start: 0.55, end: 0.80, peak: 0.68)
        XCTAssertEqual(state.overrides.addedChews.count, 1)
        XCTAssertEqual(state.disposition(for: "manual_chew_0000"), .added)

        // A true overlap is refused and does not alter a neighbor.
        let before = state.chews
        state.addChew(start: 0.54, end: 0.70, peak: 0.62)
        XCTAssertEqual(state.chews, before)
        XCTAssertTrue(state.statusMessage.contains("overlaps"))
    }

    @MainActor
    func testRejectedAndUncertainActionsRemainRevisitable() throws {
        let fixture = try makeSessionFixture(chews: [
            chew("ch_0001", 0.10, 0.20, 0.30),
            chew("ch_0002", 0.30, 0.42, 0.55),
        ])
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let state = ReviewerState()
        state.loadSession(fixture.session)

        state.selectChew(id: "ch_0001")
        state.deleteSelectedOrAt(videoTime: 0.20)
        state.markChewUncertain(id: "ch_0002")

        XCTAssertEqual(state.disposition(for: "ch_0001"), .rejected)
        XCTAssertEqual(state.disposition(for: "ch_0002"), .uncertain)
        XCTAssertTrue(state.chews.contains(where: { $0.id == "ch_0001" }))
        XCTAssertTrue(state.chews.contains(where: { $0.id == "ch_0002" }))

        // A rejected candidate can be revisited and accepted without undoing
        // unrelated decisions.
        state.acceptChew(id: "ch_0001")
        XCTAssertEqual(state.disposition(for: "ch_0001"), .accepted)
        XCTAssertEqual(state.disposition(for: "ch_0002"), .uncertain)
    }

    // MARK: - Fixture helpers

    private func chew(_ id: String, _ start: Double, _ peak: Double, _ end: Double) -> [String: Any] {
        [
            "id": id,
            "start_closed_ts": start,
            "open_peak_ts": peak,
            "end_closed_ts": end,
            "confidence": 0.9,
            "evidence": [String: Any](),
        ]
    }

    private func makeCandidateArtifact(
        chews: [[String: Any]],
        foodEntries: [[String: Any]] = []
    ) throws -> Data {
        var object: [String: Any] = [
            "schema_version": "2.0.0",
            "artifact_type": "vision_candidates",
            "session_id": "golden_session",
            "candidate_revision": 1,
            "generated_at": "2026-07-12T00:00:00Z",
            "model_versions": ["jaw_fusion": "fixture-v1"],
            "coarse_label_state": "reviewed_intervals",
            "requires_full_coarse_review": false,
            "chews": chews,
            "food_entries": foodEntries,
            "debug_telemetry": [String: Any](),
        ]
        let unhashed = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        object["content_sha256"] = try CandidateContentHasher.hash(data: unhashed)
        return try JSONSerialization.data(
            withJSONObject: object,
            options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        )
    }

    private func loadArtifact(_ data: Data) throws -> (CandidateBundle, String) {
        let url = try temporaryFile(named: "candidates-v0001.json", data: data)
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        return try CandidateContentHasher.loadAndValidate(from: url, expectedSessionID: "golden_session")
    }

    private func temporaryFile(named name: String, data: Data) throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appending(path: UUID().uuidString, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appending(path: name)
        try data.write(to: url)
        return url
    }

    private func makeSessionFixture(chews: [[String: Any]]) throws -> (root: URL, session: Session) {
        let root = FileManager.default.temporaryDirectory
            .appending(path: UUID().uuidString, directoryHint: .isDirectory)
        let raw = root.appending(path: "sessions/raw/golden_session")
        let derived = root.appending(path: "derived/vision/golden_session")
        try FileManager.default.createDirectory(at: raw, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: derived, withIntermediateDirectories: true)
        let candidateURL = derived.appending(path: "candidates-v0001.json")
        try makeCandidateArtifact(chews: chews).write(to: candidateURL)
        return (root, Session(
            id: "golden_session", rawDir: raw, derivedDir: derived,
            videoURL: nil, annotatedVideoURL: nil, framePredictionsCSV: nil,
            motionCSV: nil, alignedCSV: nil, candidatesJSON: candidateURL,
            overridesURL: raw.appending(path: "annotations/review-draft.json"),
            status: .candidates
        ))
    }
}
