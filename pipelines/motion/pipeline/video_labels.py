"""Overlay canonical reviewed video labels onto raw motion rows.

Finalized ``annotations/motion_labels.csv`` and
``annotations/canonical_events.json`` take precedence. Legacy derived 02b
artifacts remain readable during migration.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)

# Minimum spacing between adjacent chews (seconds). Mirrors
# `ReviewerState.neighborGap` in the Swift UI and ``apply_review_overrides``
# so all three layers enforce the same "start ≥ prev.end + gap" invariant.
NEIGHBOR_GAP_S = 1.0 / 30.0


# Default derived-root relative to a workspace; callers may override.
DEFAULT_DERIVED_ROOT = Path("data") / "derived" / "video_labels"

ALIGNED_CSV_NAME = "motion_aligned_labels.csv"
CHEW_CYCLES_NAME = "chew_cycles.json"
EVENTS_JSON_NAME = "events.json"
OVERRIDES_JSON_NAME = "review_overrides.json"
CANONICAL_ALIGNED_NAME = Path("annotations") / "motion_labels.csv"
CANONICAL_EVENTS_NAME = Path("annotations") / "canonical_events.json"


@dataclass
class VideoLabelsArtifacts:
    """Resolved on-disk artifacts for one session."""
    session_id: str
    aligned_csv: Optional[Path]
    chew_cycles_json: Optional[Path]
    events_json: Optional[Path]
    canonical_events_json: Optional[Path]
    overrides_json: Optional[Path]    # lives in the raw bundle dir, not derived
    chew_cycle_count: int = 0
    eating_span_count: int = 0
    verified: bool = False
    annotation_revision: int = 0
    model_ids: dict[str, str] | None = None

    @property
    def has_video_labels(self) -> bool:
        return self.aligned_csv is not None and self.aligned_csv.exists()


def find_artifacts(
    session_id: str,
    derived_root: Path,
    raw_bundle_dir: Optional[Path] = None,
) -> VideoLabelsArtifacts:
    """Locate the derived artifacts for ``session_id`` under ``derived_root``."""
    derived_root = Path(derived_root)
    session_dir = derived_root / session_id

    canonical_aligned = (
        Path(raw_bundle_dir) / CANONICAL_ALIGNED_NAME
        if raw_bundle_dir is not None
        else None
    )
    aligned = session_dir / ALIGNED_CSV_NAME
    cycles = session_dir / CHEW_CYCLES_NAME
    events = session_dir / EVENTS_JSON_NAME
    canonical_events = (
        Path(raw_bundle_dir) / CANONICAL_EVENTS_NAME
        if raw_bundle_dir is not None
        else None
    )

    overrides = None
    if raw_bundle_dir is not None:
        candidate = Path(raw_bundle_dir) / OVERRIDES_JSON_NAME
        if candidate.exists():
            overrides = candidate

    art = VideoLabelsArtifacts(
        session_id=session_id,
        aligned_csv=(
            canonical_aligned
            if canonical_aligned is not None and canonical_aligned.exists()
            else aligned if aligned.exists() else None
        ),
        chew_cycles_json=cycles if cycles.exists() else None,
        events_json=events if events.exists() else None,
        canonical_events_json=(
            canonical_events
            if canonical_events is not None and canonical_events.exists()
            else None
        ),
        overrides_json=overrides,
    )

    if art.canonical_events_json is not None:
        try:
            payload = json.loads(art.canonical_events_json.read_text())
            art.eating_span_count = len(payload.get("chewing_bouts", []) or [])
            art.chew_cycle_count = len(payload.get("chew_cycles", []) or [])
            art.verified = bool(payload.get("verified", False))
            art.annotation_revision = int(payload.get("revision", 0))
            art.model_ids = payload.get("model_ids") or {}
        except Exception:  # noqa: BLE001
            pass
    elif art.chew_cycles_json is not None:
        try:
            payload = json.loads(art.chew_cycles_json.read_text())
            episodes = payload.get("episodes", []) or []
            art.eating_span_count = len(episodes)
            art.chew_cycle_count = sum(len(ep.get("chews", []) or []) for ep in episodes)
        except Exception:  # noqa: BLE001 — best-effort metadata
            pass

    if overrides is not None and art.canonical_events_json is None:
        try:
            payload = json.loads(overrides.read_text())
            art.verified = bool(payload.get("verified", False))
        except Exception:  # noqa: BLE001
            pass

    return art


def _phase_to_sin_cos(rows: pd.DataFrame, episodes: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Compute ``chew_phase_sin`` / ``chew_phase_cos`` for each row.

    Phase angle θ ramps linearly within each chew:
      θ = π · (t - start_ts) / (peak_ts - start_ts)   for opening
      θ = π + π · (t - peak_ts) / (end_ts - peak_ts)  for closing
    so a full cycle (idle → fully open → closed) is θ ∈ [0, 2π].
    Outside any chew, sin/cos are both zero (interpreted by Phase-5 head as
    "no signal" via the chew-id mask).
    """
    if "video_ts" not in rows.columns:
        return np.zeros(len(rows)), np.zeros(len(rows))

    t = rows["video_ts"].to_numpy(dtype=float)
    theta = np.full_like(t, fill_value=np.nan)
    for ep in episodes:
        # Sort chews by start_ts and skip any that overlap a prior chew. The
        # invariant is that ``start_ts >= prev.end_ts + NEIGHBOR_GAP_S``; if an
        # upstream payload violates it we log and ignore the offender rather
        # than silently overwriting earlier rows.
        chews = sorted(
            (c for c in (ep.get("chews", []) or [])),
            key=lambda c: float(c["start_ts"]),
        )
        prev_end: float | None = None
        for chew in chews:
            start, peak, end = float(chew["start_ts"]), float(chew["peak_ts"]), float(chew["end_ts"])
            if not (start < peak <= end):
                continue
            if prev_end is not None and start < prev_end + NEIGHBOR_GAP_S:
                logger.warning(
                    "chew %s overlaps prior chew (start %.4f < prev_end %.4f + gap); skipping",
                    chew.get("id", "?"), start, prev_end,
                )
                continue
            opening = (t >= start) & (t <= peak)
            closing = (t > peak) & (t <= end)
            theta[opening] = np.pi * (t[opening] - start) / max(peak - start, 1e-9)
            theta[closing] = np.pi + np.pi * (t[closing] - peak) / max(end - peak, 1e-9)
            prev_end = end

    out_sin = np.where(np.isnan(theta), 0.0, np.sin(theta))
    out_cos = np.where(np.isnan(theta), 0.0, np.cos(theta))
    return out_sin, out_cos


def _canonical_as_episodes(payload: dict) -> list[dict]:
    chews_by_bout: dict[str, list[dict]] = {}
    unassigned: list[dict] = []
    for chew in payload.get("chew_cycles", []) or []:
        bout_id = chew.get("bout_id")
        if bout_id:
            chews_by_bout.setdefault(str(bout_id), []).append(chew)
        else:
            unassigned.append(chew)
    episodes = []
    for bout in payload.get("chewing_bouts", []) or []:
        item = dict(bout)
        item["chews"] = chews_by_bout.get(str(bout.get("id")), [])
        episodes.append(item)
    if unassigned:
        episodes.append({"id": "unassigned", "chews": unassigned})
    return episodes


def overlay_video_labels(
    raw_df: pd.DataFrame,
    artifacts: VideoLabelsArtifacts,
    *,
    timestamp_col: str = "timestamp",
) -> pd.DataFrame:
    """Return ``raw_df`` with video-label columns merged onto it.

    Adds (when artifacts are present):
      - ``eating`` (0/1, from labeller; 0 outside any episode)
      - ``chew_phase`` ("opening"/"closing"/"idle"/"unreliable")
      - ``chew_id`` (string; "" outside any chew)
      - ``video_confidence`` (float)
      - ``chew_phase_sin``, ``chew_phase_cos`` (regression targets for head B)
      - ``has_video_labels`` (constant int column; downstream sees per-row coverage)

    If artifacts are absent, the input is returned unchanged. The merge is a
    nearest-timestamp asof join so float-precision jitter between the host
    monotonic clock recorded twice (motion CSV vs alignment CSV) is tolerated.
    """
    if not artifacts.has_video_labels:
        return raw_df

    aligned = pd.read_csv(artifacts.aligned_csv)
    # Coerce string columns — empty cells round-trip as float NaN.
    aligned["chew_id"] = aligned["chew_id"].fillna("").astype(str)
    aligned["chew_phase"] = aligned["chew_phase"].fillna("idle").astype(str)
    aligned["eating"] = aligned["eating"].fillna(0).astype(int)
    aligned["video_confidence"] = aligned["video_confidence"].fillna(0.0).astype(float)

    # asof merge on timestamp — both sides must be sorted.
    raw_sorted = raw_df.sort_values(timestamp_col).reset_index(drop=False)
    aligned_sorted = aligned.sort_values("timestamp").reset_index(drop=True)

    additive_columns = [
        name for name in (
            "video_ts", "eating", "mouth_state", "chew_phase", "chew_id",
            "bite_event", "bite_id", "bout_id", "label_valid", "label_source",
            "model_id", "annotation_revision", "video_confidence",
        )
        if name in aligned_sorted.columns
    ]
    merged = pd.merge_asof(
        raw_sorted,
        aligned_sorted[["timestamp", *additive_columns]],
        on="timestamp",
        direction="nearest",
        tolerance=0.05,   # 50 ms — generous vs 100 Hz IMU period of 10 ms
    )

    # Rows outside the labelled window get NaN — replace with safe defaults.
    merged["eating"] = merged["eating"].fillna(0).astype(int)
    merged["chew_phase"] = merged["chew_phase"].fillna("idle").astype(str)
    merged["chew_id"] = merged["chew_id"].fillna("").astype(str)
    merged["video_confidence"] = merged["video_confidence"].fillna(0.0).astype(float)
    merged["video_ts"] = merged["video_ts"].fillna(-1.0).astype(float)
    defaults = {
        "mouth_state": "unknown",
        "bite_event": 0,
        "bite_id": "",
        "bout_id": "",
        "label_valid": 0,
        "label_source": "automatic",
        "model_id": "",
        "annotation_revision": 0,
    }
    for column, default in defaults.items():
        if column not in merged.columns:
            merged[column] = default
        elif isinstance(default, str):
            merged[column] = merged[column].fillna(default).astype(str)
        else:
            merged[column] = merged[column].fillna(default).astype(int)

    # Compute sin/cos targets from chew_cycles.json (authoritative timestamps).
    episodes: list[dict] = []
    if artifacts.canonical_events_json is not None:
        try:
            payload = json.loads(artifacts.canonical_events_json.read_text())
            episodes = _canonical_as_episodes(payload)
        except Exception:  # noqa: BLE001
            episodes = []
    elif artifacts.chew_cycles_json is not None:
        try:
            episodes = json.loads(artifacts.chew_cycles_json.read_text()).get("episodes", []) or []
        except Exception:  # noqa: BLE001
            episodes = []
    sin_vals, cos_vals = _phase_to_sin_cos(merged, episodes)
    merged["chew_phase_sin"] = sin_vals
    merged["chew_phase_cos"] = cos_vals
    merged["has_video_labels"] = 1

    # Restore original row order.
    merged = merged.sort_values("index").drop(columns=["index"]).reset_index(drop=True)
    return merged


def resolve_derived_root(input_dir: Path, derived_root: Optional[Path]) -> Path:
    """Resolve the derived-root path against the data input directory.

    By convention, ``input_dir`` is ``data/raw_sessions/`` so the sibling
    ``data/derived/video_labels/`` becomes the default landing zone. Callers
    may pass an explicit absolute path to override.
    """
    if derived_root is not None:
        return Path(derived_root)
    input_dir = Path(input_dir)
    # Walk up to the `data/` parent.
    if input_dir.name == "raw_sessions" and input_dir.parent.name == "data":
        return input_dir.parent / "derived" / "video_labels"
    return input_dir.parent / DEFAULT_DERIVED_ROOT
