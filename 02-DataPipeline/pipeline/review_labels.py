"""
Review-label overlay: turn human review artifacts in a session bundle
into per-row training labels with explicit confidence/mask handling.

Inputs (any subset; missing files are simply skipped):
  - `bite_review.json`     — confirmed/manual strokes (Phase A unified UX)
  - `review_feedback.csv`  — chewing/notChewing window corrections
  - `face_presence.json`   — intervals where MediaPipe lost the face

Output policy (matches the user's spec):
  - Every stroke (any non-`auto`/`heuristic` source) → label=1 in
    ±`stroke_radius_sec` of its timestamp. Auto/heuristic strokes from
    the visual labeler are *not* trusted as ground truth here.
  - Window with `corrected_state == "chewing"` → label=1 across the span.
  - Window with `corrected_state == "idle"` (i.e. notChewing correction)
    → label=0 across the span, but only inside face-present regions.
  - Rows that fall in a face-missing interval AND have no manual
    override are *masked* (`label_mask=0`) so the trainer can drop them.

The function never deletes the existing `label` column; it writes
`label_review`, `label_mask`, and `label_source` so downstream code can
choose between original-only, review-only, or unioned labels.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# Bundle artifact readers
# --------------------------------------------------------------------------

def _load_bite_review_strokes(bundle_dir: Path) -> list[tuple[float, str]]:
    """Returns [(timestamp_sec, source), ...] for every stroke in
    `bite_review.json`. Empty list when the file is missing or unreadable."""
    fp = bundle_dir / "bite_review.json"
    if not fp.exists():
        return []
    try:
        payload = json.loads(fp.read_text())
    except Exception:
        return []
    out: list[tuple[float, str]] = []
    for s in payload.get("strokes", []):
        try:
            ts = float(s["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        src = str(s.get("source") or "manual")
        out.append((ts, src))
    return out


def _load_feedback_windows(
    bundle_dir: Path,
) -> list[tuple[float, float, str]]:
    """Returns [(start_sec, end_sec, corrected_state), ...] from
    `review_feedback.csv`. Times are converted from absolute motion
    timestamps back to session-relative seconds when possible."""
    fp = bundle_dir / "review_feedback.csv"
    if not fp.exists():
        return []
    rows: list[tuple[float, float, str]] = []
    try:
        with fp.open() as f:
            reader = csv.DictReader(f)
            for r in reader:
                try:
                    s = float(r["window_start"])
                    e = float(r["window_end"])
                except (KeyError, TypeError, ValueError):
                    continue
                state = (r.get("corrected_state") or "").strip().lower()
                if e > s and state in ("chewing", "idle"):
                    rows.append((s, e, state))
    except Exception:
        return []
    return rows


def _load_face_presence(
    bundle_dir: Path,
) -> Optional[list[tuple[float, float, bool]]]:
    fp = bundle_dir / "face_presence.json"
    if not fp.exists():
        return None
    try:
        payload = json.loads(fp.read_text())
    except Exception:
        return None
    out: list[tuple[float, float, bool]] = []
    for item in payload.get("intervals", []):
        try:
            out.append((
                float(item["start"]),
                float(item["end"]),
                bool(item["present"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


# --------------------------------------------------------------------------
# Per-row mask helpers
# --------------------------------------------------------------------------

def _face_mask_for_times(
    times: np.ndarray,
    intervals: list[tuple[float, float, bool]],
) -> np.ndarray:
    """Returns a 0/1 face-present mask aligned with `times`. Defaults
    to 1 when there are no intervals (face-presence unknown ≈ trust)."""
    if not intervals:
        return np.ones_like(times, dtype=np.uint8)
    mask = np.zeros_like(times, dtype=np.uint8)
    for s, e, p in intervals:
        if not p:
            continue
        sel = (times >= s) & (times < e)
        mask[sel] = 1
    return mask


def _trusted_stroke_indices(
    strokes: Iterable[tuple[float, str]],
) -> list[float]:
    """Strokes from `auto` / `heuristic` sources reflect the seeder, not
    a human, so they aren't ground truth. Keep only manual / confirmed /
    model-confirmed strokes."""
    trusted: list[float] = []
    for ts, src in strokes:
        s = src.lower()
        if s in ("manual", "confirmed", "model"):
            trusted.append(ts)
    return trusted


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def overlay_review_labels(
    df: pd.DataFrame,
    bundle_dir: Path,
    *,
    timestamp_col: str,
    motion_base_timestamp: Optional[float] = None,
    stroke_radius_sec: float = 0.35,
) -> pd.DataFrame:
    """Add `label_review`, `label_mask`, `label_source` columns to `df`.

    Args:
        df: Bundle CSV. Must contain `timestamp_col`.
        bundle_dir: Directory holding the review artifacts.
        timestamp_col: Column with motion timestamps (absolute or relative).
        motion_base_timestamp: Subtract this from `timestamp_col` to get
            session-relative seconds. If None, the first row's timestamp
            is used as the base.
        stroke_radius_sec: Half-width of the chewing window placed around
            each trusted stroke.

    The DataFrame is returned with the new columns. The original
    columns are preserved.
    """
    if timestamp_col not in df.columns:
        raise ValueError(f"timestamp_col {timestamp_col!r} missing from CSV")

    ts_raw = df[timestamp_col]
    if not np.issubdtype(ts_raw.dtype, np.number):
        ts_abs = pd.to_datetime(ts_raw).astype("int64").to_numpy() / 1e9
    else:
        ts_abs = ts_raw.astype(float).to_numpy()

    if motion_base_timestamp is None:
        motion_base_timestamp = float(ts_abs[0]) if ts_abs.size else 0.0
    times = ts_abs - float(motion_base_timestamp)

    n = times.size
    label_review = np.full(n, fill_value=-1, dtype=np.int8)  # -1 = unknown
    label_source = np.array(["unknown"] * n, dtype=object)

    # 1) notChewing windows first; strokes can later promote to chewing.
    for s, e, state in _load_feedback_windows(bundle_dir):
        sel = (times >= s) & (times < e)
        if not sel.any():
            continue
        if state == "idle":
            label_review[sel] = 0
            label_source[sel] = "review_idle"
        elif state == "chewing":
            label_review[sel] = 1
            label_source[sel] = "review_chewing"

    # 2) Strokes win over windows — a confirmed stroke is the strongest
    #    chewing signal we have.
    for ts in _trusted_stroke_indices(_load_bite_review_strokes(bundle_dir)):
        sel = (times >= ts - stroke_radius_sec) & (times < ts + stroke_radius_sec)
        if sel.any():
            label_review[sel] = 1
            label_source[sel] = "review_stroke"

    # 3) Mask out face-missing rows that lack any manual override.
    face_intervals = _load_face_presence(bundle_dir)
    face_mask = _face_mask_for_times(times, face_intervals or [])
    label_mask = np.ones(n, dtype=np.uint8)
    if face_intervals is not None:
        ambiguous = (face_mask == 0) & (label_review < 0)
        label_mask[ambiguous] = 0
        label_source[ambiguous] = "face_missing"

    df = df.copy()
    df["label_review"] = label_review
    df["label_mask"] = label_mask
    df["label_source"] = label_source
    return df


__all__ = ["overlay_review_labels"]
