import Foundation
import SwiftUI

/// Immutable `coarse-proposals-vNNNN.json` written by `chewsense-motion assist-propose`.
/// Proposals are never labels; only decisions saved here become labels, and they
/// keep their origin (`decision`, `proposal_id`, `proposal_state`).
struct CoarseProposals: Decodable {
    struct Span: Decodable, Identifiable, Equatable {
        struct Evidence: Decodable, Equatable {
            let videoChewingWindows: Int
            let videoNotChewingWindows: Int
            let videoUnavailableWindows: Int
            let motionChewingWindows: Int
            let motionMeanProbability: Double?
        }

        let id: String
        let start: Double
        let end: Double
        let state: String
        let proposal: String?
        let reason: String
        let windows: Int
        let evidence: Evidence
        let existingLabel: String?
        let conflictWithExistingLabel: Bool

        var isAgreement: Bool { state == "auto" }
    }

    struct Window: Decodable {
        let tEnd: Double
        let videoState: String
        let pMotion: Double?
        let motionState: String
    }

    struct Summary: Decodable {
        let durationS: Double
        let coverage: Double
        let reviewItems: Int
        let batchItems: Int
        let labelConflicts: Int
    }

    struct ExistingLabel: Decodable {
        let start: Double
        let end: Double
        let label: String
    }

    let sessionId: String
    let contentSha256: String
    let summary: Summary
    let spans: [Span]
    let windows: [Window]
    let existingLabels: [ExistingLabel]

    static func decode(from url: URL) throws -> CoarseProposals {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(CoarseProposals.self, from: Data(contentsOf: url))
    }
}

/// The wearer's decisions on one proposal artifact. Pure value logic so the
/// saved artifact can be tested without UI.
struct CoarseAssistDocument: Equatable {
    enum Decision: String, Codable {
        case accepted, batchAccepted = "batch_accepted", labelled, keptExisting = "kept_existing"
    }

    struct Item: Equatable, Identifiable {
        let span: CoarseProposals.Span
        var start: Double
        var end: Double
        var label: String?
        var decision: Decision?
        var id: String { span.id }
        var isDecided: Bool { label != nil }
        var boundaryAdjusted: Bool { abs(start - span.start) > 1e-6 || abs(end - span.end) > 1e-6 }
    }

    var items: [Item]
    var actions: [String: Int] = [:]

    init(spans: [CoarseProposals.Span]) {
        items = spans.map { Item(span: $0, start: $0.start, end: $0.end, label: nil, decision: nil) }
    }

    var decidedCount: Int { items.filter(\.isDecided).count }
    var isComplete: Bool { items.allSatisfy(\.isDecided) }

    /// Agreement spans not yet decided that do not contradict an existing label.
    var batchCandidates: [Item] {
        items.filter { $0.span.isAgreement && !$0.isDecided && !$0.span.conflictWithExistingLabel }
    }

    private mutating func count(_ action: String, _ n: Int = 1) { actions[action, default: 0] += n }

    mutating func accept(_ id: String) {
        guard let index = items.firstIndex(where: { $0.id == id }), let proposal = items[index].span.proposal else { return }
        items[index].label = proposal
        items[index].decision = .accepted
        count("accept")
    }

    mutating func setLabel(_ id: String, _ label: String) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        let span = items[index].span
        items[index].label = label
        if label == span.existingLabel, span.conflictWithExistingLabel {
            items[index].decision = .keptExisting
        } else if label == span.proposal, span.isAgreement {
            items[index].decision = .accepted
        } else {
            items[index].decision = .labelled
        }
        count("label")
    }

    mutating func clear(_ id: String) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        items[index].label = nil
        items[index].decision = nil
        count("clear")
    }

    /// Accept every undecided, non-conflicting agreement span. Returns how many.
    @discardableResult
    mutating func batchAcceptAgreement() -> Int {
        var accepted = 0
        for index in items.indices where items[index].span.isAgreement && !items[index].isDecided
            && !items[index].span.conflictWithExistingLabel {
            items[index].label = items[index].span.proposal
            items[index].decision = .batchAccepted
            accepted += 1
        }
        if accepted > 0 { count("batch_accept") }
        return accepted
    }

    /// Move the boundary shared with the previous (start) or next (end) span.
    /// The neighbour moves too, so spans keep tiling the recording.
    mutating func moveBoundary(of id: String, start newStart: Double? = nil, end newEnd: Double? = nil) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        if let newStart {
            let lower = index > 0 ? items[index - 1].start + 0.1 : 0
            let value = min(max(newStart, lower), items[index].end - 0.1)
            items[index].start = value
            if index > 0 { items[index - 1].end = value }
            count("adjust")
        }
        if let newEnd {
            let upper = index + 1 < items.count ? items[index + 1].end - 0.1 : items[index].end
            let value = max(min(newEnd, upper), items[index].start + 0.1)
            items[index].end = value
            if index + 1 < items.count { items[index + 1].start = value }
            count("adjust")
        }
    }

    /// Decided spans as labelled intervals with their provenance.
    func intervals() -> [[String: Any]] {
        items.compactMap { item in
            guard let label = item.label, let decision = item.decision else { return nil }
            return [
                "start_ts": item.start,
                "end_ts": item.end,
                "label": label,
                "decision": decision.rawValue,
                "proposal_id": item.span.id,
                "proposal": item.span.proposal ?? NSNull(),
                "proposal_state": item.span.state,
                "proposal_reason": item.span.reason,
                "existing_label": item.span.existingLabel ?? NSNull(),
                "boundary_adjusted": item.boundaryAdjusted,
            ]
        }
    }

    /// Restore decisions from a saved revision of the same proposal artifact.
    mutating func restore(from intervals: [[String: Any]]) {
        for interval in intervals {
            guard let id = interval["proposal_id"] as? String,
                  let index = items.firstIndex(where: { $0.id == id }),
                  let label = interval["label"] as? String,
                  let decision = (interval["decision"] as? String).flatMap(Decision.init(rawValue:)) else { continue }
            items[index].label = label
            items[index].decision = decision
            if let start = interval["start_ts"] as? Double { items[index].start = start }
            if let end = interval["end_ts"] as? Double { items[index].end = end }
        }
    }
}

/// Loads proposals for the selected recording, records the wearer's decisions
/// and saves them as an immutable `annotations/coarse-review-vNNNN.json`.
@MainActor
final class CoarseAssistStore: ObservableObject {
    @Published private(set) var proposals: CoarseProposals?
    @Published private(set) var proposalURL: URL?
    @Published var document = CoarseAssistDocument(spans: [])
    @Published var selectedSpanID: String?
    @Published private(set) var message = ""
    @Published private(set) var savedRevision: Int?
    @Published private(set) var isDirty = false

    private var annotationsDirectory: URL?
    private var sessionID: String?
    private var activeSince: Date?
    private var accumulatedSeconds: TimeInterval = 0

    var activeSeconds: TimeInterval {
        accumulatedSeconds + (activeSince.map { Date().timeIntervalSince($0) } ?? 0)
    }

    var selectedItem: CoarseAssistDocument.Item? {
        document.items.first { $0.id == selectedSpanID }
    }

    static func proposalsDirectory(workspaceRoot: URL, sessionID: String) -> URL {
        workspaceRoot.appending(path: "derived/assist/\(sessionID)")
    }

    static func latestRevision(in directory: URL, prefix: String) -> (Int, URL)? {
        let entries = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? []
        return entries.compactMap { url -> (Int, URL)? in
            let name = url.lastPathComponent
            guard name.hasPrefix(prefix), name.hasSuffix(".json") else { return nil }
            let digits = name.dropFirst(prefix.count).dropLast(".json".count)
            return Int(digits).map { ($0, url) }
        }.max { $0.0 < $1.0 }
    }

    func load(session: Session?, workspaceRoot: URL?) {
        pauseTimer()
        proposals = nil
        proposalURL = nil
        document = CoarseAssistDocument(spans: [])
        selectedSpanID = nil
        savedRevision = nil
        isDirty = false
        accumulatedSeconds = 0
        guard let session, let workspaceRoot else {
            message = ""
            return
        }
        sessionID = session.id
        annotationsDirectory = session.rawDir.appending(path: "annotations")
        let directory = Self.proposalsDirectory(workspaceRoot: workspaceRoot, sessionID: session.id)
        guard let (_, url) = Self.latestRevision(in: directory, prefix: "coarse-proposals-v") else {
            message = "No chewing-span proposals yet. They are created after Vision runs (chewsense-motion assist-propose)."
            return
        }
        do {
            let loaded = try CoarseProposals.decode(from: url)
            guard loaded.sessionId == session.id else {
                message = "Proposal file belongs to \(loaded.sessionId); not loaded."
                return
            }
            proposals = loaded
            proposalURL = url
            document = CoarseAssistDocument(spans: loaded.spans)
            restoreLatestReview(matching: loaded.contentSha256)
            selectedSpanID = document.items.first { !$0.isDecided && !$0.span.isAgreement }?.id ?? document.items.first?.id
        } catch {
            message = "Could not read \(url.lastPathComponent): \(error.localizedDescription)"
        }
    }

    private func restoreLatestReview(matching hash: String) {
        guard let annotationsDirectory,
              let (revision, url) = Self.latestRevision(in: annotationsDirectory, prefix: "coarse-review-v"),
              let data = try? Data(contentsOf: url),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            message = proposals.map { "\($0.summary.reviewItems) spans need you; \($0.summary.batchItems) agreement spans can be accepted together." } ?? ""
            return
        }
        savedRevision = revision
        let proposalRef = json["proposals"] as? [String: Any]
        guard proposalRef?["content_sha256"] as? String == hash else {
            message = "Saved review v\(revision) used older proposals; its labels still count. These new proposals start undecided."
            return
        }
        document.restore(from: json["intervals"] as? [[String: Any]] ?? [])
        let context = json["review_context"] as? [String: Any]
        accumulatedSeconds = context?["active_seconds"] as? Double ?? 0
        document.actions = context?["actions"] as? [String: Int] ?? [:]
        message = "Restored review v\(revision): \(document.decidedCount)/\(document.items.count) spans decided."
    }

    // MARK: Timer (measures real review time)

    func resumeTimer() {
        if activeSince == nil, proposals != nil { activeSince = Date() }
    }

    func pauseTimer() {
        if let since = activeSince {
            accumulatedSeconds += Date().timeIntervalSince(since)
            activeSince = nil
        }
    }

    // MARK: Decisions

    func accept(_ id: String) { mutate { $0.accept(id) }; advance(from: id) }
    func setLabel(_ id: String, _ label: String) { mutate { $0.setLabel(id, label) }; advance(from: id) }
    func clear(_ id: String) { mutate { $0.clear(id) } }

    func batchAccept() {
        var accepted = 0
        mutate { accepted = $0.batchAcceptAgreement() }
        message = "Accepted \(accepted) agreement spans together. Each keeps its origin (batch_accepted)."
    }

    func moveStart(of id: String, to time: Double) { mutate { $0.moveBoundary(of: id, start: time) } }
    func moveEnd(of id: String, to time: Double) { mutate { $0.moveBoundary(of: id, end: time) } }

    private func mutate(_ change: (inout CoarseAssistDocument) -> Void) {
        resumeTimer()
        change(&document)
        isDirty = true
    }

    private func advance(from id: String) {
        guard let index = document.items.firstIndex(where: { $0.id == id }) else { return }
        let rest = document.items[(index + 1)...] + document.items[..<index]
        selectedSpanID = rest.first { !$0.isDecided && !$0.span.isAgreement }?.id ?? selectedSpanID
    }

    // MARK: Save

    func save() {
        guard let proposals, let proposalURL, let annotationsDirectory, let sessionID else { return }
        guard document.decidedCount > 0 else {
            message = "Nothing decided yet; nothing saved."
            return
        }
        pauseTimer()
        defer { resumeTimer() }
        let next = (Self.latestRevision(in: annotationsDirectory, prefix: "coarse-review-v")?.0 ?? 0) + 1
        let payload: [String: Any] = [
            "artifact_type": "coarse_label_review",
            "schema_version": 1,
            "session_id": sessionID,
            "revision": next,
            "saved_at": ISO8601DateFormatter().string(from: Date()),
            "proposals": ["file": proposalURL.lastPathComponent, "content_sha256": proposals.contentSha256],
            "label_semantics": "Human decisions on proposed chewing spans. Spans never decided are not labels.",
            "intervals": document.intervals(),
            "review_context": [
                "mode": "assisted_coarse",
                "active_seconds": (activeSeconds * 10).rounded() / 10,
                "actions": document.actions,
                "spans_total": document.items.count,
                "spans_decided": document.decidedCount,
                "complete": document.isComplete,
                "recording_duration_s": proposals.summary.durationS,
            ] as [String: Any],
        ]
        do {
            try FileManager.default.createDirectory(at: annotationsDirectory, withIntermediateDirectories: true)
            let url = annotationsDirectory.appending(path: String(format: "coarse-review-v%04d.json", next))
            guard !FileManager.default.fileExists(atPath: url.path) else {
                message = "\(url.lastPathComponent) already exists; nothing overwritten."
                return
            }
            let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
            // Write a temporary file, then move it into place: the move fails
            // rather than replace an existing revision.
            let temporary = annotationsDirectory.appending(path: ".\(url.lastPathComponent).\(UUID().uuidString).tmp")
            try data.write(to: temporary, options: .atomic)
            do {
                try FileManager.default.moveItem(at: temporary, to: url)
            } catch {
                try? FileManager.default.removeItem(at: temporary)
                throw error
            }
            try? FileManager.default.setAttributes([.posixPermissions: 0o444], ofItemAtPath: url.path)
            savedRevision = next
            isDirty = false
            message = "Saved \(url.lastPathComponent): \(document.decidedCount)/\(document.items.count) spans, "
                + "\(Int(activeSeconds)) s of active review."
        } catch {
            message = "Save failed: \(error.localizedDescription)"
        }
    }
}
