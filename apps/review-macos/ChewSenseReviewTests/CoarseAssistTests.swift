import Foundation
import XCTest
@testable import ChewSenseReview

/// Assisted chewing-span review: proposals stay proposals until decided, every
/// saved interval keeps its origin, and conflicts with phone labels are never
/// batch-accepted.
@MainActor
final class CoarseAssistTests: XCTestCase {
    private func proposalsJSON(conflict: Bool = true) -> String {
        """
        {"artifact_type":"coarse_label_proposals","session_id":"S1","content_sha256":"abc",
         "summary":{"duration_s":30,"coverage":0.6,"review_items":1,"batch_items":2,"label_conflicts":\(conflict ? 1 : 0)},
         "existing_labels":[{"start":0,"end":30,"label":"not_chewing"}],
         "windows":[{"t_end":3,"video_state":"not_chewing","p_motion":0.1,"motion_state":"not_chewing","video_cycles":0,"video_face_fraction":1,"state":"auto"}],
         "spans":[
          {"id":"cp_0001","start":0,"end":10,"state":"auto","proposal":"not_chewing","reason":"video and motion agree","windows":17,
           "evidence":{"video_chewing_windows":0,"video_not_chewing_windows":17,"video_unavailable_windows":0,"motion_chewing_windows":0,"motion_mean_probability":0.05,"video_cycles_per_s":0},
           "existing_label":"not_chewing","conflict_with_existing_label":false,"priority":0},
          {"id":"cp_0002","start":10,"end":20,"state":"auto","proposal":"chewing","reason":"video and motion agree","windows":20,
           "evidence":{"video_chewing_windows":20,"video_not_chewing_windows":0,"video_unavailable_windows":0,"motion_chewing_windows":20,"motion_mean_probability":0.9,"video_cycles_per_s":1.4},
           "existing_label":"not_chewing","conflict_with_existing_label":\(conflict),"priority":30},
          {"id":"cp_0003","start":20,"end":30,"state":"review","proposal":null,"reason":"face not visible","windows":20,
           "evidence":{"video_chewing_windows":0,"video_not_chewing_windows":0,"video_unavailable_windows":20,"motion_chewing_windows":3,"motion_mean_probability":0.3,"video_cycles_per_s":0},
           "existing_label":"not_chewing","conflict_with_existing_label":false,"priority":10}
         ]}
        """
    }

    private func decode(_ json: String) throws -> CoarseProposals {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).json")
        defer { try? FileManager.default.removeItem(at: url) }
        try Data(json.utf8).write(to: url)
        return try CoarseProposals.decode(from: url)
    }

    func testBatchAcceptSkipsConflictsAndReviewSpans() throws {
        var document = CoarseAssistDocument(spans: try decode(proposalsJSON()).spans)
        XCTAssertEqual(document.batchAcceptAgreement(), 1)
        XCTAssertEqual(document.items.map(\.label), ["not_chewing", nil, nil])
        XCTAssertEqual(document.items[0].decision, .batchAccepted)
        XCTAssertFalse(document.isComplete)
    }

    func testDecisionsKeepOriginAndConflictResolution() throws {
        var document = CoarseAssistDocument(spans: try decode(proposalsJSON()).spans)
        document.setLabel("cp_0002", "not_chewing")   // keeps the phone label against both detectors
        document.setLabel("cp_0003", "unknown")
        document.accept("cp_0001")
        XCTAssertEqual(document.items.map(\.decision), [.accepted, .keptExisting, .labelled])
        let intervals = document.intervals()
        XCTAssertEqual(intervals.count, 3)
        XCTAssertEqual(intervals[1]["proposal"] as? String, "chewing")
        XCTAssertEqual(intervals[1]["decision"] as? String, "kept_existing")
        XCTAssertEqual(intervals[2]["label"] as? String, "unknown")
        XCTAssertTrue(document.isComplete)
    }

    func testUndecidedSpansAreNeverWrittenAsLabels() throws {
        var document = CoarseAssistDocument(spans: try decode(proposalsJSON(conflict: false)).spans)
        document.accept("cp_0002")
        XCTAssertEqual(document.intervals().map { $0["proposal_id"] as? String }, ["cp_0002"])
        document.accept("cp_0003")  // a review span has no proposal to accept
        XCTAssertNil(document.items[2].label)
    }

    func testBoundaryMovesKeepSpansTiling() throws {
        var document = CoarseAssistDocument(spans: try decode(proposalsJSON()).spans)
        document.moveBoundary(of: "cp_0002", start: 11.5, end: 18.0)
        XCTAssertEqual(document.items[0].end, 11.5)
        XCTAssertEqual(document.items[1].start, 11.5)
        XCTAssertEqual(document.items[1].end, 18.0)
        XCTAssertEqual(document.items[2].start, 18.0)
        XCTAssertTrue(document.items[1].boundaryAdjusted)
        document.moveBoundary(of: "cp_0002", start: -5)  // clamped to the previous span
        XCTAssertGreaterThan(document.items[1].start, 0)
    }

    func testSaveWritesImmutableRevisionAndRestores() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let raw = root.appending(path: "sessions/raw/S1")
        let derived = root.appending(path: "derived/vision/S1")
        let proposals = root.appending(path: "derived/assist/S1")
        for directory in [raw, derived, proposals] {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        }
        try Data(proposalsJSON().utf8).write(to: proposals.appending(path: "coarse-proposals-v0001.json"))
        let session = Session(id: "S1", rawDir: raw, derivedDir: derived, videoURL: nil, annotatedVideoURL: nil,
                              framePredictionsCSV: nil, motionCSV: nil, alignedCSV: nil, candidatesJSON: nil,
                              overridesURL: raw.appending(path: "annotations/review-draft.json"), status: .candidates)
        var now = 10.0
        let store = CoarseAssistStore(now: { now })
        store.load(session: session, workspaceRoot: root)
        XCTAssertNotNil(store.proposals)
        now = 20
        store.batchAccept()
        XCTAssertEqual(store.activeSeconds, 0)
        store.startTimer()
        now = 24
        store.setLabel("cp_0002", "chewing")
        store.pauseTimer()
        now = 60
        XCTAssertEqual(store.activeSeconds, 4)
        store.startTimer()
        now = 62.5
        store.save()
        now = 100
        XCTAssertFalse(store.isTiming)
        XCTAssertEqual(store.activeSeconds, 6.5)
        let saved = raw.appending(path: "annotations/coarse-review-v0001.json")
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: saved)) as? [String: Any])
        XCTAssertEqual((json["proposals"] as? [String: Any])?["content_sha256"] as? String, "abc")
        XCTAssertEqual((json["intervals"] as? [[String: Any]])?.count, 2)
        let context = try XCTUnwrap(json["review_context"] as? [String: Any])
        XCTAssertEqual(context["spans_decided"] as? Int, 2)
        XCTAssertEqual(context["complete"] as? Bool, false)
        XCTAssertEqual(context["active_seconds"] as? Double, 6.5)
        XCTAssertFalse(FileManager.default.isWritableFile(atPath: saved.path))

        let reopened = CoarseAssistStore()
        reopened.load(session: session, workspaceRoot: root)
        XCTAssertEqual(reopened.document.decidedCount, 2)
        XCTAssertEqual(reopened.activeSeconds, 6.5)
        reopened.setLabel("cp_0003", "not_chewing")
        reopened.save()
        XCTAssertTrue(FileManager.default.fileExists(atPath: raw.appending(path: "annotations/coarse-review-v0002.json").path))
    }
}

/// Review must decode what `chewsense-motion pipeline-status` writes. The
/// fixture is produced from a synthetic workspace and checked against the
/// Python builder by `test_eating_status.py`.
final class TrainingStatusDecodingTests: XCTestCase {
    func testDecodesSyntheticPipelineStatus() throws {
        let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("Fixtures/pipeline_status_synthetic.json")
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let status = try decoder.decode(PipelineStatus.self, from: Data(contentsOf: url))
        XCTAssertEqual(status.data.usableRecordings, 6)
        XCTAssertTrue(status.recordings.contains { !$0.usable && $0.reason != nil })
        XCTAssertTrue(status.model.trained)
        XCTAssertNotNil(status.model.development?.f1)
        let timeline = try XCTUnwrap(status.model.timelines?.values.first)
        XCTAssertEqual(timeline.tEnd.count, timeline.probability.count)
        XCTAssertFalse(timeline.humanIntervals.isEmpty)
        XCTAssertEqual(timeline.humanIntervals[0][2].string.map { ["chewing", "not_chewing"].contains($0) }, true)
    }
}
