"""Empirical confidence for video chew-cycle proposals.

Vision used to give every proposed cycle one of four constants by heuristic
kind (0.8, 0.75, 0.6, 0.4).  Measured against human chewing spans those
constants carry little information (the 0.8 "visible open" kind was the
least likely to lie in chewing).  Rhythm does: a chew is rarely alone, so the
number of *other* proposed cycles 0.3-1.5 s away separates chewing context
from isolated mouth movements.

What the calibrated value means, precisely: the probability that a proposed
cycle lies inside a span the wearer marked as chewing.  It is *not* the
probability that the cycle is a real, individually verified chew (no chews
have been individually verified yet).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.metrics import brier_score_loss, roc_auc_score

from ..eating.dataset import EatingSession, _label_runs

NEIGHBOR_WINDOW_S = (0.3, 1.5)
MAX_BIN = 2  # 0, 1, 2+ rhythmic neighbours


def rhythm_neighbors(end_times: Sequence[float], window: tuple[float, float] = NEIGHBOR_WINDOW_S) -> np.ndarray:
    """For each cycle, how many other cycles end 0.3-1.5 s before or after it."""

    ends = np.asarray(end_times, dtype=float)
    if not len(ends):
        return np.zeros(0, dtype=int)
    gaps = np.abs(ends[:, None] - ends[None, :])
    return ((gaps >= window[0]) & (gaps <= window[1])).sum(axis=1).astype(int)


def labelled_cycles(sessions: Sequence[EatingSession], vision_dirs: Mapping[str, Path], guard_s: float = 1.0) -> list[dict[str, Any]]:
    """Proposed cycles whose midpoint lies ≥ guard_s inside one human label run."""

    rows = []
    for session in sessions:
        folder = vision_dirs.get(session.session_id)
        if folder is None or not (Path(folder) / "mouth_cycles.json").exists():
            continue
        cycles = json.loads((Path(folder) / "mouth_cycles.json").read_text())
        neighbours = rhythm_neighbors([c["end_ts"] for c in cycles])
        runs = _label_runs(session.intervals)
        for cycle, count in zip(cycles, neighbours):
            mid = (float(cycle["start_ts"]) + float(cycle["end_ts"])) / 2
            label = next((r[2] for r in runs if r[0] + guard_s <= mid <= r[1] - guard_s), None)
            if label is None:
                continue
            rows.append({
                "session_id": session.session_id,
                "neighbors": int(count),
                "kind": cycle.get("kind"),
                "fixed_confidence": float(cycle.get("confidence", 0.0)),
                "in_chewing": int(label == "chewing"),
            })
    return rows


def fit_table(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Laplace-smoothed P(in chewing span) per rhythmic-neighbour bin, with counts."""

    table = {}
    for bin_ in range(MAX_BIN + 1):
        members = [r for r in rows if min(r["neighbors"], MAX_BIN) == bin_]
        positives = sum(r["in_chewing"] for r in members)
        table[str(bin_)] = {
            "probability": round((positives + 1) / (len(members) + 2), 3),
            "cycles": len(members),
            "in_chewing": positives,
        }
    return table


def apply_table(table: Mapping[str, Any], neighbors: Sequence[int]) -> np.ndarray:
    return np.array([table[str(min(int(n), MAX_BIN))]["probability"] for n in neighbors], dtype=float)


def evaluate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Leave-one-recording-out: fit the table without a recording, score it on that recording."""

    if not rows:
        return {"cycles": 0}
    y = np.array([r["in_chewing"] for r in rows])
    fixed = np.array([r["fixed_confidence"] for r in rows])
    loro = np.zeros(len(rows))
    sessions = sorted({r["session_id"] for r in rows})
    for session in sessions:
        held = np.array([r["session_id"] == session for r in rows])
        table = fit_table([r for r, h in zip(rows, held) if not h])
        loro[held] = apply_table(table, [r["neighbors"] for r, h in zip(rows, held) if h])
    both = len(np.unique(y)) == 2
    by_kind: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row['kind']} @ {row['fixed_confidence']:g}"
        entry = by_kind.setdefault(key, {"cycles": 0, "in_chewing": 0, "fixed_confidence": row["fixed_confidence"]})
        entry["cycles"] += 1
        entry["in_chewing"] += row["in_chewing"]
    return {
        "cycles": len(rows),
        "recordings": len(sessions),
        "chewing_fraction": float(y.mean()),
        "table_all_data": fit_table(rows),
        "leave_one_recording_out": {
            "auc_roc": float(roc_auc_score(y, loro)) if both else None,
            "brier": float(brier_score_loss(y, loro)),
        },
        "fixed_constants": {
            "auc_roc": float(roc_auc_score(y, fixed)) if both else None,
            "brier": float(brier_score_loss(y, fixed)),
        },
        "constant_base_rate_brier": float(brier_score_loss(y, np.full(len(y), y.mean()))),
        "by_heuristic_kind": by_kind,
        "meaning": "P(proposed cycle lies inside a human-marked chewing span); not P(verified chew)",
    }
