"""Validation of the behaviour measurements against independent references.

* Bout timing and pauses: decoded bouts from **development out-of-fold**
  predictions versus human chewing spans.  The locked test is not used.
* Cadence: motion dominant frequency versus the video jaw-opening frequency
  inside human chewing spans (two independent sensors; neither is ground
  truth).  Includes how often motion is within 0.2 Hz of video at each level
  of motion peak share, an empirical confidence for a cadence estimate.
* Chew count: cadence x duration versus video chew-cycle proposals
  (exploratory: the video count is itself unverified).
* Video chew-cycle confidence: rhythm-calibrated probability of lying in a
  chewing span, leave-one-recording-out.

Recordings with video are mostly in the eating detector's locked test split.
These analyses use their motion and video only (never detector outputs) and
fit nothing except the cycle-confidence table, which is evaluated
leave-one-recording-out; the output says so.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from ..eating.dataset import CHEWING, EatingSession, _label_runs, build_windows
from ..eating.evaluate import DecoderConfig, decode_bouts
from . import cycle_context
from .measures import CADENCE_WINDOW_S, bouts_from_states, dominant_frequency, motion_cadence


def _median_abs(values: Sequence[float]) -> float | None:
    values = [abs(v) for v in values if v is not None and np.isfinite(v)]
    return round(float(np.median(values)), 2) if values else None


def bout_timing(
    sessions: Mapping[str, EatingSession], predictions: pd.DataFrame, threshold: float,
    *, window_s: float = 3.0, min_reference_s: float = 3.0, split: str = "dev",
) -> dict[str, Any]:
    decoder = DecoderConfig.from_threshold(threshold)
    onset, offset, duration, pause_errors = [], [], [], []
    pauses_reference = pauses_found = references = matched = 0
    per_session = []
    for session_id, rows in predictions[predictions["split"] == split].groupby("session_id"):
        session = sessions.get(session_id)
        if session is None or not session.has_chewing:
            continue
        rows = rows.sort_values("t_end")
        t = rows["t_end"].to_numpy()
        states, _ = decode_bouts(t, rows["probability"].to_numpy(dtype=float), decoder)
        bouts = bouts_from_states(t, states, window_s)
        human = [(a, b) for a, b, label, _ in _label_runs(session.intervals) if label == CHEWING and b - a >= min_reference_s]
        found = []
        for start, end in human:
            hits = [b for b in bouts if b[1] > start and b[0] < end]
            references += 1
            if not hits:
                found.append(None)
                continue
            matched += 1
            span = (min(h[0] for h in hits), max(h[1] for h in hits))
            onset.append(span[0] - start)
            offset.append(span[1] - end)
            duration.append(sum(min(h[1], end) - max(h[0], start) for h in hits) - (end - start))
            found.append((span, hits))
        for (a, previous), (b, following) in zip(zip(human, found), zip(human[1:], found[1:])):
            pauses_reference += 1
            reference_gap = b[0] - a[1]
            if previous and following and previous[1][-1] != following[1][0]:
                pauses_found += 1
                pause_errors.append((following[0][0] - previous[0][1]) - reference_gap)
        per_session.append({"session_id": session_id, "reference_bouts": len(human),
                            "matched": sum(f is not None for f in found), "predicted_bouts": len(bouts)})
    return {
        "split": split,
        "predictions": "out-of-fold" if split == "dev" else split,
        "reference_bouts": references,
        "matched_bouts": matched,
        "median_abs_onset_error_s": _median_abs(onset),
        "median_onset_error_s": round(float(np.median(onset)), 2) if onset else None,
        "median_abs_offset_error_s": _median_abs(offset),
        "median_abs_duration_error_s": _median_abs(duration),
        "reference_pauses": pauses_reference,
        "pauses_detected": pauses_found,
        "median_abs_pause_error_s": _median_abs(pause_errors),
        "timeline": "offline: decisions placed at window centres",
        "per_session": per_session,
    }


def cadence_against_video(sessions: Sequence[EatingSession], vision_dirs: Mapping[str, Path]) -> dict[str, Any]:
    rows = []
    for session in sessions:
        folder = vision_dirs.get(session.session_id)
        if folder is None or not (Path(folder) / "signals" / "ts.npy").exists():
            continue
        windows = build_windows([session], window_s=CADENCE_WINDOW_S, stride_s=1.0, guard_s=1.0)
        ts = np.load(Path(folder) / "signals" / "ts.npy").astype(float)
        jaw = np.load(Path(folder) / "signals" / "jaw_open_bs_z.npy").astype(float)
        face = np.load(Path(folder) / "signals" / "face_confidence.npy").astype(float)
        fps = 1.0 / max(float(np.median(np.diff(ts))), 1e-3)
        cycles = json.loads((Path(folder) / "mouth_cycles.json").read_text())
        cycle_ends = np.array([c["end_ts"] for c in cycles], dtype=float)
        for index in np.flatnonzero(windows.label == 1):
            end = float(windows.t_end[index])
            inside = (ts > end - CADENCE_WINDOW_S) & (ts <= end)
            if inside.sum() < fps * CADENCE_WINDOW_S * 0.8 or (face[inside] > 0.5).mean() < 0.9:
                continue
            video_hz, _ = dominant_frequency(np.nan_to_num(jaw[inside]), fps)
            motion_hz, share = motion_cadence(windows.X[index])
            video_cycles = int(((cycle_ends > end - CADENCE_WINDOW_S) & (cycle_ends <= end)).sum())
            rows.append({"session_id": session.session_id, "clock": session.clock, "video_hz": video_hz,
                         "motion_hz": motion_hz, "peak_share": share, "video_cycles": video_cycles})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"windows": 0}
    frame["abs_diff"] = (frame["motion_hz"] - frame["video_hz"]).abs()
    frame["estimated_chews"] = frame["motion_hz"] * CADENCE_WINDOW_S
    # Terciles of motion peak share: does a more prominent motion peak agree with video more often?
    cuts = np.quantile(frame["peak_share"], [0.0, 1 / 3, 2 / 3, 1.0])
    confidence = []
    for index, (low, high) in enumerate(zip(cuts, cuts[1:])):
        last = index == len(cuts) - 2
        members = frame[(frame["peak_share"] >= low) & ((frame["peak_share"] <= high) if last else (frame["peak_share"] < high))]
        confidence.append({
            "peak_share": f"{low:.3f}-{high:.3f}",
            "windows": int(len(members)),
            "within_0_2_hz": None if members.empty else round(float((members["abs_diff"] <= 0.2).mean()), 3),
        })
    count_ratio = frame["estimated_chews"] / frame["video_cycles"].where(frame["video_cycles"] > 0)
    return {
        "window_s": CADENCE_WINDOW_S,
        "windows": int(len(frame)),
        "recordings": sorted(frame["session_id"].unique().tolist()),
        "median_abs_difference_hz": round(float(frame["abs_diff"].median()), 3),
        "within_0_2_hz": round(float((frame["abs_diff"] <= 0.2).mean()), 3),
        "video_hz_median": round(float(frame["video_hz"].median()), 3),
        "motion_hz_median": round(float(frame["motion_hz"].median()), 3),
        "video_hz_range": [round(float(frame["video_hz"].min()), 3), round(float(frame["video_hz"].max()), 3)],
        "pearson_r": round(float(frame[["video_hz", "motion_hz"]].corr().iloc[0, 1]), 3) if len(frame) > 2 else None,
        "confidence_by_peak_share": confidence,
        "chew_count": {
            "status": "exploratory: video cycles are unverified proposals with known misses",
            "median_ratio_estimate_to_video_cycles": None if count_ratio.dropna().empty else round(float(count_ratio.median()), 3),
            "windows_with_video_cycles": int(count_ratio.notna().sum()),
        },
        "caveat": "one self-selected pace; agreement at one pace does not show that changes in pace are tracked",
    }


def evaluate_behavior(
    *, sessions: Sequence[EatingSession], vision_dirs: Mapping[str, Path], predictions: pd.DataFrame,
    threshold: float, test_split_ids: set[str],
) -> dict[str, Any]:
    by_id = {s.session_id: s for s in sessions}
    video_sessions = [s for s in sessions if s.session_id in vision_dirs]
    cycle_rows = cycle_context.labelled_cycles(video_sessions, vision_dirs)
    return {
        "bout_timing": bout_timing(by_id, predictions, threshold),
        "cadence": cadence_against_video(video_sessions, vision_dirs),
        "video_cycle_confidence": cycle_context.evaluate(cycle_rows),
        "test_split_recordings_used_for_sensor_comparisons": sorted(
            s.session_id for s in video_sessions if s.session_id in test_split_ids),
        "test_split_note": "motion and video only; no detector output from the locked test is read, and nothing "
                           "is tuned on these recordings except the cycle-confidence table (leave-one-recording-out)",
    }
