import Foundation

/// What the quality monitor can currently observe. `nil` means "not measured".
nonisolated struct QualitySnapshot: Equatable, Sendable {
    var faceVisible: Bool?
    var jawVisible: Bool?
    var brightness: Double?
    var phoneMotionG: Double?
    var motionFresh: Bool
    var captureRunning: Bool

    static let good = QualitySnapshot(faceVisible: true, jawVisible: true, brightness: 0.5,
                                      phoneMotionG: 0, motionFresh: true, captureRunning: true)
}

nonisolated enum QualityIssue: String, CaseIterable, Sendable {
    case captureInterrupted = "capture_interrupted"
    case motionMissing = "motion_missing"
    case faceMissing = "face_missing"
    case jawNotVisible = "jaw_not_visible"
    case lowLight = "low_light"
    case phoneMoving = "phone_moving"

    var prompt: String {
        switch self {
        case .captureInterrupted: "Recording interrupted — keep the app open"
        case .motionMissing: "Reconnect your AirPods"
        case .faceMissing: "Bring your face back into frame"
        case .jawNotVisible: "Move back so your jaw is visible"
        case .lowLight: "Too dark to see your face — tap the bulb for Screen light"
        case .phoneMoving: "Keep the iPhone still"
        }
    }
}

/// Drives a guided experiment on the recording's host-monotonic clock.
///
/// Scheduled time is never treated as evidence: the countdown advances only
/// while the activity's required evidence is observable (policy `pause`),
/// marks uncertain time (policy `mark`), or restarts the activity (policy
/// `repeat`). Every transition is logged with its actual host time. Media
/// recording itself continues throughout; only the instruction timer pauses.
nonisolated final class GuidedRunEngine {
    enum Phase: Equatable {
        case notStarted
        case leadIn
        case waitingForEvidence(index: Int)
        case running(index: Int)
        case paused(index: Int)
        case finished
        case abandoned
    }

    struct Event {
        var type: String
        var hostTime: Double
        var activityIndex: Int?
        var attempt: Int?
        var detail: [String: Any] = [:]
    }

    struct Attempt {
        var activityID: String
        var activityIndex: Int
        var attempt: Int
        var presented: Double
        var started: Double?
        var ended: Double?
        var outcome: String = "interrupted"
        var pausedSeconds: Double = 0
        var validSeconds: Double = 0
        var flags: Set<String> = []
    }

    struct InvalidInterval {
        var start: Double
        var end: Double?
        var reasons: Set<String>
    }

    struct Validation {
        struct Check { var id: String; var passed: Bool; var message: String; var activityIDs: [String] = [] }
        var checks: [Check]
        var repeatActivityIDs: [String]
        var complete: Bool { checks.allSatisfy(\.passed) }
    }

    enum Effect: Equatable { case activityChanged(Int), paused([QualityIssue]), resumed, restarted(Int), finished }

    // Configuration
    let definition: ExperimentDefinition
    /// Indices into `definition.activities` that this run performs, in order.
    let plan: [Int]
    let issueDebounce: Double = 0.75
    let restoreDebounce: Double = 0.5
    let maxPauseBeforeRepeat: Double = 15

    // State
    private(set) var phase: Phase = .notStarted
    private(set) var events: [Event] = []
    private(set) var attempts: [Attempt] = []
    private(set) var invalidIntervals: [InvalidInterval] = []
    private(set) var activeIssues: [QualityIssue] = []
    private(set) var planPosition = 0
    private var leadInEnds = 0.0
    private var elapsedInActivity = 0.0
    private var lastTick: Double?
    private var badSince: Double?
    private var goodSince: Double?
    private var pausedAt: Double?
    private var openInvalid: Int?
    private(set) var startedAt: Double?
    // Quality accounting over activities that require the face.
    private(set) var faceRequiredSeconds = 0.0
    private(set) var faceVisibleSeconds = 0.0
    private(set) var lowLightSeconds = 0.0
    private(set) var phoneMovingSeconds = 0.0
    private(set) var observedSeconds = 0.0

    init(definition: ExperimentDefinition, onlyActivityIDs: [String]? = nil) {
        self.definition = definition
        if let only = onlyActivityIDs, !only.isEmpty {
            let wanted = Set(only)
            let markers = definition.activities.indices.filter { definition.activities[$0].syncMarker }
            var indices = definition.activities.indices.filter { wanted.contains(definition.activities[$0].id) }
            // Bracket repeated segments with the definition's first and last sync markers.
            if let first = markers.first, !indices.contains(first) { indices.insert(first, at: 0) }
            if let last = markers.last, !indices.contains(last) { indices.append(last) }
            plan = indices
        } else {
            plan = Array(definition.activities.indices)
        }
    }

    var currentIndex: Int? {
        switch phase {
        case .waitingForEvidence(let i), .running(let i), .paused(let i): i
        default: nil
        }
    }

    var currentActivity: ExperimentDefinition.Activity? { currentIndex.map { definition.activities[$0] } }

    var nextActivity: ExperimentDefinition.Activity? {
        let next = phase == .leadIn ? 0 : planPosition + 1
        return next < plan.count ? definition.activities[plan[next]] : nil
    }

    func remainingSeconds(at now: Double) -> Double {
        switch phase {
        case .leadIn: return max(0, leadInEnds - now)
        case .waitingForEvidence(let i), .running(let i), .paused(let i):
            return max(0, definition.activities[i].durationS - elapsedInActivity)
        default: return 0
        }
    }

    var plannedSeconds: Double { plan.reduce(0) { $0 + definition.activities[$1].durationS } }

    var progress: Double {
        guard plannedSeconds > 0 else { return 0 }
        let done = plan.prefix(planPosition).reduce(0) { $0 + definition.activities[$1].durationS }
        let current = currentIndex == nil ? 0 : elapsedInActivity
        return phase == .finished ? 1 : min(1, (done + current) / plannedSeconds)
    }

    var isTimerPaused: Bool {
        if case .paused = phase { return true }
        if case .waitingForEvidence = phase { return true }
        return false
    }

    // MARK: - Lifecycle

    func start(at now: Double) {
        guard phase == .notStarted else { return }
        startedAt = now
        log("user_acknowledged", now, detail: ["action": "start_experiment"])
        if definition.leadInS > 0 {
            phase = .leadIn
            leadInEnds = now + definition.leadInS
            log("lead_in_started", now, detail: ["seconds": definition.leadInS])
        } else {
            enterActivity(at: now)
        }
        lastTick = now
    }

    func abandon(at now: Double) {
        guard phase != .finished, phase != .abandoned else { return }
        closeInvalid(at: now)
        if let index = currentIndex, var attempt = attempts.last, attempt.activityIndex == index, attempt.ended == nil {
            attempt.ended = attempt.started == nil ? nil : now
            attempt.outcome = "abandoned"
            attempts[attempts.count - 1] = attempt
        }
        phase = .abandoned
        log("experiment_abandoned", now)
    }

    /// Records events that happen outside the timer, e.g. AirPods disconnects.
    func note(_ type: String, at now: Double, detail: [String: Any] = [:]) {
        log(type, now, activityIndex: currentIndex, attempt: attempts.last?.attempt, detail: detail)
    }

    @discardableResult
    func tick(at now: Double, quality: QualitySnapshot) -> [Effect] {
        guard let previous = lastTick else { return [] }
        let dt = max(0, now - previous)
        lastTick = now
        var effects: [Effect] = []
        switch phase {
        case .leadIn:
            if now + 1e-6 >= leadInEnds {
                effects += enterActivity(at: now)
                if case .waitingForEvidence(let next) = phase {
                    effects += startIfObservable(index: next, at: now, quality: quality)
                }
            }
        case .waitingForEvidence(let index):
            account(dt: dt, activity: definition.activities[index], quality: quality, counting: false)
            effects += startIfObservable(index: index, at: now, quality: quality)
        case .running(let index):
            let activity = definition.activities[index]
            let issues = requiredIssues(for: activity, quality: quality)
            account(dt: dt, activity: activity, quality: quality, counting: true)
            if issues.isEmpty {
                badSince = nil
                if openInvalid != nil { closeInvalid(at: now); log("quality_restored", now, activityIndex: index, attempt: attempts.last?.attempt) }
                activeIssues = []
                elapsedInActivity += dt
                attempts[attempts.count - 1].validSeconds += dt
            } else {
                badSince = badSince ?? (now - dt)
                activeIssues = issues
                openInvalidIfNeeded(at: badSince ?? now, reasons: issues)
                if activity.onQualityLoss == .mark {
                    elapsedInActivity += dt
                    attempts[attempts.count - 1].flags.formUnion(issues.map(\.rawValue))
                } else if now - (badSince ?? now) >= issueDebounce {
                    // Time already counted during the debounce window was not valid evidence.
                    let invalidCounted = min(elapsedInActivity, now - (badSince ?? now))
                    elapsedInActivity -= invalidCounted
                    attempts[attempts.count - 1].validSeconds = max(0, attempts[attempts.count - 1].validSeconds - invalidCounted)
                    phase = .paused(index: index)
                    pausedAt = now
                    goodSince = nil
                    attempts[attempts.count - 1].flags.formUnion(issues.map(\.rawValue))
                    log("activity_paused", now, activityIndex: index, attempt: attempts.last?.attempt,
                        detail: ["reasons": issues.map(\.rawValue)])
                    log("quality_failed", badSince ?? now, activityIndex: index, attempt: attempts.last?.attempt,
                        detail: ["reasons": issues.map(\.rawValue)], ordered: false)
                    effects.append(.paused(issues))
                } else {
                    elapsedInActivity += dt  // provisional until the debounce decides
                    attempts[attempts.count - 1].validSeconds += dt
                }
            }
            if case .running = phase, elapsedInActivity + 1e-6 >= activity.durationS {
                effects += completeActivity(at: now)
                // Start the next activity in the same tick when its evidence is present.
                if case .waitingForEvidence(let next) = phase {
                    effects += startIfObservable(index: next, at: now, quality: quality)
                }
            }
        case .paused(let index):
            let activity = definition.activities[index]
            let issues = requiredIssues(for: activity, quality: quality)
            account(dt: dt, activity: activity, quality: quality, counting: true)
            attempts[attempts.count - 1].pausedSeconds += dt
            if issues.isEmpty {
                goodSince = goodSince ?? now
                if now - (goodSince ?? now) >= restoreDebounce {
                    closeInvalid(at: goodSince ?? now)
                    log("quality_restored", goodSince ?? now, activityIndex: index, attempt: attempts.last?.attempt,
                        ordered: false)
                    let pausedFor = now - (pausedAt ?? now)
                    if activity.onQualityLoss == .repeatActivity || pausedFor > maxPauseBeforeRepeat {
                        effects += restartActivity(at: now, reason: pausedFor > maxPauseBeforeRepeat
                                                   ? "pause_exceeded_limit" : "repeat_policy")
                    } else {
                        phase = .running(index: index)
                        badSince = nil
                        activeIssues = []
                        log("activity_resumed", now, activityIndex: index, attempt: attempts.last?.attempt)
                        effects.append(.resumed)
                    }
                }
            } else {
                goodSince = nil
                activeIssues = issues
                if let open = openInvalid { invalidIntervals[open].reasons.formUnion(issues.map(\.rawValue)) }
            }
        case .notStarted, .finished, .abandoned:
            break
        }
        return effects
    }

    // MARK: - Transitions

    @discardableResult
    private func enterActivity(at now: Double) -> [Effect] {
        guard planPosition < plan.count else {
            phase = .finished
            log("experiment_completed", now)
            return [.finished]
        }
        let index = plan[planPosition]
        let number = (attempts.filter { $0.activityIndex == index }.map(\.attempt).max() ?? 0) + 1
        attempts.append(Attempt(activityID: definition.activities[index].id, activityIndex: index,
                                attempt: number, presented: now))
        elapsedInActivity = 0
        badSince = nil
        goodSince = nil
        phase = .waitingForEvidence(index: index)
        log("instruction_presented", now, activityIndex: index, attempt: number,
            detail: ["instruction": definition.activities[index].instruction])
        return [.activityChanged(index)]
    }

    private func startIfObservable(index: Int, at now: Double, quality: QualitySnapshot) -> [Effect] {
        let issues = requiredIssues(for: definition.activities[index], quality: quality)
        guard issues.isEmpty else {
            goodSince = nil
            activeIssues = issues
            return []
        }
        goodSince = goodSince ?? now
        // A fresh instruction starts at once; after waiting, require stable evidence.
        guard activeIssues.isEmpty || now - (goodSince ?? now) >= restoreDebounce else { return [] }
        beginCountdown(at: now)
        return [.resumed]
    }

    private func beginCountdown(at now: Double) {
        guard let index = currentIndex else { return }
        attempts[attempts.count - 1].started = now
        phase = .running(index: index)
        activeIssues = []
        log("activity_started", now, activityIndex: index, attempt: attempts.last?.attempt)
    }

    private func completeActivity(at now: Double) -> [Effect] {
        guard let index = currentIndex else { return [] }
        closeInvalid(at: now)
        var attempt = attempts[attempts.count - 1]
        attempt.ended = now
        // Uncertain only when observable time fell short (policy `mark`); a paused
        // activity that accumulated its full observable duration is complete,
        // with its quality flags and invalid interval still recorded.
        let duration = definition.activities[index].durationS
        attempt.outcome = attempt.validSeconds + 0.05 >= duration ? "completed" : "completed_with_uncertainty"
        attempts[attempts.count - 1] = attempt
        if attempt.outcome == "completed_with_uncertainty" {
            log("activity_marked_uncertain", now, activityIndex: index, attempt: attempt.attempt,
                detail: ["reasons": attempt.flags.sorted()])
        }
        log("activity_completed", now, activityIndex: index, attempt: attempt.attempt)
        planPosition += 1
        return enterActivity(at: now)
    }

    private func restartActivity(at now: Double, reason: String) -> [Effect] {
        guard let index = currentIndex else { return [] }
        var attempt = attempts[attempts.count - 1]
        attempt.ended = now
        attempt.outcome = "interrupted"
        attempts[attempts.count - 1] = attempt
        log("activity_restarted", now, activityIndex: index, attempt: attempt.attempt, detail: ["reason": reason])
        _ = enterActivity(at: now)
        return [.restarted(index)]
    }

    // MARK: - Quality

    func requiredIssues(for activity: ExperimentDefinition.Activity, quality: QualitySnapshot) -> [QualityIssue] {
        var issues: [QualityIssue] = []
        if !quality.captureRunning { return [.captureInterrupted] }
        if activity.requiresMotion && !quality.motionFresh { issues.append(.motionMissing) }
        if activity.requiresFace {
            // Dim light alone never blocks: while the face is still found it is
            // usable, and the dim time is reported as low_light_fraction.
            if quality.faceVisible == false {
                if let brightness = quality.brightness, brightness < definition.camera.minBrightness {
                    issues.append(.lowLight)
                }
                issues.append(.faceMissing)
            } else if definition.camera.jawRequired && quality.jawVisible == false { issues.append(.jawNotVisible) }
            if let motion = quality.phoneMotionG, motion > definition.camera.maxPhoneMotionG {
                issues.append(.phoneMoving)
            }
        }
        return issues
    }

    private func account(dt: Double, activity: ExperimentDefinition.Activity, quality: QualitySnapshot, counting: Bool) {
        observedSeconds += dt
        guard activity.requiresFace else { return }
        faceRequiredSeconds += dt
        if quality.faceVisible == true { faceVisibleSeconds += dt }
        if let b = quality.brightness, b < definition.camera.minBrightness { lowLightSeconds += dt }
        if let m = quality.phoneMotionG, m > definition.camera.maxPhoneMotionG { phoneMovingSeconds += dt }
    }

    private func openInvalidIfNeeded(at start: Double, reasons: [QualityIssue]) {
        if let open = openInvalid {
            invalidIntervals[open].reasons.formUnion(reasons.map(\.rawValue))
        } else {
            invalidIntervals.append(InvalidInterval(start: start, end: nil, reasons: Set(reasons.map(\.rawValue))))
            openInvalid = invalidIntervals.count - 1
        }
    }

    private func closeInvalid(at now: Double) {
        guard let open = openInvalid else { return }
        invalidIntervals[open].end = max(invalidIntervals[open].start, now)
        openInvalid = nil
    }

    private func log(_ type: String, _ time: Double, activityIndex: Int? = nil, attempt: Int? = nil,
                     detail: [String: Any] = [:], ordered: Bool = true) {
        let event = Event(type: type, hostTime: time, activityIndex: activityIndex, attempt: attempt, detail: detail)
        if ordered || events.isEmpty {
            events.append(event)
        } else {
            // Back-dated observations (the first bad sample) keep the log time-ordered.
            let position = events.lastIndex(where: { $0.hostTime <= time }).map { $0 + 1 } ?? 0
            events.insert(event, at: position)
        }
    }

    // MARK: - Validation

    func validate(motionCoverage: Double?) -> Validation {
        var checks: [Validation.Check] = []
        var repeatIDs: [String] = []
        let completion = definition.completion
        for index in plan {
            let activity = definition.activities[index]
            let done = attempts.filter { $0.activityIndex == index && $0.ended != nil && $0.outcome.hasPrefix("completed") }
            let best = done.map { $0.validSeconds / activity.durationS }.max() ?? 0
            let threshold = max(activity.minValidFraction, completion.minActivityValidFraction)
            if done.isEmpty {
                checks.append(.init(id: "activity_attempted", passed: false,
                                    message: "“\(activity.instruction)” was not completed.", activityIDs: [activity.id]))
                if activity.repeatable { repeatIDs.append(activity.id) }
            } else if best + 1e-9 < threshold {
                checks.append(.init(id: "activity_evidence", passed: false,
                                    message: "“\(activity.instruction)” had only \(Int(best * 100))% observable time.",
                                    activityIDs: [activity.id]))
                if activity.repeatable { repeatIDs.append(activity.id) }
            }
        }
        let markers = plan.filter { definition.activities[$0].syncMarker }.filter { index in
            attempts.contains { $0.activityIndex == index && $0.outcome == "completed" }
        }.count
        let neededMarkers = min(completion.requiredSyncMarkers, plan.filter { definition.activities[$0].syncMarker }.count)
        checks.append(.init(id: "sync_markers", passed: markers >= neededMarkers,
                            message: "\(markers) of \(neededMarkers) synchronization nods were captured cleanly."))
        if faceRequiredSeconds > 0 {
            let fraction = faceVisibleSeconds / faceRequiredSeconds
            checks.append(.init(id: "face_visible", passed: fraction >= completion.minFaceVisibleFraction,
                                message: "Face visible \(Int(fraction * 100))% of the time it was needed."))
        }
        if let coverage = motionCoverage {
            checks.append(.init(id: "motion_coverage", passed: coverage >= completion.minMotionCoverage,
                                message: "AirPods motion covered \(Int(coverage * 100))% of the recording."))
        } else {
            checks.append(.init(id: "motion_coverage", passed: false, message: "No AirPods motion was recorded."))
        }
        if phase == .abandoned {
            checks.append(.init(id: "finished", passed: false, message: "The experiment was ended early."))
        }
        return Validation(checks: checks.filter { !$0.passed } + checks.filter(\.passed), repeatActivityIDs: repeatIDs)
    }

    // MARK: - Sidecar

    func sidecar(sessionID: String, experimentSHA256: String, rawDefinition: Data, catalogVersion: Int?,
                 videoClockOrigin: Double, motionCoverage: Double?, repeatOf: String?) throws -> Data {
        let validation = validate(motionCoverage: motionCoverage)
        let definitionObject = try JSONSerialization.jsonObject(with: rawDefinition)
        func clean(_ value: Double?) -> Any { value.map { $0 as Any } ?? NSNull() }
        let payload: [String: Any] = [
            "schema_version": "guided-session/1",
            "session_id": sessionID,
            "experiment_id": definition.experimentID,
            "experiment_version": definition.version,
            "definition_sha256": experimentSHA256,
            "definition": definitionObject,
            "catalog_version": catalogVersion.map { $0 as Any } ?? NSNull(),
            "time_domain": "host_monotonic",
            "video_clock_origin_host_time_s": videoClockOrigin,
            "events": events.map { event -> [String: Any] in
                var item: [String: Any] = ["type": event.type, "host_time_s": event.hostTime, "detail": event.detail]
                if let i = event.activityIndex { item["activity_index"] = i }
                if let a = event.attempt { item["attempt"] = a }
                return item
            },
            "attempts": attempts.map { a -> [String: Any] in
                [
                    "activity_id": a.activityID, "activity_index": a.activityIndex, "attempt": a.attempt,
                    "presented_host_s": a.presented, "started_host_s": clean(a.started),
                    "ended_host_s": clean(a.started == nil ? nil : a.ended), "outcome": a.outcome,
                    "paused_seconds": a.pausedSeconds, "valid_seconds": a.validSeconds,
                    "quality_flags": a.flags.sorted(),
                ]
            },
            "invalid_intervals": invalidIntervals.map { i -> [String: Any] in
                ["start_host_s": i.start, "end_host_s": i.end ?? i.start, "reasons": i.reasons.sorted()]
            },
            "quality": [
                "samples": Int(observedSeconds * 10),
                "face_visible_fraction": faceRequiredSeconds > 0 ? faceVisibleSeconds / faceRequiredSeconds : NSNull(),
                "low_light_fraction": faceRequiredSeconds > 0 ? lowLightSeconds / faceRequiredSeconds : NSNull(),
                "phone_motion_fraction": faceRequiredSeconds > 0 ? phoneMovingSeconds / faceRequiredSeconds : NSNull(),
                "motion_coverage_fraction": clean(motionCoverage),
            ] as [String: Any],
            "validation": [
                "complete": validation.complete,
                "checks": validation.checks.map {
                    ["id": $0.id, "passed": $0.passed, "message": $0.message, "activity_ids": $0.activityIDs]
                },
                "repeat_activity_ids": validation.repeatActivityIDs,
            ] as [String: Any],
            "repeat_of_session_id": repeatOf.map { $0 as Any } ?? NSNull(),
            "repeated_activity_ids": plan.count == definition.activities.count
                ? [] : plan.map { definition.activities[$0].id },
            "labels_status": "intended_only_not_verified",
        ]
        return try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
    }
}
