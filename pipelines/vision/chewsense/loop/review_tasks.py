"""Decide where a human is needed and point them at the useful spans.

Priorities come from disagreement between instructions, proposals and
recording quality. They are review *hints*; nothing here accepts a proposal.
Evaluation sessions are reviewed blind first, and their assisted review stays
locked until the blind review is frozen.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..canonical import load_candidate_artifact, load_review_draft
from .store import read_json

LOW_CONFIDENCE = 0.5


def _latest_candidates(derived: Path):
    paths = sorted(derived.glob("candidates-v[0-9][0-9][0-9][0-9].json"))
    return load_candidate_artifact(paths[-1]) if paths else None


def _review_state(session: Path) -> str:
    annotations = session / "annotations"
    if (annotations / "canonical_events.json").exists():
        return "finalized"
    draft_path = annotations / "review-draft.json"
    if not draft_path.exists():
        return "not_started"
    draft = load_review_draft(draft_path)
    if draft.verified and draft.full_video_pass_completed:
        return "verified_draft"
    return "in_progress"


def review_state(workspace: Path, session_id: str) -> str:
    session = workspace / "sessions/raw" / session_id
    return _review_state(session) if session.exists() else "absent"


def _overlaps(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


def priority_segments(candidates, intended: dict[str, Any] | None) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    chews = list(candidates.chews) if candidates else []
    intervals = (intended or {}).get("intervals", [])
    for interval in intervals:
        start, end = interval["start_ts"], interval["end_ts"]
        inside = [c for c in chews if _overlaps(c.start_closed_ts, c.end_closed_ts, start, end)]
        if interval["intended_label"] == "chewing" and not inside:
            segments.append(dict(start_ts=start, end_ts=end, score=3,
                                 reason="instructed chewing but no proposals: check for missed chews"))
        elif interval["intended_label"] == "not_chewing" and inside and not interval["sync_marker"]:
            segments.append(dict(start_ts=start, end_ts=end, score=3,
                                 reason=f"{len(inside)} proposals during instructed "
                                        f"{interval['kind'].replace('_', ' ')}: possible false positives"))
        if interval["sync_marker"]:
            segments.append(dict(start_ts=start, end_ts=end, score=1,
                                 reason="sync marker: inspect for clock confirmation; exclude from chewing"))
    for chew in chews:
        if chew.confidence < LOW_CONFIDENCE:
            segments.append(dict(start_ts=chew.start_closed_ts, end_ts=chew.end_closed_ts, score=2,
                                 reason=f"low-confidence proposal ({chew.confidence:.2f})",
                                 candidate_id=chew.id))
    for invalid in (intended or {}).get("invalid_intervals", []):
        segments.append(dict(start_ts=invalid["start_ts"], end_ts=invalid["end_ts"], score=2,
                             reason="recording quality loss (" + ", ".join(invalid["reasons"])
                                    + "): mark unobservable if evidence is missing"))
    segments.sort(key=lambda s: (-s["score"], s["start_ts"]))
    return segments


def tasks_for_session(record: dict[str, Any], assisted: Path, blind: Path,
                      reports: Path) -> list[dict[str, Any]]:
    session_id = record["session_id"]
    role = record.get("role")
    if record.get("guided") and not record.get("device_validation_complete"):
        return [dict(session_id=session_id, kind="recording_incomplete", workspace=str(assisted),
                     priority=4, title="Incomplete recording retained for diagnosis",
                     detail="This shortened/failed take does not count toward training or evaluation. Record a full experiment when ready.",
                     blocked=True, segments=[])]
    if role == "diagnostic" and record.get("experiment_id") == "timing-check":
        draft = read_json(assisted / "sessions/raw" / session_id / "annotations/review-draft.json", {})
        if (draft.get("review_context") or {}).get("sync_verified"):
            return []
        return [dict(session_id=session_id, kind="synchronization_review", workspace=str(assisted),
                     priority=0, title="Confirm the three timing markers",
                     detail="Inspect all three nods and motion traces. Automatic peak matching may be ambiguous; leave timing unconfirmed until correspondence meets the budget. No individual chew labels are needed here.",
                     blocked=False, segments=[])]
    intended = read_json(reports / "intended-activity.json")
    sync = read_json(reports / "sync-assessment.json")
    tasks: list[dict[str, Any]] = []
    blind_state = review_state(blind, session_id) if role == "evaluation" else None
    if role == "evaluation" and blind_state not in {"finalized", "verified_draft"}:
        blind_session = blind / "sessions/raw" / session_id
        coarse_done = (blind_session / "annotations/coarse_labels.json").exists()
        tasks.append(dict(
            session_id=session_id, kind="blind_review", workspace=str(blind), priority=0,
            title="Independent blind review",
            detail=("Watch the original video and add every chew; predictions are hidden."
                    if coarse_done else
                    "Coarse review needed first (Collect or review-coarse), then add every chew."),
            blocked=not coarse_done, segments=[]))
    assisted_state = review_state(assisted, session_id)
    if assisted_state in {"finalized", "verified_draft"}:
        return tasks
    candidates = _latest_candidates(assisted / "derived/vision" / session_id)
    if candidates is None:
        return tasks
    locked = role == "evaluation" and blind_state not in {"finalized", "verified_draft"}
    segments = priority_segments(candidates, intended)
    sync_note = None
    if sync:
        sync_note = {
            "consistent": "Automatic marker check is consistent; confirm the clock if the video agrees.",
            "exceeds_budget": "Automatic marker residuals exceed the budget; leave sync unconfirmed.",
        }.get(sync.get("status"), "Automatic marker check inconclusive; inspect the nod markers.")
    tasks.append(dict(
        session_id=session_id, kind="assisted_review", workspace=str(assisted),
        priority=1 if not locked else 3,
        title="Review proposals" + (" (locked until blind review is frozen)" if locked else ""),
        detail=f"{len(candidates.chews)} chew proposals; {len(segments)} highlighted spans.",
        blocked=locked, segments=segments[:40], sync_note=sync_note,
        coarse_review_required=candidates.requires_full_coarse_review))
    return tasks
