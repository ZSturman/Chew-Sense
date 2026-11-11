"""Persistent session-level development/test assignment.

The assignment file is created once and only ever grows.  A session never
changes split, so the test set stays untouched by model development:

* loop sessions inherit their loop role (``evaluation`` -> test,
  ``training`` -> development; ``diagnostic`` never reaches this module);
* on first creation, the chronologically newest quarter of each stratum
  (recordings with chewing / negative-only recordings) becomes test, which
  also tests generalization forward in time;
* sessions first seen later are assigned by a stable hash of their ID.

Only one wearer exists, so participant-level holdout is impossible; the
report says so instead of implying cross-person generalization.

Recordings are not independent of each other either.  Several were captured
back to back in one *sitting* (same meal, same AirPods fit, same posture):
the four ``Eating-20251203-06*`` recordings are one meal.  Development folds
therefore keep a sitting together by default, and uncertainty is resampled
over sittings.  The locked ``splits.json`` itself is per recording and is
never rewritten; test recordings that share a sitting with development data
are listed in every run instead (:func:`shared_sittings`).
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .dataset import EatingSession


SPLIT_VERSION = 1
TEST = "test"
DEV = "dev"
CV_GROUPINGS = ("sitting", "session")
SITTING_GAP_MINUTES = 45.0


def _stratum(session: EatingSession) -> str:
    return "contains_chewing" if session.has_chewing else "negative_only"


def _hash_fraction(session_id: str) -> float:
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def assign_splits(
    sessions: Sequence[EatingSession],
    path: Path,
    *,
    test_fraction: float = 0.25,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    if path.exists():
        payload = json.loads(path.read_text())
        if payload.get("version") != SPLIT_VERSION:
            raise ValueError(f"refusing to reinterpret split file version {payload.get('version')}")
        assignments: dict[str, dict[str, Any]] = payload["assignments"]
        first_creation = False
    else:
        payload = {
            "version": SPLIT_VERSION,
            "created_at": now,
            "policy": {
                "unit": "session",
                "test_fraction": test_fraction,
                "initial_rule": "newest quarter per stratum by capture time; loop roles honoured",
                "later_rule": "stable SHA-256 hash of session_id; loop roles honoured",
                "immutable": True,
            },
        }
        assignments = {}
        first_creation = True

    def record(session: EatingSession, split: str, reason: str) -> None:
        assignments[session.session_id] = {
            "split": split,
            "reason": reason,
            "stratum": _stratum(session),
            "recorded_at": session.created_at,
            "assigned_at": now,
        }

    new = [s for s in sessions if s.session_id not in assignments]
    if not first_creation and not new:
        # A reproducibility/development run must not rewrite the locked file
        # merely to update a timestamp, including when recordings are rejected.
        return payload
    for session in new:
        if session.loop_role == "evaluation":
            record(session, TEST, "loop_evaluation_role")
        elif session.loop_role == "training":
            record(session, DEV, "loop_training_role")
    remaining = [s for s in new if s.session_id not in assignments]
    if first_creation:
        for stratum in ("contains_chewing", "negative_only"):
            members = [s for s in sessions if _stratum(s) == stratum]
            quota = math.ceil(len(members) * test_fraction)
            already = sum(
                1 for s in members if assignments.get(s.session_id, {}).get("split") == TEST
            )
            pending = sorted(
                (s for s in remaining if _stratum(s) == stratum),
                key=lambda s: (s.created_at, s.session_id),
                reverse=True,
            )
            for session in pending:
                if already < quota:
                    record(session, TEST, "initial_newest_per_stratum")
                    already += 1
                else:
                    record(session, DEV, "initial_older_per_stratum")
    else:
        for session in remaining:
            if _hash_fraction(session.session_id) < test_fraction:
                record(session, TEST, "hash_assignment")
            else:
                record(session, DEV, "hash_assignment")
    payload["assignments"] = dict(sorted(assignments.items()))
    payload["updated_at"] = now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _capture_time(session: EatingSession) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(session.created_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.replace(tzinfo=None)


def sitting_groups(
    sessions: Sequence[EatingSession], *, gap_minutes: float = SITTING_GAP_MINUTES
) -> dict[str, str]:
    """Map each recording to its sitting: a run of back-to-back captures.

    A recording joins the previous one's sitting when it starts within
    ``gap_minutes`` of that recording's end.  A recording without a readable
    capture time is its own sitting.  The key names the sitting's first
    recording time, so it is stable when later recordings are added.
    """

    gap = timedelta(minutes=gap_minutes)
    groups: dict[str, str] = {}
    timed = [(stamp, s) for s in sessions if (stamp := _capture_time(s)) is not None]
    current: str | None = None
    last_end: datetime | None = None
    for stamp, session in sorted(timed, key=lambda item: (item[0], item[1].session_id)):
        if last_end is None or stamp - last_end > gap:
            current = stamp.strftime("sitting-%Y%m%d-%H%M")
        groups[session.session_id] = str(current)
        end = stamp + timedelta(seconds=max(float(session.duration_s), 0.0))
        last_end = end if last_end is None else max(last_end, end)
    for session in sessions:
        groups.setdefault(session.session_id, f"sitting-{session.session_id}")
    return groups


def shared_sittings(
    sittings: Mapping[str, str], split_of: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Test recordings captured in the same sitting as development recordings."""

    dev_by_sitting: dict[str, list[str]] = {}
    for session_id, sitting in sittings.items():
        if split_of.get(session_id) == DEV:
            dev_by_sitting.setdefault(sitting, []).append(session_id)
    return [
        {"session_id": session_id, "sitting": sitting, "development_recordings": sorted(dev_by_sitting[sitting])}
        for session_id, sitting in sorted(sittings.items())
        if split_of.get(session_id) == TEST and sitting in dev_by_sitting
    ]


def development_folds(
    sessions: Sequence[EatingSession],
    k: int = 5,
    *,
    grouping: str = "session",
    sittings: Mapping[str, str] | None = None,
) -> dict[str, int]:
    """Stratified chronological round-robin assignment of dev sessions to folds.

    ``grouping="sitting"`` deals whole sittings, so recordings of one meal are
    never split between fitting and held-out folds.  ``"session"`` deals single
    recordings (the rule every run before 2026-11-10 used).
    """

    if grouping not in CV_GROUPINGS:
        raise ValueError(f"cv grouping must be one of {CV_GROUPINGS}")
    if grouping == "sitting":
        sittings = sittings if sittings is not None else sitting_groups(sessions)
        members_of: dict[str, list[EatingSession]] = {}
        for session in sessions:
            members_of.setdefault(sittings[session.session_id], []).append(session)
    else:
        members_of = {session.session_id: [session] for session in sessions}
    units = [
        (min((m.created_at, m.session_id) for m in members), any(m.has_chewing for m in members), members)
        for members in members_of.values()
    ]
    folds: dict[str, int] = {}
    for chewing, offset in ((True, 0), (False, 2)):
        stratum = sorted((u for u in units if u[1] == chewing), key=lambda u: u[0])
        for index, (_, _, members) in enumerate(stratum):
            for session in members:
                folds[session.session_id] = (index + offset) % k
    return folds
