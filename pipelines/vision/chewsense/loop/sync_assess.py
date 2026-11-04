"""Automatic *proposal* of synchronization-marker correspondences.

Within each instructed nod window, compare the peak visual head angular speed
(Vision frame telemetry) with the peak AirPods gyro magnitude. The result is a
diagnostic for the reviewer. It never changes the clock mapping and never sets
``sync_verified``; only a human confirmation in Review does that.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

WINDOW_MARGIN_S = 1.5
MEDIAN_TARGET_S = 0.05
P95_TARGET_S = 0.10
MIN_MARKERS = 3


def _peak(times: np.ndarray, values: np.ndarray, start: float, end: float):
    inside = (times >= start) & (times <= end) & np.isfinite(values)
    if inside.sum() < 5:
        return None
    window_t, window_v = times[inside], values[inside]
    index = int(np.argmax(window_v))
    baseline = float(np.median(window_v))
    spread = float(np.median(abs(window_v - baseline))) or 1e-9
    return float(window_t[index]), float((window_v[index] - baseline) / spread)


def _alternative_peaks(times, values, start, end):
    """Keep substantial local maxima, without choosing one using the clock map."""
    inside = (times >= start) & (times <= end) & np.isfinite(values)
    t, v = times[inside], values[inside]
    if len(t) < 5:
        return []
    baseline = float(np.median(v))
    threshold = baseline + 0.5 * (float(np.max(v)) - baseline)
    return [float(t[i]) for i in find_peaks(v)[0] if v[i] >= threshold]


def assess_markers(
    intended: dict[str, Any],
    frame_predictions: Path,
    motion_csv: Path,
    video_clock_origin: float,
    *,
    min_prominence: float = 4.0,
) -> dict[str, Any]:
    windows = [i for i in intended.get("intervals", []) if i.get("sync_marker")]
    result: dict[str, Any] = {
        "artifact_type": "sync_marker_assessment",
        "assessment_version": 3,
        "automatic": True,
        "sets_sync_verified": False,
        "mapping_changed": False,
        "markers": [],
    }
    if not frame_predictions.exists():
        result.update(status="inconclusive", reason="no vision frame telemetry")
        return result
    columns = {"ts", "head_pitch", "head_yaw", "head_roll", "face_detected"}
    frames = pd.read_csv(frame_predictions, usecols=lambda c: c in columns)
    if columns.difference(frames.columns):
        result.update(status="inconclusive", reason="missing full three-axis visual pose telemetry")
        return result
    frames = frames[frames["face_detected"].astype(bool)]
    ft = frames["ts"].to_numpy(float)
    if len(ft) < 10:
        result.update(status="inconclusive", reason="face not detected in enough frames")
        return result
    # FaceFeatures emits radians, including its matrix-based pose estimate.
    pitch = np.unwrap(frames["head_pitch"].to_numpy(float))
    yaw = np.unwrap(frames["head_yaw"].to_numpy(float))
    roll = np.unwrap(frames["head_roll"].to_numpy(float))
    dt = np.gradient(ft)
    dt[dt <= 0] = np.nan
    # Gyro magnitude includes all three axes. Omitting roll can select the
    # opposite phase of a nod even when the actual streams coincide.
    visual_speed = np.sqrt(sum((np.gradient(angle) / dt) ** 2 for angle in (pitch, yaw, roll)))
    result["visual_pose_axes"] = ["head_pitch", "head_yaw", "head_roll"]

    motion = pd.read_csv(motion_csv, usecols=["timestamp", "gx", "gy", "gz"])
    mt = motion["timestamp"].to_numpy(float) - video_clock_origin
    gyro = np.sqrt((motion[["gx", "gy", "gz"]].to_numpy(float) ** 2).sum(axis=1))

    residuals = []
    for window in windows:
        start = window["start_ts"] - WINDOW_MARGIN_S
        end = window["end_ts"] + WINDOW_MARGIN_S
        visual = _peak(ft, visual_speed, start, end)
        sensor = _peak(mt, gyro, start, end)
        entry: dict[str, Any] = {"activity_id": window["activity_id"], "window": [start, end]}
        if visual is None or sensor is None:
            entry["usable"] = False
            entry["reason"] = "missing visual or motion samples in window"
        else:
            usable = visual[1] >= min_prominence and sensor[1] >= min_prominence
            visual_peaks = _alternative_peaks(ft, visual_speed, start, end)
            sensor_peaks = _alternative_peaks(mt, gyro, start, end)
            # A down-and-up nod has multiple speed peaks. If independently
            # strongest peaks disagree while substantial alternatives match,
            # this cannot establish a clock defect or justify a correction.
            ambiguous = (abs(visual[0] - sensor[0]) > 2 * P95_TARGET_S
                         and any(abs(v - m) <= P95_TARGET_S
                                 for v in visual_peaks for m in sensor_peaks))
            usable = usable and not ambiguous
            entry.update(
                usable=usable,
                video_ts=visual[0],
                motion_video_ts=sensor[0],
                motion_host_ts=sensor[0] + video_clock_origin,
                residual_s=visual[0] - sensor[0],
                visual_prominence=visual[1],
                motion_prominence=sensor[1],
            )
            if ambiguous:
                entry.update(reason="multiple nod phases: strongest peaks may be different movements",
                             ambiguous=True, visual_peak_times=visual_peaks, motion_peak_times=sensor_peaks)
            elif not usable:
                entry["reason"] = "peak not distinct enough to identify the same nod"
            else:
                residuals.append(visual[0] - sensor[0])
        result["markers"].append(entry)

    result["usable_markers"] = len(residuals)
    if len(residuals) < MIN_MARKERS:
        result.update(status="inconclusive", reason=f"fewer than {MIN_MARKERS} distinct markers")
        return result
    absolute = np.abs(residuals)
    median, p95 = float(np.median(absolute)), float(np.percentile(absolute, 95))
    result.update(
        median_absolute_residual_s=median,
        p95_absolute_residual_s=p95,
        status="consistent" if median <= MEDIAN_TARGET_S and p95 <= P95_TARGET_S else "exceeds_budget",
        note=(
            "Peak times are quantized to the 30 Hz analysis grid and include visual "
            "pose-estimation error. A human must still confirm the correspondence."
        ),
    )
    return result
