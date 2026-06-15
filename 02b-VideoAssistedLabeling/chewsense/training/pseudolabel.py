"""Build per-frame chew-head labels from automatic or reviewed events.

Input: an `out/<run>/` directory previously produced by `chewsense run`.
       Reads `frame_predictions.parquet` and `events.json`.

Output: `labels.parquet` with columns:
    frame_idx, ts, label_id, weight
where `label_id` indexes into `LABEL_NAMES` and `weight` is in [0, 1] —
1.0 for frames inside an Episode (high-confidence chewing) or fully
non-eating frames; lower for ambiguous frames near boundaries or under
occlusion / talking.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

# Binary task: is this frame part of an active chewing episode?
LABEL_NAMES = ("not_chewing", "chewing")
NOT_CHEWING = 0
CHEWING = 1


def build_labels(
    run_dir: str | Path,
    *,
    boundary_soft_s: float = 0.4,
    annotations_path: str | Path | None = None,
) -> pd.DataFrame:
    run_dir = Path(run_dir)
    fp = run_dir / "frame_predictions.parquet"
    if not fp.exists():
        # tolerate CSV fallback path
        alt = run_dir / "frame_predictions.csv"
        if alt.exists():
            df = pd.read_csv(alt)
        else:
            raise FileNotFoundError(f"no frame_predictions in {run_dir}")
    else:
        df = pd.read_parquet(fp)

    if annotations_path is not None:
        events = json.loads(Path(annotations_path).read_text())
        if not events.get("verified", False):
            raise ValueError("reviewed annotations must be verified before CV training")
        eating_intervals = events.get("chewing_bouts", [])
        mouth_states = events.get("mouth_states", [])
    else:
        events = json.loads((run_dir / "events.json").read_text())
        eating_intervals = events.get("episodes", [])
        mouth_states = []
    ts = df["ts"].to_numpy(dtype=np.float64)
    n = len(ts)
    label = np.zeros(n, dtype=np.int8)
    weight = np.ones(n, dtype=np.float32)

    # Mark chewing inside episodes
    for ep in eating_intervals:
        a = int(np.searchsorted(ts, ep["start_ts"]))
        b = int(np.searchsorted(ts, ep["end_ts"]))
        label[a:b] = CHEWING

    # Soft boundaries: down-weight frames within ±boundary_soft_s of an episode edge
    edges = []
    for ep in eating_intervals:
        edges.append(ep["start_ts"])
        edges.append(ep["end_ts"])
    for edge in edges:
        a = int(np.searchsorted(ts, edge - boundary_soft_s))
        b = int(np.searchsorted(ts, edge + boundary_soft_s))
        weight[max(0, a): min(n, b)] = np.minimum(weight[max(0, a): min(n, b)], 0.5)

    # Down-weight ambiguous regions: talking spans, occlusion spans
    for sp in events.get("non_eating_mouth_spans", []):
        a = int(np.searchsorted(ts, sp["start_ts"]))
        b = int(np.searchsorted(ts, sp["end_ts"]))
        weight[a:b] = np.minimum(weight[a:b], 0.3)
    for sp in events.get("occlusion_spans", []):
        a = int(np.searchsorted(ts, sp["start_ts"]))
        b = int(np.searchsorted(ts, sp["end_ts"]))
        weight[a:b] = 0.0  # don't train on occluded frames
    for state in mouth_states:
        if state.get("state") != "unknown":
            continue
        a = int(np.searchsorted(ts, state["start_ts"]))
        b = int(np.searchsorted(ts, state["end_ts"]))
        weight[a:b] = 0.0

    out = pd.DataFrame({
        "frame_idx": df["frame_idx"].astype(np.int32),
        "ts": ts.astype(np.float32),
        "label_id": label,
        "weight": weight,
    })
    return out


def write_labels(
    run_dir: str | Path,
    out_path: str | Path,
    *,
    annotations_path: str | Path | None = None,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = build_labels(run_dir, annotations_path=annotations_path)
    df.to_parquet(out_path, index=False)
    return out_path
