"""Phase B tests: mouth-occlusion detector + FSM occlusion-hold."""
from __future__ import annotations

import numpy as np

from chewsense.config import load_config
from chewsense.signals.mouth_calibration import calibrate
from chewsense.signals.mouth_state import detect_mouth_states
from chewsense.signals.occlusion import detect_mouth_occlusion


def _synth_chewing_clip(fps: float = 30.0, duration_s: float = 12.0):
    n = int(duration_s * fps)
    t = np.arange(n) / fps
    rng = np.random.default_rng(7)
    inner = (0.02 + rng.normal(0, 0.001, n)).astype(np.float32)
    outer = (0.12 + rng.normal(0, 0.001, n)).astype(np.float32)
    chin = (0.30 + rng.normal(0, 0.001, n)).astype(np.float32)
    aperture = inner / np.maximum(outer, 1e-4)
    bs = (0.05 + rng.normal(0, 0.003, n)).astype(np.float32)
    # Steady chew at 1.5 Hz across the whole clip.
    bs += 0.30 * (1 - np.cos(2 * np.pi * 1.5 * t)) / 2
    return t.astype(np.float64), inner, outer, aperture.astype(np.float32), chin, bs


def test_detect_mouth_occlusion_flags_hand_on_mouth():
    cfg = load_config()
    n = 90
    face_det = np.ones(n, dtype=bool)
    fc = np.full(n, 0.95, dtype=np.float32)
    h2m = np.full(n, 1.0, dtype=np.float32)  # hand far away
    bs = np.full(n, 0.10, dtype=np.float32)
    # Sustained hand-on-mouth from frame 30..60 (1s @ 30fps)
    h2m[30:60] = 0.02
    mask = detect_mouth_occlusion(
        fps=30.0, face_detected=face_det, face_confidence=fc,
        hand_to_mouth_min=h2m, jaw_open_bs=bs, cfg=cfg,
    )
    assert mask[40] and mask[55]
    assert not mask[10] and not mask[80]


def test_detect_mouth_occlusion_filters_singleton_spikes():
    cfg = load_config()
    n = 90
    face_det = np.ones(n, dtype=bool)
    fc = np.full(n, 0.95, dtype=np.float32)
    h2m = np.full(n, 1.0, dtype=np.float32)
    bs = np.full(n, 0.10, dtype=np.float32)
    # Single-frame blip: should NOT count as occlusion (min_run_s=0.15 -> ~5 frames)
    h2m[40] = 0.02
    mask = detect_mouth_occlusion(
        fps=30.0, face_detected=face_det, face_confidence=fc,
        hand_to_mouth_min=h2m, jaw_open_bs=bs, cfg=cfg,
    )
    assert not mask.any()


def test_fsm_holds_state_across_brief_occlusion():
    """A brief hand-over-mouth event should hold the prior state, not flip to unknown."""
    cfg = load_config()
    fps = 30.0
    t, inner, outer, aperture, chin, bs = _synth_chewing_clip(fps=fps)
    n = len(t)
    pose = ["frontal"] * n
    face_det = np.ones(n, dtype=bool)
    occ_only_face_loss = np.zeros(n, dtype=bool)
    # Insert a 0.3s occlusion (9 frames < 0.5s hold) at frame 200.
    occluded = occ_only_face_loss.copy()
    occluded[200:209] = True
    spk = np.zeros(n, dtype=bool)
    h2m = np.full(n, 5.0, dtype=np.float32)
    cal = calibrate(
        fps=fps, inner_lip=inner, outer_lip=outer, chin_to_nose=chin,
        pose_class=pose, face_detected=face_det, occluded=occ_only_face_loss,
        is_speaking=spk, h2m=h2m, cfg=cfg, jaw_open_bs=bs,
    )
    out = detect_mouth_states(
        fps=fps, timestamps=t, inner_lip_open=inner, outer_lip_open=outer,
        lip_aperture_ratio=aperture, chin_to_nose=chin,
        pose_class=pose, face_detected=face_det, occluded_flags=occluded,
        talking_flags=spk, calibration=cal, cfg=cfg, jaw_open_bs=bs,
    )
    # Default hold_s=0.5 (~15 frames). A 9-frame occlusion fits inside the hold.
    states_in_gap = out.state_per_frame[200:209]
    # No frame in the gap should be "unknown" (code 4).
    assert (states_in_gap != 4).all(), f"unexpected unknown in hold window: {states_in_gap}"


def test_fsm_emits_unknown_for_long_occlusion():
    """Occlusions longer than hold_s must collapse to unknown (the safe fallback)."""
    cfg = load_config()
    # Make hold short for the test
    cfg.setdefault("occlusion", {})["hold_s"] = 0.2  # 6 frames @ 30fps
    fps = 30.0
    t, inner, outer, aperture, chin, bs = _synth_chewing_clip(fps=fps)
    n = len(t)
    pose = ["frontal"] * n
    face_det = np.ones(n, dtype=bool)
    occluded = np.zeros(n, dtype=bool)
    occluded[200:240] = True  # ~1.3s occlusion
    spk = np.zeros(n, dtype=bool)
    h2m = np.full(n, 5.0, dtype=np.float32)
    cal = calibrate(
        fps=fps, inner_lip=inner, outer_lip=outer, chin_to_nose=chin,
        pose_class=pose, face_detected=face_det, occluded=np.zeros(n, dtype=bool),
        is_speaking=spk, h2m=h2m, cfg=cfg, jaw_open_bs=bs,
    )
    out = detect_mouth_states(
        fps=fps, timestamps=t, inner_lip_open=inner, outer_lip_open=outer,
        lip_aperture_ratio=aperture, chin_to_nose=chin,
        pose_class=pose, face_detected=face_det, occluded_flags=occluded,
        talking_flags=spk, calibration=cal, cfg=cfg, jaw_open_bs=bs,
    )
    # Beyond the hold window, the FSM must report unknown.
    tail = out.state_per_frame[210:240]
    assert (tail == 4).any(), "expected unknown frames beyond hold window"
