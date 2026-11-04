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
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .dataset import EatingSession


SPLIT_VERSION = 1
TEST = "test"
DEV = "dev"


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


def development_folds(sessions: Sequence[EatingSession], k: int = 5) -> dict[str, int]:
    """Stratified chronological round-robin assignment of dev sessions to folds."""

    folds: dict[str, int] = {}
    for stratum in ("contains_chewing", "negative_only"):
        members = sorted(
            (s for s in sessions if _stratum(s) == stratum),
            key=lambda s: (s.created_at, s.session_id),
        )
        offset = 0 if stratum == "contains_chewing" else 2
        for index, session in enumerate(members):
            folds[session.session_id] = (index + offset) % k
    return folds
