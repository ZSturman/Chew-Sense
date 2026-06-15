"""Head-pose classification + hysteresis tests."""
from __future__ import annotations

import math

import numpy as np

from chewsense.config import load_config
from chewsense.signals.head_pose import classify_pose, trust_channels


def _rad(deg: float) -> float:
    return math.radians(deg)


def test_frontal_profile_tilted_basic():
    cfg = load_config()
    yaws = np.array([_rad(0), _rad(0), _rad(0), _rad(0), _rad(0)], dtype=np.float32)
    pitches = np.array([_rad(0), _rad(0), _rad(0), _rad(0), _rad(0)], dtype=np.float32)
    fd = np.ones(5, dtype=bool)
    out = classify_pose(yaws, pitches, fd, cfg)
    assert out[-1] == "frontal"

    yaws = np.full(10, _rad(60), dtype=np.float32)
    pitches = np.zeros(10, dtype=np.float32)
    out = classify_pose(yaws, pitches, np.ones(10, dtype=bool), cfg)
    assert out[-1] == "profile"

    yaws = np.zeros(10, dtype=np.float32)
    pitches = np.full(10, _rad(30), dtype=np.float32)
    out = classify_pose(yaws, pitches, np.ones(10, dtype=bool), cfg)
    assert out[-1] == "tilted_down"


def test_hysteresis_holds_state():
    cfg = load_config()
    # 10 frontal frames, 1 spurious profile frame, then back to frontal
    yaws = np.array([_rad(0)] * 10 + [_rad(60)] + [_rad(0)] * 5, dtype=np.float32)
    pitches = np.zeros_like(yaws)
    fd = np.ones_like(yaws, dtype=bool)
    out = classify_pose(yaws, pitches, fd, cfg)
    # single-frame blip should not flip state because hysteresis_frames=3
    assert out[10] == "frontal"


def test_trust_channels():
    assert trust_channels("frontal")["inner_lip"] is True
    assert trust_channels("frontal")["jaw_proxy"] is True
    assert trust_channels("profile")["inner_lip"] is False
    assert trust_channels("profile")["jaw_proxy"] is True
    assert trust_channels("other")["inner_lip"] is False
