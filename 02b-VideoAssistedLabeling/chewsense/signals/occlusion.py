"""Mouth-occlusion detector.

Combines several signals into a single per-frame ``mouth_occluded`` mask:

* Face not detected (already captured by ``face_detected``).
* Low face confidence (blurry / partial face).
* Hand close to the mouth (``hand_to_mouth_min`` below threshold).
* ``jaw_open_bs`` blendshape unavailable (NaN) — the model failed to fit a
  mouth, usually because the lower face is covered.

The mask is intended to be OR-ed into the existing ``occluded_flags`` and then
passed into ``detect_mouth_states`` so the FSM can hold the previous state
across brief occlusions instead of collapsing to ``unknown``.
"""
from __future__ import annotations

import numpy as np


def detect_mouth_occlusion(
    *,
    fps: float,
    face_detected: np.ndarray,
    face_confidence: np.ndarray,
    hand_to_mouth_min: np.ndarray,
    jaw_open_bs: np.ndarray,
    cfg: dict,
) -> np.ndarray:
    """Return a per-frame boolean ``mouth_occluded`` mask.

    Parameters
    ----------
    face_detected : (n,) bool
    face_confidence : (n,) float in [0, 1]
    hand_to_mouth_min : (n,) float, normalized distance (smaller = closer)
    jaw_open_bs : (n,) float, NaN where blendshape was unavailable

    cfg keys read (with defaults):
        occlusion.face_conf_min : float = 0.4
        occlusion.h2m_close     : float = 0.06   # hand-on-mouth distance
        occlusion.min_run_s     : float = 0.15   # require sustained signal
    """
    occ_cfg = cfg.get("occlusion", {})
    face_conf_min = float(occ_cfg.get("face_conf_min", 0.4))
    h2m_close = float(occ_cfg.get("h2m_close", 0.06))
    min_run_s = float(occ_cfg.get("min_run_s", 0.15))

    n = len(face_detected)
    if n == 0:
        return np.zeros(0, dtype=bool)

    fc = np.asarray(face_confidence, dtype=np.float32)
    h2m = np.asarray(hand_to_mouth_min, dtype=np.float32)
    bs = np.asarray(jaw_open_bs, dtype=np.float32)

    low_conf = ~np.asarray(face_detected, dtype=bool) | (fc < face_conf_min)
    bs_missing = ~np.isfinite(bs)
    hand_on_mouth = np.isfinite(h2m) & (h2m < h2m_close)

    raw = low_conf | bs_missing | hand_on_mouth

    # Require sustained signal: dilate-by-min_run_frames in both directions
    # of agreement, i.e. drop singleton spikes shorter than min_run_s.
    min_run = max(1, int(round(min_run_s * fps)))
    if min_run <= 1:
        return raw

    out = np.zeros(n, dtype=bool)
    i = 0
    while i < n:
        if not raw[i]:
            i += 1
            continue
        j = i
        while j < n and raw[j]:
            j += 1
        if (j - i) >= min_run:
            out[i:j] = True
        i = j
    return out
