"""FSM + cycle detector tests using synthetic mouth signals."""
from __future__ import annotations

import numpy as np

from chewsense.config import load_config
from chewsense.signals.mouth_calibration import calibrate
from chewsense.signals.mouth_state import detect_mouth_states


def _synth(
    *,
    fps: float = 30.0,
    duration_s: float = 12.0,
    n_cycles: int = 8,
    freq_hz: float = 1.5,
    inner_amp: float = 0.06,
    outer_amp: float = 0.08,
    chin_amp: float = 0.0,
    inner_rest: float = 0.02,
    outer_rest: float = 0.12,
    chin_rest: float = 0.30,
    noise: float = 0.001,
    cycles_start_s: float = 5.5,
):
    rng = np.random.default_rng(0)
    n = int(duration_s * fps)
    t = np.arange(n) / fps
    inner = np.full(n, inner_rest, dtype=np.float32)
    outer = np.full(n, outer_rest, dtype=np.float32)
    chin = np.full(n, chin_rest, dtype=np.float32)
    cycle_dur = n_cycles / freq_hz
    in_cycle = (t >= cycles_start_s) & (t < cycles_start_s + cycle_dur)
    phase = 2 * np.pi * freq_hz * (t - cycles_start_s)
    pulse = (1 - np.cos(phase)) / 2
    pulse[~in_cycle] = 0.0
    inner = inner + inner_amp * pulse
    outer = outer + outer_amp * pulse
    chin = chin + chin_amp * pulse
    inner += rng.normal(0, noise, n).astype(np.float32)
    outer += rng.normal(0, noise, n).astype(np.float32)
    chin += rng.normal(0, noise, n).astype(np.float32)
    return t.astype(np.float64), inner.astype(np.float32), outer.astype(np.float32), chin.astype(np.float32)


def _common_inputs(fps, n):
    pose = ["frontal"] * n
    fd = np.ones(n, dtype=bool)
    occ = np.zeros(n, dtype=bool)
    spk = np.zeros(n, dtype=bool)
    h2m = np.full(n, 5.0, dtype=np.float32)
    return pose, fd, occ, spk, h2m


def test_visible_open_cycles_detected_and_classified():
    cfg = load_config()
    fps = 30.0
    t, inner, outer, chin = _synth(fps=fps, n_cycles=8, inner_amp=0.06, outer_amp=0.10, chin_amp=0.04)
    aperture = inner / np.maximum(outer, 1e-4)
    pose, fd, occ, spk, h2m = _common_inputs(fps, len(t))
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
    assert 5 <= len(out.cycles) <= 10, len(out.cycles)
    visible = sum(1 for c in out.cycles if c.kind == "visible_open")
    assert visible >= 4
    # FSM hit 'open' state during the active stretch
    assert int((out.state_per_frame == 2).sum()) > 0


def test_closed_lip_cycles_detected():
    cfg = load_config()
    fps = 30.0
    # Lips barely move (outer/inner small), but chin->nose drops noticeably
    t, inner, outer, chin = _synth(
        fps=fps, n_cycles=6, inner_amp=0.003, outer_amp=0.004, chin_amp=0.08,
    )
    aperture = inner / np.maximum(outer, 1e-4)
    pose, fd, occ, spk, h2m = _common_inputs(fps, len(t))
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
    assert len(out.cycles) >= 2
    closed_lip = sum(1 for c in out.cycles if c.kind == "closed_lip")
    visible = sum(1 for c in out.cycles if c.kind == "visible_open")
    # Should prefer 'closed_lip' over 'visible_open' here
    assert closed_lip >= visible
    assert closed_lip >= 1


def test_talking_cycles_rejected():
    cfg = load_config()
    fps = 30.0
    t, inner, outer, chin = _synth(fps=fps, n_cycles=8, inner_amp=0.06, outer_amp=0.10, chin_amp=0.04)
    aperture = inner / np.maximum(outer, 1e-4)
    pose, fd, occ, spk, h2m = _common_inputs(fps, len(t))
    # Mark talking during the active window
    spk = ((t >= 5.5) & (t < 11.0)).astype(bool)
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
    assert len(out.cycles) == 0


def test_occlusion_produces_unknown_state():
    cfg = load_config()
    fps = 30.0
    t, inner, outer, chin = _synth(fps=fps, n_cycles=4, inner_amp=0.06, outer_amp=0.10, chin_amp=0.04)
    aperture = inner / np.maximum(outer, 1e-4)
    pose, fd, occ, spk, h2m = _common_inputs(fps, len(t))
    occ[int(7 * fps):int(9 * fps)] = True
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
    assert int((out.state_per_frame[occ] == 4).sum()) > 0
