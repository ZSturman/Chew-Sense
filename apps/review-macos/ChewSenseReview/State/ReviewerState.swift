import SwiftUI

/// Authoritative v2 review state. Automatic candidates stay immutable; every
/// reviewer action is represented as an explicit decision in `ReviewDraft`.
@MainActor
final class ReviewerState: ObservableObject {
    // MARK: Workspace + sessions
    @Published var workspaceRoot: URL?
    @Published var sessions: [Session] = []
    @Published var selectedSession: Session?

    // MARK: Loaded session data
    @Published private(set) var baseRows: [AlignedLabel] = []
    @Published private(set) var alignedRows: [AlignedLabel] = []
    @Published private(set) var candidates: CandidateBundle?
    @Published private(set) var overrides = ReviewDraft.empty(
        sessionID: "", candidateRevision: 0, candidateSHA256: ""
    )
    @Published private(set) var candidateIntegrityError: String?
    private var loadedCandidateHash: String?

    /// Compatibility name retained for existing playback UI while the file
    /// contract is now `candidates-vNNNN.json`.
    var chewCycles: CandidateBundle? { candidates }

    // MARK: Working events + selection
    @Published private(set) var chews: [ReviewChew] = []
    @Published private(set) var bites: [ReviewFoodEntry] = []
    @Published private(set) var eatingSegments: [EatingSegment] = []
    @Published private(set) var framePredictions: [FramePrediction] = []
    @Published private(set) var motionEvidence: [MotionEvidenceSample] = []
    @Published private(set) var motionEvidenceMessage: String?
    @Published var selectedChewID: String?
    @Published var selectedBiteID: String?
    @Published var lastTouchedMarker: ChewMarker = .start

    // MARK: Playback-coupled state
    @Published var loopSelectedChew = false
    @Published var loopPadding = 0.10
    @Published var useAnnotatedVideo = false

    // MARK: UI state
    @Published var statusMessage = ""
    @Published var isDirty = false

    // MARK: Undo / redo
    private struct Snapshot {
        var draft: ReviewDraft
        var selectedChewID: String?
        var selectedBiteID: String?
    }
    private var undoStack: [Snapshot] = []
    private var redoStack: [Snapshot] = []
    var canUndo: Bool { !undoStack.isEmpty }
    var canRedo: Bool { !redoStack.isEmpty }

    private static let rootDefaultsKey = "ChewSenseReview.workspaceRoot"
    private static let minimumMarkerGap = 0.001

    init() {
        if let path = UserDefaults.standard.string(forKey: Self.rootDefaultsKey) {
            let url = URL(fileURLWithPath: path)
            if FileManager.default.fileExists(atPath: url.path) {
                setWorkspaceRoot(url)
            }
        }
    }

    // MARK: - Workspace

    func setWorkspaceRoot(_ url: URL) {
        workspaceRoot = url
        UserDefaults.standard.set(url.path, forKey: Self.rootDefaultsKey)
        refreshSessions()
    }

    func refreshSessions() {
        guard let root = workspaceRoot else { sessions = []; return }
        sessions = SessionDiscovery(paths: WorkspacePaths(root: root)).listSessions()
    }

    // MARK: - Loading

    func loadSession(_ session: Session) {
        selectedSession = session
        resetLoadedState()
        guard let candidateURL = session.candidatesJSON else {
            candidateIntegrityError = "Legacy v1 labels cannot be reviewed automatically. Run the explicit v1 migration, rerun vision, and review from scratch."
            statusMessage = candidateIntegrityError ?? "Migration required"
            return
        }
        guard FileManager.default.fileExists(atPath: candidateURL.path) else {
            candidateIntegrityError = "The review draft references \(candidateURL.lastPathComponent), but that immutable candidate artifact is missing. Restore that exact revision or begin a fresh review; another revision will not be substituted."
            statusMessage = "Load blocked: \(candidateIntegrityError ?? "candidate artifact missing")"
            return
        }

        do {
            let (bundle, hash) = try CandidateContentHasher.loadAndValidate(
                from: candidateURL,
                expectedSessionID: session.id
            )
            let draft = try OverridePersistence.loadIfPresent(session.overridesURL)
                ?? .empty(
                    sessionID: session.id,
                    candidateRevision: bundle.candidateRevision,
                    candidateSHA256: hash
                )
            try ReviewContractValidator.validate(draft, candidates: bundle, candidateHash: hash)

            candidates = bundle
            loadedCandidateHash = hash
            overrides = draft
            baseRows = try loadAutoBaseline(for: session)
            framePredictions = session.framePredictionsCSV
                .map { FramePredictionsLoader.load(from: $0) } ?? []
            if let motionCSV = session.motionCSV {
                do {
                    motionEvidence = try MotionEvidenceLoader.load(
                        from: motionCSV,
                        sessionManifestURL: session.rawDir.appending(path: "session.json")
                    )
                } catch {
                    motionEvidence = []
                    motionEvidenceMessage = error.localizedDescription
                }
            }
            rebuildPreview()
            statusMessage = bundle.requiresFullCoarseReview
                ? "Loaded revision \(bundle.candidateRevision), but coarse labels are missing; verification is blocked"
                : "Loaded \(bundle.chews.count) chew and \(bundle.foodEntries.count) food-entry candidates · v\(String(format: "%04d", bundle.candidateRevision))"
        } catch {
            candidateIntegrityError = error.localizedDescription
            statusMessage = "Load blocked: \(error.localizedDescription)"
        }
    }

    private func resetLoadedState() {
        baseRows = []
        alignedRows = []
        candidates = nil
        loadedCandidateHash = nil
        candidateIntegrityError = nil
        overrides = .empty(sessionID: selectedSession?.id ?? "", candidateRevision: 0, candidateSHA256: "")
        chews = []
        bites = []
        eatingSegments = []
        framePredictions = []
        motionEvidence = []
        motionEvidenceMessage = nil
        selectedChewID = nil
        selectedBiteID = nil
        undoStack.removeAll()
        redoStack.removeAll()
        isDirty = false
    }

    private func loadAutoBaseline(for session: Session) throws -> [AlignedLabel] {
        let snapshot = session.derivedDir.appending(path: "motion_aligned_labels.auto.csv")
        if FileManager.default.fileExists(atPath: snapshot.path) {
            return try CSVLoader.loadAlignedLabels(from: snapshot)
        }
        guard let aligned = session.alignedCSV else { return [] }
        return try CSVLoader.loadAlignedLabels(from: aligned)
    }

    // MARK: - Saving + completion

    func saveOverrides() {
        guard let session = selectedSession,
              let candidateURL = session.candidatesJSON,
              let candidates,
              let loadedCandidateHash else {
            statusMessage = "Save blocked: no validated v2 candidate artifact is loaded"
            return
        }
        do {
            // Re-read immediately before every save. A replaced or modified
            // artifact can never be hidden by state loaded earlier in the app.
            let (currentBundle, currentHash) = try CandidateContentHasher.loadAndValidate(
                from: candidateURL,
                expectedSessionID: session.id
            )
            guard currentBundle.candidateRevision == candidates.candidateRevision,
                  currentHash == loadedCandidateHash else {
                throw ReviewDraftError.staleHash(expected: loadedCandidateHash, actual: currentHash)
            }
            var draft = overrides
            draft.updatedAt = ISO8601DateFormatter().string(from: Date())
            try ReviewContractValidator.validate(draft, candidates: currentBundle, candidateHash: currentHash)
            try OverridePersistence.save(draft, session: session)
            overrides = draft
            isDirty = false
            statusMessage = draft.verified
                ? "Saved verified decision draft; run finalize-session to freeze it"
                : "Saved review draft linked to \(shortHash(currentHash))"
            refreshSessions()
        } catch {
            candidateIntegrityError = error.localizedDescription
            statusMessage = "Save blocked: \(error.localizedDescription)"
        }
    }

    func toggleVerified() {
        if overrides.verified {
            pushUndo()
            overrides.verified = false
            isDirty = true
            return
        }
        guard canFinalizeReview else {
            statusMessage = completionStatus
            return
        }
        pushUndo()
        overrides.verified = true
        isDirty = true
        statusMessage = "Review complete; save, audit, then run finalize-session"
    }

    func toggleFullVideoPassCompleted() {
        guard candidateIntegrityError == nil else {
            statusMessage = "Full pass cannot be completed while candidate lineage is invalid"
            return
        }
        pushUndo()
        overrides.fullVideoPassCompleted.toggle()
        overrides.verified = false
        isDirty = true
        statusMessage = overrides.fullVideoPassCompleted
            ? "Full-video pass completed"
            : "Full-video pass reopened"
    }

    private func invalidateCompletion() {
        overrides.verified = false
        overrides.fullVideoPassCompleted = false
    }

    var canFinalizeReview: Bool {
        guard candidateIntegrityError == nil,
              let candidates,
              !candidates.requiresFullCoarseReview else { return false }
        let decidedChews = Set(overrides.chewDecisions.map(\.candidateID))
        let decidedEntries = Set(overrides.foodEntryDecisions.map(\.candidateID))
        return Set(candidates.chews.map(\.id)).isSubset(of: decidedChews)
            && Set(candidates.foodEntries.map(\.id)).isSubset(of: decidedEntries)
            && overrides.fullVideoPassCompleted
    }

    var completionStatus: String {
        if let candidateIntegrityError { return "Lineage error: \(candidateIntegrityError)" }
        guard let candidates else { return "Load a v2 candidate artifact" }
        if candidates.requiresFullCoarseReview { return "Complete coarse review before event verification" }
        let decidedChews = Set(overrides.chewDecisions.map(\.candidateID))
        let chewRemaining = candidates.chews.filter { !decidedChews.contains($0.id) }.count
        if chewRemaining > 0 { return "Review \(chewRemaining) remaining chew candidate(s)" }
        let decidedEntries = Set(overrides.foodEntryDecisions.map(\.candidateID))
        let entryRemaining = candidates.foodEntries.filter { !decidedEntries.contains($0.id) }.count
        if entryRemaining > 0 { return "Review \(entryRemaining) remaining food-entry candidate(s)" }
        if !overrides.fullVideoPassCompleted { return "Complete the final full-video pass" }
        return "Ready to verify"
    }

    private func shortHash(_ value: String) -> String {
        String(value.prefix(12)) + "…"
    }

    // MARK: - Selection

    var selectedChew: ReviewChew? {
        guard let id = selectedChewID else { return nil }
        return chews.first { $0.id == id }
    }

    var selectedChewIndex: Int? {
        guard let id = selectedChewID else { return nil }
        return chews.firstIndex { $0.id == id }
    }

    func selectChew(id: String?) {
        selectedChewID = id
        if id != nil { selectedBiteID = nil }
    }

    @discardableResult
    func selectNextChew() -> ReviewChew? {
        guard !chews.isEmpty else { return nil }
        let index = selectedChewIndex.map { min($0 + 1, chews.count - 1) } ?? 0
        selectedChewID = chews[index].id
        return chews[index]
    }

    @discardableResult
    func selectPreviousChew() -> ReviewChew? {
        guard !chews.isEmpty else { return nil }
        let index = selectedChewIndex.map { max($0 - 1, 0) } ?? (chews.count - 1)
        selectedChewID = chews[index].id
        return chews[index]
    }

    @discardableResult
    func selectNearestChew(to videoTime: Double) -> ReviewChew? {
        guard let chew = chews.min(by: { $0.distance(to: videoTime) < $1.distance(to: videoTime) }) else {
            return nil
        }
        selectedChewID = chew.id
        return chew
    }

    var selectedBite: ReviewFoodEntry? {
        guard let id = selectedBiteID else { return nil }
        return bites.first { $0.id == id }
    }

    func selectBite(id: String?) {
        selectedBiteID = id
        if id != nil { selectedChewID = nil }
    }

    // MARK: - Chew decisions

    func disposition(for id: String) -> ReviewDisposition? {
        if overrides.addedChews.contains(where: { $0.id == id }) { return .added }
        return overrides.chewDecisions.first { $0.candidateID == id }?.disposition
    }

    func isVerified(id: String) -> Bool { disposition(for: id) != nil }

    func acceptChew(id: String) {
        guard isCandidateChew(id) else { return }
        setChewDecision(.init(candidateID: id, disposition: .accepted))
        statusMessage = "Accepted \(id)"
    }

    func markChewUnobservable(id: String) {
        guard isCandidateChew(id) else { return }
        setChewDecision(.init(candidateID: id, disposition: .unobservable))
        statusMessage = "Marked \(id) unobservable (excluded from phase supervision)"
    }

    func markChewUncertain(id: String) {
        guard isCandidateChew(id) else { return }
        setChewDecision(.init(candidateID: id, disposition: .uncertain))
        statusMessage = "Marked \(id) uncertain (excluded from phase supervision)"
    }

    func toggleChewVerified(id: String) {
        if let index = overrides.chewDecisions.firstIndex(where: { $0.candidateID == id }) {
            pushUndo()
            invalidateCompletion()
            overrides.chewDecisions.remove(at: index)
            rebuildPreview()
            isDirty = true
            statusMessage = "Returned \(id) to pending"
        } else {
            acceptChew(id: id)
        }
    }

    private func setChewDecision(_ decision: ReviewDraft.ChewDecision) {
        pushUndo()
        invalidateCompletion()
        overrides.chewDecisions.removeAll { $0.candidateID == decision.candidateID }
        overrides.chewDecisions.append(decision)
        rebuildPreview()
        isDirty = true
    }

    /// Add a closed→maximum-open→closed cycle. Existing cycles are never
    /// trimmed to make room; a real overlap is reported and the add is refused.
    func addChew(start: Double, end: Double, peak: Double? = nil) {
        let start = max(0, start)
        let peak = peak ?? ((start + end) / 2)
        guard start < peak, peak < end else {
            statusMessage = "Add blocked: Closed start < Maximum open < Closed end is required"
            return
        }
        if let conflict = chews.first(where: {
            ($0.disposition == nil || $0.disposition?.contributesPhaseSupervision == true)
                && $0.startTS < end && start < $0.endTS
        }) {
            statusMessage = "Add blocked: overlaps \(conflict.id). Shared closed boundaries are allowed; overlap is not."
            return
        }
        pushUndo()
        invalidateCompletion()
        let id = nextManualChewID()
        overrides.addedChews.append(.init(
            id: id,
            startClosedTS: start,
            openPeakTS: peak,
            endClosedTS: end
        ))
        rebuildPreview()
        selectedChewID = id
        isDirty = true
        statusMessage = "Added \(id)"
    }

    @discardableResult
    func updateMarker(
        chewID: String,
        marker: ChewMarker,
        to time: Double,
        recordUndo: Bool = true
    ) -> ReviewChew? {
        guard let chew = chews.first(where: { $0.id == chewID }) else { return nil }
        guard let acceptedTime = validatedMarkerTime(
            chew: chew, marker: marker, proposed: time
        ) else { return chew }
        if recordUndo { pushUndo() }
        invalidateCompletion()
        var updated = chew
        switch marker {
        case .start: updated.startTS = acceptedTime
        case .peak: updated.peakTS = acceptedTime
        case .end: updated.endTS = acceptedTime
        }
        if updated.source == .added {
            if let index = overrides.addedChews.firstIndex(where: { $0.id == chewID }) {
                overrides.addedChews[index].startClosedTS = updated.startTS
                overrides.addedChews[index].openPeakTS = updated.peakTS
                overrides.addedChews[index].endClosedTS = updated.endTS
            }
        } else {
            overrides.chewDecisions.removeAll { $0.candidateID == chewID }
            overrides.chewDecisions.append(.init(
                candidateID: chewID,
                disposition: .edited,
                startClosedTS: updated.startTS,
                openPeakTS: updated.peakTS,
                endClosedTS: updated.endTS
            ))
        }
        lastTouchedMarker = marker
        rebuildPreview()
        selectedChewID = chewID
        isDirty = true
        statusMessage = "Edited \(marker.displayName) for \(chewID)"
        return chews.first { $0.id == chewID }
    }

    func nudgeLastTouchedMarker(by delta: Double) {
        guard let chew = selectedChew else { return }
        updateMarker(
            chewID: chew.id,
            marker: lastTouchedMarker,
            to: chew.timestamp(for: lastTouchedMarker) + delta
        )
    }

    func deleteSelectedOrAt(videoTime: Double) {
        guard let chew = selectedChew ?? chews.first(where: { $0.contains(videoTime) }) else { return }
        pushUndo()
        invalidateCompletion()
        if chew.source == .added {
            overrides.addedChews.removeAll { $0.id == chew.id }
        } else {
            overrides.chewDecisions.removeAll { $0.candidateID == chew.id }
            overrides.chewDecisions.append(.init(candidateID: chew.id, disposition: .rejected))
        }
        rebuildPreview()
        selectedChewID = nil
        isDirty = true
        statusMessage = "Rejected \(chew.id)"
    }

    func revertChewToAuto(id: String) {
        guard isCandidateChew(id) else {
            statusMessage = "No candidate baseline for \(id)"
            return
        }
        pushUndo()
        invalidateCompletion()
        overrides.chewDecisions.removeAll { $0.candidateID == id }
        rebuildPreview()
        selectedChewID = id
        isDirty = true
        statusMessage = "Returned \(id) to pending candidate"
    }

    private func validatedMarkerTime(
        chew: ReviewChew,
        marker: ChewMarker,
        proposed: Double
    ) -> Double? {
        let gap = Self.minimumMarkerGap
        guard proposed >= 0, proposed.isFinite else {
            statusMessage = "Edit blocked: marker time must be finite and non-negative"
            return nil
        }
        let others = chews.filter {
            $0.id != chew.id
                && ($0.disposition == nil || $0.disposition?.contributesPhaseSupervision == true)
        }.sorted { $0.startTS < $1.startTS }
        let previous = others.last { $0.startTS < chew.startTS }
        let next = others.first { $0.startTS > chew.startTS }
        switch marker {
        case .start:
            let lower = previous?.endTS ?? 0
            guard proposed >= lower, proposed <= chew.peakTS - gap else {
                statusMessage = "Edit blocked: Closed start would overlap a neighbor or cross Maximum open"
                return nil
            }
        case .peak:
            guard proposed >= chew.startTS + gap, proposed <= chew.endTS - gap else {
                statusMessage = "Edit blocked: Maximum open must remain between both closed anchors"
                return nil
            }
        case .end:
            let upper = next?.startTS ?? Double.infinity
            guard proposed <= upper, proposed >= chew.peakTS + gap else {
                statusMessage = "Edit blocked: Closed end would overlap a neighbor or cross Maximum open"
                return nil
            }
        }
        return proposed
    }

    private func isCandidateChew(_ id: String) -> Bool {
        candidates?.chews.contains(where: { $0.id == id }) == true
    }

    private func nextManualChewID() -> String {
        let existing = Set((candidates?.chews.map(\.id) ?? []) + overrides.addedChews.map(\.id))
        var index = 0
        while true {
            let id = String(format: "manual_chew_%04d", index)
            if !existing.contains(id) { return id }
            index += 1
        }
    }

    // MARK: - Food-entry decisions

    func foodEntryDisposition(for id: String) -> ReviewDisposition? {
        if overrides.addedFoodEntries.contains(where: { $0.id == id }) { return .added }
        return overrides.foodEntryDecisions.first { $0.candidateID == id }?.disposition
    }

    func isBiteVerified(id: String) -> Bool { foodEntryDisposition(for: id) != nil }

    func acceptBite(id: String) {
        guard candidates?.foodEntries.contains(where: { $0.id == id }) == true else { return }
        setFoodEntryDecision(.init(candidateID: id, disposition: .accepted))
        statusMessage = "Accepted food entry \(id)"
    }

    func markFoodEntryUnobservable(id: String) {
        setFoodEntryDecision(.init(candidateID: id, disposition: .unobservable))
        statusMessage = "Marked food entry \(id) unobservable"
    }

    func markFoodEntryUncertain(id: String) {
        setFoodEntryDecision(.init(candidateID: id, disposition: .uncertain))
        statusMessage = "Marked food entry \(id) uncertain"
    }

    private func setFoodEntryDecision(_ decision: ReviewDraft.FoodEntryDecision) {
        pushUndo()
        invalidateCompletion()
        overrides.foodEntryDecisions.removeAll { $0.candidateID == decision.candidateID }
        overrides.foodEntryDecisions.append(decision)
        rebuildPreview()
        isDirty = true
    }

    func addBite(at time: Double) {
        pushUndo()
        invalidateCompletion()
        let id = nextManualFoodEntryID()
        overrides.addedFoodEntries.append(.init(id: id, foodEntryTS: max(0, time)))
        rebuildPreview()
        selectedBiteID = id
        isDirty = true
        statusMessage = "Added food entry \(id)"
    }

    func moveBite(id: String, to time: Double) {
        guard let entry = bites.first(where: { $0.id == id }) else { return }
        pushUndo()
        invalidateCompletion()
        if entry.source == .added {
            if let index = overrides.addedFoodEntries.firstIndex(where: { $0.id == id }) {
                overrides.addedFoodEntries[index].foodEntryTS = max(0, time)
            }
        } else {
            overrides.foodEntryDecisions.removeAll { $0.candidateID == id }
            overrides.foodEntryDecisions.append(.init(
                candidateID: id,
                disposition: .edited,
                foodEntryTS: max(0, time)
            ))
        }
        rebuildPreview()
        selectedBiteID = id
        isDirty = true
        statusMessage = "Moved food entry \(id)"
    }

    func rejectBite(id: String) {
        guard let entry = bites.first(where: { $0.id == id }) else { return }
        pushUndo()
        invalidateCompletion()
        if entry.source == .added {
            overrides.addedFoodEntries.removeAll { $0.id == id }
        } else {
            overrides.foodEntryDecisions.removeAll { $0.candidateID == id }
            overrides.foodEntryDecisions.append(.init(candidateID: id, disposition: .rejected))
        }
        rebuildPreview()
        selectedBiteID = nil
        isDirty = true
        statusMessage = "Rejected food entry \(id)"
    }

    private func nextManualFoodEntryID() -> String {
        let existing = Set((candidates?.foodEntries.map(\.id) ?? []) + overrides.addedFoodEntries.map(\.id))
        var index = 0
        while true {
            let id = String(format: "manual_food_entry_%04d", index)
            if !existing.contains(id) { return id }
            index += 1
        }
    }

    // MARK: - Queue workflow

    var candidateChewCount: Int { (candidates?.chews.count ?? 0) + overrides.addedChews.count }
    var candidateFoodEntryCount: Int { (candidates?.foodEntries.count ?? 0) + overrides.addedFoodEntries.count }
    var verifiedCount: Int { overrides.chewDecisions.count + overrides.addedChews.count }
    var verifiedBiteCount: Int { overrides.foodEntryDecisions.count + overrides.addedFoodEntries.count }

    @discardableResult
    func verifyAndAdvance() -> ReviewChew? {
        guard let current = selectedChew else { return nil }
        if disposition(for: current.id) == nil { acceptChew(id: current.id) }
        return advanceToNextUnverified() ?? selectNextChew()
    }

    @discardableResult
    func advanceToNextUnverified() -> ReviewChew? {
        let start = (selectedChewIndex ?? -1) + 1
        if start < chews.count {
            for index in start..<chews.count where disposition(for: chews[index].id) == nil {
                selectedChewID = chews[index].id
                return chews[index]
            }
        }
        for chew in chews where disposition(for: chew.id) == nil {
            selectedChewID = chew.id
            return chew
        }
        statusMessage = "All visible chew candidates reviewed"
        return nil
    }

    // MARK: - Undo / redo

    private func pushUndo() {
        undoStack.append(Snapshot(
            draft: overrides,
            selectedChewID: selectedChewID,
            selectedBiteID: selectedBiteID
        ))
        if undoStack.count > 100 { undoStack.removeFirst(undoStack.count - 100) }
        redoStack.removeAll()
    }

    func undo() {
        guard let snapshot = undoStack.popLast() else { return }
        redoStack.append(Snapshot(
            draft: overrides,
            selectedChewID: selectedChewID,
            selectedBiteID: selectedBiteID
        ))
        overrides = snapshot.draft
        selectedChewID = snapshot.selectedChewID
        selectedBiteID = snapshot.selectedBiteID
        rebuildPreview()
        isDirty = true
        statusMessage = "Undo"
    }

    func redo() {
        guard let snapshot = redoStack.popLast() else { return }
        undoStack.append(Snapshot(
            draft: overrides,
            selectedChewID: selectedChewID,
            selectedBiteID: selectedBiteID
        ))
        overrides = snapshot.draft
        selectedChewID = snapshot.selectedChewID
        selectedBiteID = snapshot.selectedBiteID
        rebuildPreview()
        isDirty = true
        statusMessage = "Redo"
    }

    // MARK: - Preview construction (no normalization)

    private func rebuildPreview() {
        guard let candidates else {
            chews = []
            bites = []
            alignedRows = []
            eatingSegments = []
            return
        }
        alignedRows = OverrideApplier.apply(overrides, candidates: candidates, to: baseRows)
        chews = buildChews(candidates)
        bites = buildFoodEntries(candidates)
        eatingSegments = Self.buildEatingSegments(from: alignedRows)
        if let id = selectedChewID, !chews.contains(where: { $0.id == id }) { selectedChewID = nil }
        if let id = selectedBiteID, !bites.contains(where: { $0.id == id }) { selectedBiteID = nil }
    }

    private func buildChews(_ candidates: CandidateBundle) -> [ReviewChew] {
        let decisions = Dictionary(uniqueKeysWithValues: overrides.chewDecisions.map { ($0.candidateID, $0) })
        var result: [ReviewChew] = []
        for candidate in candidates.chews {
            let decision = decisions[candidate.id]
            let edited = decision?.disposition == .edited
            result.append(ReviewChew(
                id: candidate.id,
                startTS: edited ? (decision?.startClosedTS ?? candidate.startClosedTS) : candidate.startClosedTS,
                peakTS: edited ? (decision?.openPeakTS ?? candidate.openPeakTS) : candidate.openPeakTS,
                endTS: edited ? (decision?.endClosedTS ?? candidate.endClosedTS) : candidate.endClosedTS,
                confidence: candidate.confidence,
                source: edited ? .edited : .candidate,
                disposition: decision?.disposition
            ))
        }
        result += overrides.addedChews.map {
            ReviewChew(
                id: $0.id,
                startTS: $0.startClosedTS,
                peakTS: $0.openPeakTS,
                endTS: $0.endClosedTS,
                confidence: $0.confidence,
                source: .added,
                disposition: .added
            )
        }
        // Sorting is presentational only; boundaries are never altered/dropped.
        return result.sorted {
            if $0.startTS == $1.startTS { return $0.id < $1.id }
            return $0.startTS < $1.startTS
        }
    }

    private func buildFoodEntries(_ candidates: CandidateBundle) -> [ReviewFoodEntry] {
        let decisions = Dictionary(uniqueKeysWithValues: overrides.foodEntryDecisions.map { ($0.candidateID, $0) })
        var result: [ReviewFoodEntry] = []
        for candidate in candidates.foodEntries {
            let decision = decisions[candidate.id]
            result.append(ReviewFoodEntry(
                id: candidate.id,
                ts: decision?.disposition == .edited
                    ? (decision?.foodEntryTS ?? candidate.foodEntryTS)
                    : candidate.foodEntryTS,
                confidence: candidate.confidence,
                source: decision?.disposition == .edited ? .edited : .candidate
            ))
        }
        result += overrides.addedFoodEntries.map {
            ReviewFoodEntry(id: $0.id, ts: $0.foodEntryTS, confidence: $0.confidence, source: .added)
        }
        return result.sorted { $0.ts < $1.ts }
    }

    var stats: (visible: Int, added: Int, edited: Int, rejected: Int, excluded: Int) {
        (
            chews.filter { $0.disposition != .rejected }.count,
            overrides.addedChews.count,
            overrides.chewDecisions.filter { $0.disposition == .edited }.count,
            overrides.chewDecisions.filter { $0.disposition == .rejected }.count,
            overrides.chewDecisions.filter { $0.disposition == .unobservable || $0.disposition == .uncertain }.count
        )
    }

    // MARK: - Marker navigation

    var allMarkers: [(time: Double, chewID: String, marker: ChewMarker)] {
        chews.flatMap { chew in
            [(chew.startTS, chew.id, .start), (chew.peakTS, chew.id, .peak), (chew.endTS, chew.id, .end)]
        }.sorted { $0.0 < $1.0 }
    }

    func nextMarker(after time: Double) -> (time: Double, chewID: String, marker: ChewMarker)? {
        allMarkers.first { $0.time > time + 0.001 }
    }

    func previousMarker(before time: Double) -> (time: Double, chewID: String, marker: ChewMarker)? {
        allMarkers.last { $0.time < time - 0.001 }
    }

    // MARK: - Eating evidence (read-only in v2 event review)

    struct EatingSegment: Identifiable, Equatable {
        let id: String
        let startRow: Int
        let endRow: Int
        let startTS: Double
        let endTS: Double
        var duration: Double { max(0, endTS - startTS) }
    }

    enum EatingEdge { case start, end }

    private static func buildEatingSegments(from rows: [AlignedLabel]) -> [EatingSegment] {
        guard !rows.isEmpty else { return [] }
        var result: [EatingSegment] = []
        var index = 0
        while index < rows.count {
            guard rows[index].eating == 1 else { index += 1; continue }
            let start = index
            while index < rows.count, rows[index].eating == 1 { index += 1 }
            let end = index - 1
            let halfFrame = 1.0 / 60.0
            result.append(.init(
                id: "\(start)-\(end)",
                startRow: start,
                endRow: end,
                startTS: max(0, rows[start].videoTS - halfFrame),
                endTS: rows[end].videoTS + halfFrame
            ))
        }
        return result
    }

    func editEatingEdge(segmentID: String, edge: EatingEdge, to time: Double, recordUndo: Bool = true) {
        statusMessage = "Eating intervals are read-only evidence here; complete coarse review before generating candidates"
    }

    func eatingSegment(enclosing chew: ReviewChew) -> EatingSegment? {
        eatingSegments.first { $0.startTS <= chew.peakTS && $0.endTS >= chew.peakTS }
    }
}
