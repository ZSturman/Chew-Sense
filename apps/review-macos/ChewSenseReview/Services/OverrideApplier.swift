import Foundation

/// In-memory preview only. Review does not persist motion-row labels; Python
/// finalization materializes them from the immutable review revision.
enum OverrideApplier {
    static func apply(_ draft: ReviewDraft, candidates: CandidateBundle, to base: [AlignedLabel]) -> [AlignedLabel] {
        guard !base.isEmpty else { return [] }
        var rows = base

        // Remove any legacy derived event/phase truth before previewing v2.
        for index in rows.indices {
            rows[index].chewID = ""
            rows[index].chewPhase = "idle"
            rows[index].biteEvent = 0
            rows[index].biteID = ""
        }

        let decisions = Dictionary(uniqueKeysWithValues: draft.chewDecisions.map { ($0.candidateID, $0) })
        for candidate in candidates.chews {
            let decision = decisions[candidate.id]
            if decision?.disposition == .rejected ||
                decision?.disposition == .unobservable ||
                decision?.disposition == .uncertain {
                continue
            }
            let anchors = resolvedAnchors(candidate: candidate, decision: decision)
            writeChew(
                id: candidate.id,
                start: anchors.start,
                peak: anchors.peak,
                end: anchors.end,
                confidence: candidate.confidence,
                rows: &rows
            )
        }
        for chew in draft.addedChews {
            writeChew(
                id: chew.id,
                start: chew.startClosedTS,
                peak: chew.openPeakTS,
                end: chew.endClosedTS,
                confidence: chew.confidence,
                rows: &rows
            )
        }

        let entryDecisions = Dictionary(uniqueKeysWithValues: draft.foodEntryDecisions.map { ($0.candidateID, $0) })
        for candidate in candidates.foodEntries {
            let decision = entryDecisions[candidate.id]
            guard decision?.disposition != .rejected,
                  decision?.disposition != .unobservable,
                  decision?.disposition != .uncertain else { continue }
            writeFoodEntry(
                id: candidate.id,
                ts: decision?.foodEntryTS ?? candidate.foodEntryTS,
                confidence: candidate.confidence,
                rows: &rows
            )
        }
        for entry in draft.addedFoodEntries {
            writeFoodEntry(id: entry.id, ts: entry.foodEntryTS, confidence: entry.confidence, rows: &rows)
        }
        return rows
    }

    private static func resolvedAnchors(
        candidate: CandidateBundle.Chew,
        decision: ReviewDraft.ChewDecision?
    ) -> (start: Double, peak: Double, end: Double) {
        guard decision?.disposition == .edited else {
            return (candidate.startClosedTS, candidate.openPeakTS, candidate.endClosedTS)
        }
        return (
            decision?.startClosedTS ?? candidate.startClosedTS,
            decision?.openPeakTS ?? candidate.openPeakTS,
            decision?.endClosedTS ?? candidate.endClosedTS
        )
    }

    private static func writeChew(
        id: String,
        start: Double,
        peak: Double,
        end: Double,
        confidence: Double,
        rows: inout [AlignedLabel]
    ) {
        for index in rows.indices {
            let time = rows[index].videoTS
            if time >= start, time <= peak {
                rows[index].chewID = id
                rows[index].chewPhase = "opening"
                rows[index].videoConfidence = confidence
            } else if time > peak, time <= end {
                rows[index].chewID = id
                rows[index].chewPhase = "closing"
                rows[index].videoConfidence = confidence
            }
        }
    }

    private static func writeFoodEntry(
        id: String,
        ts: Double,
        confidence: Double,
        rows: inout [AlignedLabel]
    ) {
        guard let nearest = rows.indices.min(by: {
            abs(rows[$0].videoTS - ts) < abs(rows[$1].videoTS - ts)
        }) else { return }
        guard abs(rows[nearest].videoTS - ts) <= 0.04 else { return }
        rows[nearest].biteEvent = 1
        rows[nearest].biteID = id
        rows[nearest].videoConfidence = confidence
    }
}

enum ReviewContractValidator {
    static func validate(_ draft: ReviewDraft, candidates: CandidateBundle, candidateHash: String) throws {
        try draft.validateLineage(bundle: candidates, computedHash: candidateHash)
        if let context = draft.reviewContext {
            guard ["assisted", "blind"].contains(context.mode), context.activeSeconds.isFinite, context.activeSeconds >= 0 else {
                throw ReviewDraftError.invalidDisposition("experiment context")
            }
            if context.mode == "blind", !candidates.chews.isEmpty || !candidates.foodEntries.isEmpty {
                throw ReviewDraftError.invalidDisposition("blind proposals must be empty")
            }
            for span in context.excludedSpans {
                guard span.startTS.isFinite, span.endTS.isFinite, span.startTS >= 0, span.startTS < span.endTS,
                      ["uncertain", "unobservable"].contains(span.disposition) else { throw ReviewDraftError.invalidAnchors("excluded span") }
            }
        }

        let candidateChewIDs = Set(candidates.chews.map(\.id))
        var seenChews = Set<String>()
        for decision in draft.chewDecisions {
            guard candidateChewIDs.contains(decision.candidateID) else {
                throw ReviewDraftError.missingCandidate(decision.candidateID)
            }
            guard seenChews.insert(decision.candidateID).inserted else {
                throw ReviewDraftError.duplicateDecision(decision.candidateID)
            }
            guard decision.disposition != .added else {
                throw ReviewDraftError.invalidDisposition(decision.candidateID)
            }
            if decision.disposition == .edited {
                guard let start = decision.startClosedTS,
                      let peak = decision.openPeakTS,
                      let end = decision.endClosedTS,
                      start < peak, peak < end else {
                    throw ReviewDraftError.invalidAnchors(decision.candidateID)
                }
            } else if decision.startClosedTS != nil ||
                        decision.openPeakTS != nil ||
                        decision.endClosedTS != nil {
                throw ReviewDraftError.invalidAnchors(decision.candidateID)
            }
        }

        let candidateEntryIDs = Set(candidates.foodEntries.map(\.id))
        var seenEntries = Set<String>()
        for decision in draft.foodEntryDecisions {
            guard candidateEntryIDs.contains(decision.candidateID) else {
                throw ReviewDraftError.missingCandidate(decision.candidateID)
            }
            guard seenEntries.insert(decision.candidateID).inserted else {
                throw ReviewDraftError.duplicateDecision(decision.candidateID)
            }
            guard decision.disposition != .added else {
                throw ReviewDraftError.invalidDisposition(decision.candidateID)
            }
            if decision.disposition == .edited, decision.foodEntryTS == nil {
                throw ReviewDraftError.invalidAnchors(decision.candidateID)
            } else if decision.disposition != .edited, decision.foodEntryTS != nil {
                throw ReviewDraftError.invalidAnchors(decision.candidateID)
            }
        }

        var allChewIDs = candidateChewIDs
        for chew in draft.addedChews {
            guard chew.disposition == .added,
                  chew.startClosedTS < chew.openPeakTS,
                  chew.openPeakTS < chew.endClosedTS,
                  chew.confidence.isFinite,
                  (0...1).contains(chew.confidence),
                  allChewIDs.insert(chew.id).inserted else {
                throw ReviewDraftError.invalidAnchors(chew.id)
            }
        }
        var allFoodEntryIDs = candidateEntryIDs
        for entry in draft.addedFoodEntries {
            guard entry.disposition == .added,
                  entry.foodEntryTS.isFinite,
                  entry.confidence.isFinite,
                  (0...1).contains(entry.confidence),
                  allFoodEntryIDs.insert(entry.id).inserted else {
                throw ReviewDraftError.invalidDisposition(entry.id)
            }
        }

        let resolved = resolvedChews(draft, candidates: candidates).sorted {
            if $0.start == $1.start { return $0.id < $1.id }
            return $0.start < $1.start
        }
        var previousEnd = -Double.infinity
        for chew in resolved {
            guard chew.start >= previousEnd else { throw ReviewDraftError.overlap(chew.id) }
            previousEnd = chew.end
        }
    }

    private static func resolvedChews(
        _ draft: ReviewDraft,
        candidates: CandidateBundle
    ) -> [(id: String, start: Double, peak: Double, end: Double)] {
        let decisions = Dictionary(uniqueKeysWithValues: draft.chewDecisions.map { ($0.candidateID, $0) })
        var result: [(String, Double, Double, Double)] = []
        for candidate in candidates.chews {
            let decision = decisions[candidate.id]
            guard decision?.disposition != .rejected,
                  decision?.disposition != .unobservable,
                  decision?.disposition != .uncertain else { continue }
            if decision?.disposition == .edited {
                result.append((
                    candidate.id,
                    decision?.startClosedTS ?? candidate.startClosedTS,
                    decision?.openPeakTS ?? candidate.openPeakTS,
                    decision?.endClosedTS ?? candidate.endClosedTS
                ))
            } else {
                result.append((candidate.id, candidate.startClosedTS, candidate.openPeakTS, candidate.endClosedTS))
            }
        }
        result += draft.addedChews.map { ($0.id, $0.startClosedTS, $0.openPeakTS, $0.endClosedTS) }
        return result
    }
}
