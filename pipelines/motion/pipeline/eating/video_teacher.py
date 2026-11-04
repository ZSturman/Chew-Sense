"""Can video produce the coarse chewing spans automatically?

Today every chewing span is marked by hand; Vision only proposes individual
chews *inside* those hand-marked spans.  This module measures, on recordings
that have both video and human coarse labels, how well ungated Vision output
(``chewsense-vision run``) recovers the human spans, how often talking and
head movement fool it, and how often it agrees with the motion-only detector.

Nothing here becomes a label.  Video scores are compared against human tiers
only; the motion predictions used for agreement are out-of-fold or test
predictions from an eating experiment, so neither automatic source is judged
by labels it produced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from .dataset import EatingSession, discover_sessions
from .evaluate import window_metrics

CYCLE_RATE_THRESHOLD = 2 / 3.0  # at least two proposed chew cycles in a 3 s window
LAG_RANGE_S = (0.33, 1.25)


def _jaw_periodicity(ts: np.ndarray, jaw: np.ndarray, end: float, window_s: float) -> float:
    mask = (ts > end - window_s) & (ts <= end) & np.isfinite(jaw)
    if mask.sum() < 30:
        return float("nan")
    t, x = ts[mask], jaw[mask]
    fps = 1.0 / max(np.median(np.diff(t)), 1e-3)
    x = x - x.mean()
    variance = float((x * x).sum())
    if variance <= 1e-12:
        return 0.0
    best = 0.0
    for lag in range(int(LAG_RANGE_S[0] * fps), min(int(LAG_RANGE_S[1] * fps), len(x) - 2) + 1):
        best = max(best, float((x[lag:] * x[:-lag]).sum() / variance))
    return best


def video_window_features(vision_dir: Path, ends: np.ndarray, window_s: float = 3.0) -> pd.DataFrame:
    cycles = json.loads((vision_dir / "mouth_cycles.json").read_text())
    cycle_ends = np.array(sorted(float(c["end_ts"]) for c in cycles))
    signals = vision_dir / "signals"
    ts = np.load(signals / "ts.npy").astype(np.float64)
    jaw = np.load(signals / "jaw_open_bs_z.npy").astype(np.float64)
    face = np.load(signals / "face_confidence.npy").astype(np.float64)
    rows = []
    for end in ends:
        in_window = (ts > end - window_s) & (ts <= end)
        count = int(((cycle_ends > end - window_s) & (cycle_ends <= end)).sum())
        rows.append({
            "t_end": float(end),
            "video_cycles_per_s": count / window_s,
            "video_jaw_periodicity": _jaw_periodicity(ts, jaw, float(end), window_s),
            "video_face_fraction": float((face[in_window] > 0.5).mean()) if in_window.any() else 0.0,
        })
    return pd.DataFrame(rows)


def evaluate_video_teacher(
    *,
    workspace: Path,
    vision_root: Path,
    predictions_path: Path | None,
    motion_column: str = "p_hgb",
    motion_threshold: float = 0.5,
    window_s: float = 3.0,
    stride_s: float = 0.5,
    guard_s: float = 1.0,
) -> dict[str, Any]:
    from .dataset import build_windows

    sessions, _ = discover_sessions(
        canonical_roots=[workspace / "sessions" / "raw", workspace / "loop" / "assisted" / "sessions" / "raw"],
        field_test_roots=[workspace / "sessions" / "field-tests"],
        loop_root=workspace / "loop",
    )
    paired: list[EatingSession] = [s for s in sessions if (vision_root / s.session_id / "mouth_cycles.json").exists()]
    if not paired:
        raise RuntimeError(f"no recordings with both human labels and Vision output under {vision_root}")
    windows = build_windows(paired, window_s=window_s, stride_s=stride_s, guard_s=guard_s)
    frame = pd.DataFrame({
        "session_id": windows.session_id, "t_end": windows.t_end, "label": windows.label,
        "tier": windows.tier, "context": windows.context,
    })
    video_parts = []
    for session in paired:
        ends = frame.loc[frame["session_id"] == session.session_id, "t_end"].to_numpy()
        part = video_window_features(vision_root / session.session_id, ends, window_s)
        part["session_id"] = session.session_id
        video_parts.append(part)
    frame = frame.merge(pd.concat(video_parts), on=["session_id", "t_end"], how="left")
    if predictions_path is not None and predictions_path.exists():
        motion = pd.read_csv(predictions_path)[["session_id", "t_end", motion_column]]
        motion["t_end"] = motion["t_end"].round(3)
        frame["t_end"] = frame["t_end"].round(3)
        frame = frame.merge(motion, on=["session_id", "t_end"], how="left")
    labelled = frame[(frame["label"] >= 0) & (frame["video_face_fraction"] >= 0.5)].copy()
    y = labelled["label"].to_numpy()

    def scores(column: str) -> dict[str, Any]:
        values = labelled[column].to_numpy(dtype=float)
        ok = np.isfinite(values)
        both = len(np.unique(y[ok])) == 2
        return {
            "windows": int(ok.sum()),
            "auc_roc": float(roc_auc_score(y[ok], values[ok])) if both else None,
            "auc_pr": float(average_precision_score(y[ok], values[ok])) if both else None,
        }

    video_pred = labelled["video_cycles_per_s"].to_numpy() >= CYCLE_RATE_THRESHOLD
    result: dict[str, Any] = {
        "recordings": sorted(labelled["session_id"].unique().tolist()),
        "excluded_low_face_windows": int(((frame["label"] >= 0) & (frame["video_face_fraction"] < 0.5)).sum()),
        "video_cycle_rate": {
            **scores("video_cycles_per_s"),
            "threshold_cycles_per_s": CYCLE_RATE_THRESHOLD,
            "at_threshold": window_metrics(y, video_pred.astype(float), 0.5),
        },
        "video_jaw_periodicity": scores("video_jaw_periodicity"),
        "by_context": {},
    }
    for context, rows in labelled.groupby("context"):
        yy = rows["label"].to_numpy()
        pp = (rows["video_cycles_per_s"].to_numpy() >= CYCLE_RATE_THRESHOLD).astype(float)
        result["by_context"][context] = window_metrics(yy, pp, 0.5)
    if motion_column in labelled:
        has_motion = labelled[motion_column].notna().to_numpy()
        motion_pred = labelled[motion_column].to_numpy() >= motion_threshold
        agree = has_motion & (motion_pred == video_pred)
        agreed_correct = agree & (motion_pred == (y == 1))
        disagree = has_motion & ~agree
        result["motion_agreement"] = {
            "motion_column": motion_column,
            "motion_threshold": motion_threshold,
            "windows_with_both": int(has_motion.sum()),
            "agree_fraction": float(agree.sum() / max(has_motion.sum(), 1)),
            "accuracy_when_agree": float(agreed_correct.sum() / max(agree.sum(), 1)),
            "agree_chewing_windows": int((agree & motion_pred).sum()),
            "agree_chewing_precision": float((agree & motion_pred & (y == 1)).sum() / max((agree & motion_pred).sum(), 1)),
            "agree_not_chewing_windows": int((agree & ~motion_pred).sum()),
            "agree_not_chewing_npv": float((agree & ~motion_pred & (y == 0)).sum() / max((agree & ~motion_pred).sum(), 1)),
            "disagree_windows": int(disagree.sum()),
            "accuracy_motion_when_disagree": float((disagree & (motion_pred == (y == 1))).sum() / max(disagree.sum(), 1)),
        }
    per_session = []
    for session_id, rows in labelled.groupby("session_id"):
        entry = {"session_id": session_id, "windows": int(len(rows)), "chewing": int((rows["label"] == 1).sum())}
        yy = rows["label"].to_numpy()
        vv = (rows["video_cycles_per_s"].to_numpy() >= CYCLE_RATE_THRESHOLD).astype(float)
        m = window_metrics(yy, vv, 0.5)
        entry.update(video_recall=m["recall"], video_fpr=m["false_positive_rate"])
        if motion_column in rows:
            mm = rows[motion_column].to_numpy()
            ok = np.isfinite(mm)
            mw = window_metrics(yy[ok], mm[ok], motion_threshold) if ok.any() else {}
            entry.update(motion_recall=mw.get("recall"), motion_fpr=mw.get("false_positive_rate"))
        per_session.append(entry)
    result["per_session"] = per_session
    return result


def _dominant_hz(values: np.ndarray, rate: float, band: tuple[float, float] = (0.5, 3.0)) -> tuple[float, float]:
    """Dominant frequency in ``band`` and its share of in-band power."""

    x = values - values.mean()
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)), n=max(len(x), 512))) ** 2
    freqs = np.fft.rfftfreq(max(len(x), 512), d=1.0 / rate)
    mask = (freqs >= band[0]) & (freqs <= band[1])
    if not mask.any() or spectrum[mask].sum() <= 0:
        return float("nan"), 0.0
    index = int(np.argmax(spectrum[mask]))
    return float(freqs[mask][index]), float(spectrum[mask][index] / spectrum[mask].sum())


def cadence_consistency(
    *,
    workspace: Path,
    vision_root: Path,
    window_s: float = 6.0,
    stride_s: float = 1.0,
    guard_s: float = 1.0,
) -> dict[str, Any]:
    """Exploratory: motion vs video dominant chewing frequency in human chewing spans.

    Neither stream is cadence ground truth; agreement only says the two
    sensors see the same rhythm.  Longer windows give finer frequency
    resolution (1/window_s Hz before zero-padding).
    """

    from .dataset import build_windows

    sessions, _ = discover_sessions(
        canonical_roots=[workspace / "sessions" / "raw", workspace / "loop" / "assisted" / "sessions" / "raw"],
        field_test_roots=[workspace / "sessions" / "field-tests"],
        loop_root=workspace / "loop",
    )
    paired = [s for s in sessions if (vision_root / s.session_id / "signals" / "ts.npy").exists()]
    rows = []
    for session in paired:
        windows = build_windows([session], window_s=window_s, stride_s=stride_s, guard_s=guard_s)
        signals = vision_root / session.session_id / "signals"
        ts = np.load(signals / "ts.npy").astype(np.float64)
        jaw = np.load(signals / "jaw_open_bs_z.npy").astype(np.float64)
        face = np.load(signals / "face_confidence.npy").astype(np.float64)
        fps = 1.0 / max(float(np.median(np.diff(ts))), 1e-3)
        for index in np.flatnonzero(windows.label == 1):
            end = float(windows.t_end[index])
            in_window = (ts > end - window_s) & (ts <= end)
            if in_window.sum() < fps * window_s * 0.8 or (face[in_window] > 0.5).mean() < 0.9:
                continue
            video_hz, video_share = _dominant_hz(np.nan_to_num(jaw[in_window]), fps)
            gyro = windows.X[index, 3:6]
            candidates = [_dominant_hz(gyro[axis], 50.0) for axis in range(3)]
            motion_hz, motion_share = max(candidates, key=lambda item: item[1])
            rows.append({
                "session_id": session.session_id, "t_end": end, "video_hz": video_hz,
                "video_peak_share": video_share, "motion_hz": motion_hz, "motion_peak_share": motion_share,
            })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"windows": 0}
    difference = (frame["motion_hz"] - frame["video_hz"]).abs()
    return {
        "window_s": window_s,
        "windows": int(len(frame)),
        "recordings": sorted(frame["session_id"].unique().tolist()),
        "median_abs_difference_hz": float(difference.median()),
        "within_0_2_hz": float((difference <= 0.2).mean()),
        "video_hz_median": float(frame["video_hz"].median()),
        "motion_hz_median": float(frame["motion_hz"].median()),
        "pearson_r": float(frame[["video_hz", "motion_hz"]].corr().iloc[0, 1]) if len(frame) > 2 else None,
        "per_session": frame.groupby("session_id").agg(
            windows=("t_end", "size"), video_hz=("video_hz", "median"), motion_hz=("motion_hz", "median"),
        ).reset_index().to_dict("records"),
    }

