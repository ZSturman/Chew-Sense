"""High-recall visual food-entry proposal detector.

A proposal is emitted when a hand approaches the mouth, the mouth opens
during that approach, and the hand retracts.  These results are automatic
telemetry for review; they are not bites and do not define chewing bouts.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class FoodEntryCandidate:
    frame_idx: int
    confidence: float
    evidence: dict


def detect_food_entries(
    h2m: np.ndarray,
    jaw_open_smooth: np.ndarray,
    timestamps: np.ndarray,
    fps: float,
    cfg: dict,
) -> list[FoodEntryCandidate]:
    """Return independent food-entry proposals in video-clock time."""
    if len(h2m) == 0 or fps <= 0:
        return []
    enter = cfg["hands"]["proximity_enter"]
    exit_ = cfg["hands"]["proximity_exit"]
    entry_cfg = cfg["food_entry"]
    win = max(1, int(entry_cfg["approach_window_s"] * fps))
    open_min = entry_cfg["mouth_open_min_norm"]
    # Bridge brief gaps where h2m blips above exit_ (for example, finger
    # flutter) so one food-entry gesture does not become several proposals.
    bridge_frames = max(1, int(entry_cfg.get("between_run_merge_s", 0.4) * fps))
    merge_s = float(entry_cfg.get("candidate_merge_s", 0.8))

    # Low-percentile rolling baseline for the observable opening component.
    n = len(jaw_open_smooth)
    rest = np.zeros(n, dtype=np.float32)
    rwin = max(int(5 * fps), 9)
    for i in range(n):
        a = max(0, i - rwin)
        b = min(n, i + 1)
        segment = jaw_open_smooth[a:b]
        rest[i] = float(np.percentile(segment, 20)) if len(segment) else 0.0

    near = h2m < enter
    out: list[FoodEntryCandidate] = []
    i = 0
    while i < n:
        if not near[i]:
            i += 1
            continue
        j = i
        while j < n:
            if h2m[j] < exit_:
                j += 1
                continue
            k = j + 1
            while k < n and (k - j) <= bridge_frames and h2m[k] >= exit_:
                k += 1
            if k < n and h2m[k] < exit_ and (k - j) <= bridge_frames:
                j = k
                continue
            break

        if j - i < 2:
            i = j + 1
            continue
        opening = jaw_open_smooth[i:j] - rest[i:j]
        if opening.max() < open_min:
            i = j + 1
            continue
        peak = i + int(np.argmax(opening))
        half = max(1, win // 2)
        a = max(i, peak - half)
        b = min(j, peak + half + 1)
        retract_zone = h2m[peak:min(n, peak + half + 1)]
        retracted = (j >= n) or (
            np.isfinite(retract_zone).any() and (retract_zone >= exit_).any()
        ) or (j - i) <= win
        if not retracted:
            i = j + 1
            continue

        depth = float(enter - h2m[a:b].min())
        opening_score = float(min(1.0, opening.max() / (open_min * 3)))
        depth_score = float(min(1.0, max(0.0, depth) / enter))
        brevity = float(1.0 - min(1.0, (b - a) / win))
        confidence = 0.4 * opening_score + 0.4 * depth_score + 0.2 * brevity
        out.append(
            FoodEntryCandidate(
                frame_idx=peak,
                confidence=float(confidence),
                evidence={
                    "approach_start_frame": int(i),
                    "approach_end_frame": int(j),
                    "min_h2m": float(h2m[i:j].min()),
                    "max_opening": float(opening.max()),
                },
            )
        )
        i = j + 1

    if not out or merge_s <= 0:
        return out
    ordered = sorted(out, key=lambda candidate: candidate.frame_idx)
    merged: list[FoodEntryCandidate] = [ordered[0]]
    for candidate in ordered[1:]:
        previous = merged[-1]
        delta = abs(
            timestamps[candidate.frame_idx] - timestamps[previous.frame_idx]
        )
        if delta < merge_s:
            if candidate.confidence > previous.confidence:
                merged[-1] = candidate
        else:
            merged.append(candidate)
    return merged
