"""Validation tests for the Phase 1.5 MediaPipe-blendshape mouth-open channel.

These tests pin down the behavioural guarantees we promised the user:

1. When the legacy 2D channels (inner_lip + chin_to_nose) are flat but the
   `jawOpen` blendshape oscillates (sealed-lip chewing), cycles must still be
   detected and classified as ``closed_lip``.

2. At ``pose=other`` (head tilt outside frontal/profile/tilted_down bounds)
   the legacy code collapsed every frame to ``unknown``. With the blendshape
   trusted at all poses, the FSM must run normally and avoid all-unknown.
"""
from __future__ import annotations

import numpy as np

from chewsense.config import load_config
from chewsense.signals.mouth_calibration import calibrate
from chewsense.signals.mouth_state import detect_mouth_states


def _quiet(n: int, level: float, noise: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (level + rng.normal(0, noise, n)).astype(np.float32)


def _make_clip(
    *,
    fps: float = 30.0,
    duration_s: float = 12.0,
    bs_amp: float = 0.30,
    bs_rest: float = 0.05,
    freq_hz: float = 1.5,
    cycles_start_s: float = 5.5,
    n_cycles: int = 7,
    flat_legacy: bool = True,
):
    """Synthetic clip where blendshape oscillates but lip/chin channels are flat."""
    n = int(duration_s * fps)
    t = np.arange(n) / fps
    # Legacy 2D channels: barely move (sealed lips, no chin translation visible).
    if flat_legacy:
        inner = _quiet(n, 0.02, 0.0008, 1)
        outer = _quiet(n, 0.12, 0.0008, 2)
        chin = _quiet(n, 0.30, 0.0008, 3)
    else:
        inner = _quiet(n, 0.02, 0.001, 1)
        outer = _quiet(n, 0.12, 0.001, 2)
        chin = _quiet(n, 0.30, 0.001, 3)
    aperture = inner / np.maximum(outer, 1e-4)
    # Blendshape: clean bell-shaped pulses
    bs = _quiet(n, bs_rest, 0.003, 4)
    cycle_dur = n_cycles / freq_hz
    in_cycle = (t >= cycles_start_s) & (t < cycles_start_s + cycle_dur)
    phase = 2 * np.pi * freq_hz * (t - cycles_start_s)
    pulse = (1 - np.cos(phase)) / 2
    pulse[~in_cycle] = 0.0
    bs = bs + bs_amp * pulse
    return t.astype(np.float64), inner, outer, aperture.astype(np.float32), chin, bs.astype(np.float32)


def test_blendshape_drives_closed_lip_cycles_when_2d_channels_flat():
    """Sealed-lip chewing: legacy channels see nothing, blendshape sees everything."""
    cfg = load_config()
    fps = 30.0
    t, inner, outer, aperture, chin, bs = _make_clip(fps=fps)
    n = len(t)
    pose = ["frontal"] * n
    fd = np.ones(n, dtype=bool)
    occ = np.zeros(n, dtype=bool)
    spk = np.zeros(n, dtype=bool)
    h2m = np.full(n, 5.0, dtype=np.float32)
    cal = calibrate(
        fps=fps, inner_lip=inner, outer_lip=outer, chin_to_nose=chin,
        pose_class=pose, face_detected=fd, occluded=occ,
        is_speaking=spk, h2m=h2m, cfg=cfg, jaw_open_bs=bs,
    )
    # Sanity: blendshape baseline was learned
    assert cal.get("frontal").jaw_open_bs.n_samples > 0
    out = detect_mouth_states(
        fps=fps, timestamps=t,
        inner_lip_open=inner, outer_lip_open=outer,
        lip_aperture_ratio=aperture, chin_to_nose=chin,
        pose_class=pose, face_detected=fd, occluded_flags=occ,
        talking_flags=spk, calibration=cal, cfg=cfg, jaw_open_bs=bs,
    )
    assert len(out.cycles) >= 4, f"expected blendshape to drive cycles, got {len(out.cycles)}"
    closed = sum(1 for c in out.cycles if c.kind == "closed_lip")
    assert closed >= 3, f"expected most cycles to be closed_lip, got kinds={[c.kind for c in out.cycles]}"
    # And the consensus channel bitmask should record the blendshape was used.
    assert int((out.used_channels & 1).sum()) > 0


def test_other_pose_no_longer_kills_state_when_blendshape_present():
    """At pose=other, legacy FSM emitted all-unknown. Blendshape must rescue it."""
    cfg = load_config()
    fps = 30.0
    t, inner, outer, aperture, chin, bs = _make_clip(fps=fps)
    n = len(t)
    # Mark every frame as the "other" pose (extreme tilt outside all buckets)
    pose = ["other"] * n
    fd = np.ones(n, dtype=bool)
    occ = np.zeros(n, dtype=bool)
    spk = np.zeros(n, dtype=bool)
    h2m = np.full(n, 5.0, dtype=np.float32)
    cal = calibrate(
        fps=fps, inner_lip=inner, outer_lip=outer, chin_to_nose=chin,
        pose_class=pose, face_detected=fd, occluded=occ,
        is_speaking=spk, h2m=h2m, cfg=cfg, jaw_open_bs=bs,
    )
    out = detect_mouth_states(
        fps=fps, timestamps=t,
        inner_lip_open=inner, outer_lip_open=outer,
        lip_aperture_ratio=aperture, chin_to_nose=chin,
        pose_class=pose, face_detected=fd, occluded_flags=occ,
        talking_flags=spk, calibration=cal, cfg=cfg, jaw_open_bs=bs,
    )
    unknown_frac = float((out.state_per_frame == 4).mean())
    # Legacy behaviour: 1.0. New behaviour: blendshape rescues most frames.
    assert unknown_frac < 0.2, f"too many unknown frames at pose=other: {unknown_frac:.2f}"
    # And the FSM should have actually opened during the chew burst.
    assert int((out.state_per_frame == 2).sum()) + int((out.state_per_frame == 1).sum()) > 0


def test_legacy_path_still_works_without_blendshape():
    """Backwards compat: calling without `jaw_open_bs` keeps Phase 1 behaviour."""
    cfg = load_config()
    fps = 30.0
    # Use the same _synth from the existing FSM tests would be ideal; here we
    # just confirm that the new optional kwarg is truly optional.
    n = int(10 * fps)
    rng = np.random.default_rng(0)
    inner = 0.02 + rng.normal(0, 0.001, n).astype(np.float32)
    outer = 0.12 + rng.normal(0, 0.001, n).astype(np.float32)
    chin = 0.30 + rng.normal(0, 0.001, n).astype(np.float32)
    aperture = inner / np.maximum(outer, 1e-4)
    t = np.arange(n) / fps
    pose = ["frontal"] * n
    fd = np.ones(n, dtype=bool)
    occ = np.zeros(n, dtype=bool)
    spk = np.zeros(n, dtype=bool)
    h2m = np.full(n, 5.0, dtype=np.float32)
    cal = calibrate(
        fps=fps, inner_lip=inner, outer_lip=outer, chin_to_nose=chin,
        pose_class=pose, face_detected=fd, occluded=occ,
        is_speaking=spk, h2m=h2m, cfg=cfg,
    )
    out = detect_mouth_states(
        fps=fps, timestamps=t,
        inner_lip_open=inner, outer_lip_open=outer,
        lip_aperture_ratio=aperture, chin_to_nose=chin,
        pose_class=pose, face_detected=fd, occluded_flags=occ,
        talking_flags=spk, calibration=cal, cfg=cfg,
    )
    assert out.state_per_frame.shape == (n,)
    # No oscillation, so no cycles expected.
    assert len(out.cycles) == 0
