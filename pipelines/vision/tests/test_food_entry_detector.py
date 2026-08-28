from __future__ import annotations

import numpy as np

from chewsense.config import load_config
from chewsense.signals.food_entry_detector import detect_food_entries


def test_food_entry_detector_emits_independent_proposal():
    fps = 30.0
    timestamps = np.arange(120, dtype=np.float64) / fps
    hand_to_mouth = np.full(120, 2.0, dtype=np.float32)
    hand_to_mouth[30:60] = 0.2
    jaw_open = np.full(120, 0.05, dtype=np.float32)
    jaw_open[30:45] = np.linspace(0.05, 0.20, 15, dtype=np.float32)
    jaw_open[45:60] = np.linspace(0.20, 0.05, 15, dtype=np.float32)

    proposals = detect_food_entries(
        hand_to_mouth,
        jaw_open,
        timestamps,
        fps,
        load_config(),
    )

    assert len(proposals) == 1
    assert 40 <= proposals[0].frame_idx <= 50
    assert proposals[0].confidence > 0
    assert proposals[0].evidence["approach_start_frame"] == 30


def test_food_entry_detector_handles_empty_input():
    empty = np.array([], dtype=np.float32)
    assert detect_food_entries(empty, empty, empty, 30.0, load_config()) == []
