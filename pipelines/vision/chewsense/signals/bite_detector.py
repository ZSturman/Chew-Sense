"""Bite (food-entry) detector.

A bite is emitted when a hand approaches the mouth (distance crosses below
`proximity_enter`), the mouth opens during that approach, and the hand retracts
(distance returns above `proximity_exit`) within `approach_window_s`.

The detector consumes per-frame arrays and emits a list of bite candidates as
(peak_frame_idx, ts, confidence, evidence). The pipeline assigns final ids and
links them to episodes in the state machine.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class BiteCandidate:
    frame_idx: int
    confidence: float
    evidence: dict


def detect_bites(
    h2m: np.ndarray,           # per-frame normalized hand-to-mouth min distance (inf where no hand)
    jaw_open_smooth: np.ndarray,
    timestamps: np.ndarray,
    fps: float,
    cfg: dict,
) -> list[BiteCandidate]:
    if len(h2m) == 0 or fps <= 0:
        return []
    enter = cfg["hands"]["proximity_enter"]
    exit_ = cfg["hands"]["proximity_exit"]
    win = max(1, int(cfg["bite"]["approach_window_s"] * fps))
    open_min = cfg["bite"]["mouth_open_min_norm"]
    # Bridge brief gaps where h2m blips above exit_ (eg, finger flutter) so we
    # don't split one bite gesture into multiple near-runs.
    bridge_frames = max(1, int(cfg["bite"].get("between_run_merge_s", 0.4) * fps))
    # Post-detection NMS: collapse bite candidates that are closer in time than
    # this into a single, highest-confidence event.
    merge_s = float(cfg["bite"].get("candidate_merge_s", 0.8))

    # Resting jaw-open: a low-percentile baseline over a 5-second rolling window
    n = len(jaw_open_smooth)
    rest = np.zeros(n, dtype=np.float32)
    rwin = max(int(5 * fps), 9)
    for i in range(n):
        a = max(0, i - rwin); b = min(n, i + 1)
        seg = jaw_open_smooth[a:b]
        rest[i] = float(np.percentile(seg, 20)) if len(seg) > 0 else 0.0

    near = h2m < enter
    out: list[BiteCandidate] = []
    i = 0
    while i < n:
        if not near[i]:
            i += 1
            continue
        # Extend through hysteresis: stay "in approach" while h2m < exit_,
        # AND bridge brief gaps where h2m pops above exit_ for fewer than
        # `bridge_frames` (typical hand flutter / detection blip).
        j = i
        while j < n:
            if h2m[j] < exit_:
                j += 1
                continue
            # h2m[j] >= exit_: look ahead `bridge_frames` to see if we re-enter
            # the approach zone. If so, treat as a single contiguous run.
            k = j + 1
            while k < n and (k - j) <= bridge_frames and h2m[k] >= exit_:
                k += 1
            if k < n and h2m[k] < exit_ and (k - j) <= bridge_frames:
                j = k  # bridge the gap, keep extending
                continue
            break

        if j - i < 2:
            i = j + 1
            continue
        seg_open = jaw_open_smooth[i:j]
        seg_rest = rest[i:j]
        opening = seg_open - seg_rest
        # The bite event is anchored at the peak of `opening` within the run;
        # we only require that a `win`-frame neighbourhood AROUND that peak
        # contains the approach+retraction (real bites can linger past `win`).
        if opening.max() < open_min:
            i = j + 1
            continue
        peak_local = int(np.argmax(opening))
        peak = i + peak_local
        # Local approach window centred on the peak: [peak-win/2, peak+win/2].
        half = max(1, win // 2)
        a = max(i, peak - half)
        b = min(j, peak + half + 1)
        # Retraction: h2m must climb back above exit_ at or shortly after the
        # peak (within the local window, OR by the end of the run).
        retract_zone = h2m[peak:min(n, peak + half + 1)]
        retracted = (j >= n) or (
            np.isfinite(retract_zone).any() and (retract_zone >= exit_).any()
        ) or (j - i) <= win  # short clean runs pass automatically
        if not retracted:
            i = j + 1
            continue

        # confidence: combine opening size, brevity of approach, depth of approach
        depth = float(enter - h2m[a:b].min())  # smaller h2m -> bigger number
        opening_score = float(min(1.0, opening.max() / (open_min * 3)))
        depth_score = float(min(1.0, max(0.0, depth) / enter))
        brevity = float(1.0 - min(1.0, (b - a) / win))
        conf = (0.4 * opening_score + 0.4 * depth_score + 0.2 * brevity)
        out.append(
            BiteCandidate(
                frame_idx=peak,
                confidence=float(conf),
                evidence={
                    "approach_start_frame": int(i),
                    "approach_end_frame": int(j),
                    "min_h2m": float(h2m[i:j].min()),
                    "max_opening": float(opening.max()),
                },
            )
        )
        i = j + 1

    # Non-maximum suppression: if two candidates land within `merge_s`, keep
    # the higher-confidence one. Fragmented near-runs that survived bridging
    # can still produce duplicate candidates; this collapses them.
    if not out or merge_s <= 0:
        return out
    out_sorted = sorted(out, key=lambda c: c.frame_idx)
    merged: list[BiteCandidate] = [out_sorted[0]]
    for c in out_sorted[1:]:
        prev = merged[-1]
        dt = abs(timestamps[c.frame_idx] - timestamps[prev.frame_idx])
        if dt < merge_s:
            if c.confidence > prev.confidence:
                merged[-1] = c
        else:
            merged.append(c)
    return merged
