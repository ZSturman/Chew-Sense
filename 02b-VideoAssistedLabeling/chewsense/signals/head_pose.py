"""Per-frame head-pose classification with simple hysteresis.

Inputs are the rough geometric `head_yaw` / `head_pitch` already computed in
`features.face` (radians, see the formulas there). The output is a categorical
label that downstream signals use to pick which mouth-aperture channel to
trust:

- ``frontal``      — small yaw, normal pitch; both inner-lip and chin/jaw
  channels are reliable.
- ``profile``      — large |yaw|; inner-lip distance becomes unreliable in 2D,
  so only the chin/jaw-translation proxy is trusted.
- ``tilted_down``  — strong negative pitch (head pitched down toward a plate);
  both channels usable but signal will be noisier.
- ``other``        — any frame outside the above; the FSM collapses to
  ``unknown`` here.

A short hysteresis (``hysteresis_frames``) requires the *new* pose to be
observed for that many consecutive frames before the classifier switches,
which keeps the HUD steady when the subject briefly turns.
"""
from __future__ import annotations

import math

import numpy as np

PoseClass = str  # one of: "frontal" | "profile" | "tilted_down" | "other"


def _classify_one(yaw_rad: float, pitch_rad: float, cfg: dict) -> PoseClass:
    p = cfg["pose"]
    yaw_deg = abs(math.degrees(yaw_rad))
    pitch_deg = math.degrees(pitch_rad)
    if yaw_deg >= p["profile_yaw_min_deg"]:
        return "profile"
    if pitch_deg >= p["tilted_down_pitch_min_deg"]:
        # MediaPipe-derived pitch is positive when nose-y > eye-mid-y
        # (i.e. head tipped down toward a plate).
        return "tilted_down"
    if yaw_deg <= p["frontal_yaw_max_deg"] and abs(pitch_deg) <= p["frontal_pitch_abs_max_deg"]:
        return "frontal"
    return "other"


def classify_pose(
    head_yaw: np.ndarray,
    head_pitch: np.ndarray,
    face_detected: np.ndarray,
    cfg: dict,
) -> list[PoseClass]:
    """Return one pose label per frame, applying hysteresis."""
    n = len(head_yaw)
    if n == 0:
        return []
    hold = max(1, int(cfg["pose"].get("hysteresis_frames", 3)))
    out: list[PoseClass] = []
    current: PoseClass = "other"
    pending: PoseClass | None = None
    pending_count = 0
    for i in range(n):
        if not bool(face_detected[i]):
            raw: PoseClass = "other"
        else:
            raw = _classify_one(float(head_yaw[i]), float(head_pitch[i]), cfg)
        if raw == current:
            pending = None
            pending_count = 0
        else:
            if raw == pending:
                pending_count += 1
            else:
                pending = raw
                pending_count = 1
            if pending_count >= hold:
                current = raw
                pending = None
                pending_count = 0
        out.append(current)
    return out


def trust_channels(pose_class: PoseClass) -> dict[str, bool]:
    """Which mouth-aperture channels to trust at this pose.

    Returns ``{"inner_lip": bool, "jaw_proxy": bool, "jaw_open_bs": bool}``.

    The MediaPipe `jawOpen` blendshape is derived from the 3D mesh fit, not
    the raw 2D landmarks, so it is robust to head pose AND to whether the lips
    actually separate. We therefore trust it at every pose where a face is
    visible at all — including the previously-fatal ``other`` bucket.
    """
    if pose_class == "profile":
        return {"inner_lip": False, "jaw_proxy": True, "jaw_open_bs": True}
    if pose_class in ("frontal", "tilted_down"):
        return {"inner_lip": True, "jaw_proxy": True, "jaw_open_bs": True}
    # "other": legacy 2D channels are unreliable, but the blendshape still is.
    return {"inner_lip": False, "jaw_proxy": False, "jaw_open_bs": True}
