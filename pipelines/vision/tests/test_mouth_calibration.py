"""Auto-calibration tests."""
from __future__ import annotations

import numpy as np

from chewsense.config import load_config
from chewsense.signals.mouth_calibration import calibrate


def test_baselines_from_stable_window():
    cfg = load_config()
    fps = 30.0
    n = int(8 * fps)  # 8s clip
    # Quiet rest: small noise around 0.05 (inner), 0.10 (outer), 0.30 (chin)
    rng = np.random.default_rng(42)
    inner = 0.05 + rng.normal(0, 0.001, n).astype(np.float32)
    outer = 0.10 + rng.normal(0, 0.001, n).astype(np.float32)
    chin = 0.30 + rng.normal(0, 0.001, n).astype(np.float32)
    pose = ["frontal"] * n
    fd = np.ones(n, dtype=bool)
    occ = np.zeros(n, dtype=bool)
    spk = np.zeros(n, dtype=bool)
    h2m = np.full(n, 5.0, dtype=np.float32)  # hands far away

    res = calibrate(
        fps=fps,
        inner_lip=inner,
        outer_lip=outer,
        chin_to_nose=chin,
        pose_class=pose,
        face_detected=fd,
        occluded=occ,
        is_speaking=spk,
        h2m=h2m,
        cfg=cfg,
    )
    pb = res.get("frontal")
    assert abs(pb.inner_lip.median - 0.05) < 0.005
    assert abs(pb.chin_to_nose.median - 0.30) < 0.005
    assert pb.inner_lip.source == "calibration_window"


def test_fallback_when_no_stable_window():
    cfg = load_config()
    fps = 30.0
    n = int(8 * fps)
    inner = np.full(n, 0.05, dtype=np.float32)
    outer = np.full(n, 0.10, dtype=np.float32)
    chin = np.full(n, 0.30, dtype=np.float32)
    pose = ["frontal"] * n
    fd = np.ones(n, dtype=bool)
    # everything is speaking inside the calibration window -> no stable samples
    spk = np.ones(n, dtype=bool)
    spk[int(5 * fps):] = False  # after window, quiet
    occ = np.zeros(n, dtype=bool)
    h2m = np.full(n, 5.0, dtype=np.float32)

    res = calibrate(
        fps=fps, inner_lip=inner, outer_lip=outer, chin_to_nose=chin,
        pose_class=pose, face_detected=fd, occluded=occ,
        is_speaking=spk, h2m=h2m, cfg=cfg,
    )
    pb = res.get("frontal")
    # Should have fallen back to full-clip stable samples (the quiet tail)
    assert pb.inner_lip.n_samples > 0
    assert any("not stable" in w for w in res.warnings)
