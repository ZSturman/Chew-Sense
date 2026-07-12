"""Tests for the chew-overlap repair invariant.

Both the Python `_phase_to_sin_cos` (used by the data-pipeline transform) and
the offline `apply_review_overrides` merger must enforce
``start_ts >= prev_end_ts + NEIGHBOR_GAP_S`` for every adjacent pair of chews.
This module verifies that overlapping inputs are sanitised — overlapping or
out-of-order chews trigger a logged warning and either get trimmed (the
merger) or skipped (`_phase_to_sin_cos`).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Make the vision review merger importable.
THIS_DIR = Path(__file__).resolve().parent
APPLY_DIR = THIS_DIR.parents[1] / "vision" / "scripts"
sys.path.insert(0, str(APPLY_DIR))

from pipeline.video_labels import _phase_to_sin_cos  # noqa: E402

import apply_review_overrides as aro  # noqa: E402


def test_phase_skips_overlapping_chew(caplog) -> None:
    rows = pd.DataFrame({"video_ts": np.linspace(0, 2.0, 201)})
    episodes = [{
        "chews": [
            {"id": "ch_0", "start_ts": 0.10, "peak_ts": 0.30, "end_ts": 0.50},
            # Overlaps ch_0 — must be dropped, not silently overwrite.
            {"id": "ch_1", "start_ts": 0.40, "peak_ts": 0.55, "end_ts": 0.70},
            {"id": "ch_2", "start_ts": 0.80, "peak_ts": 1.00, "end_ts": 1.20},
        ]
    }]

    with caplog.at_level(logging.WARNING, logger="pipeline.video_labels"):
        sin_v, cos_v = _phase_to_sin_cos(rows, episodes)

    # ch_1 should have been skipped; rows in (0.50, 0.80) keep the
    # default-zero "no signal" output, while ch_0 and ch_2 windows are filled.
    in_ch0 = (rows["video_ts"] >= 0.10) & (rows["video_ts"] <= 0.50)
    between = (rows["video_ts"] > 0.50) & (rows["video_ts"] < 0.80)
    in_ch2 = (rows["video_ts"] >= 0.80) & (rows["video_ts"] <= 1.20)
    assert np.any(np.abs(sin_v[in_ch0]) > 0)
    assert np.allclose(sin_v[between], 0.0)
    assert np.allclose(cos_v[between], 0.0)
    assert np.any(np.abs(sin_v[in_ch2]) > 0)
    assert any("overlaps prior chew" in rec.message for rec in caplog.records)


def test_repair_overlaps_trims_later_chew(caplog) -> None:
    chews = [
        aro.ChewEdit(id="ch_0", start_ts=0.10, peak_ts=0.30, end_ts=0.50),
        aro.ChewEdit(id="ch_1", start_ts=0.40, peak_ts=0.60, end_ts=0.80),
    ]
    with caplog.at_level(logging.WARNING, logger="apply_review_overrides"):
        repaired = aro._repair_overlaps(chews)
    assert len(repaired) == 2
    assert repaired[0].id == "ch_0"
    assert repaired[1].id == "ch_1"
    assert repaired[1].start_ts > repaired[0].end_ts
    assert repaired[1].start_ts == repaired[0].end_ts + aro.NEIGHBOR_GAP_S
    assert any("trimming start_ts" in rec.message for rec in caplog.records)


def test_repair_overlaps_drops_irreparable(caplog) -> None:
    chews = [
        aro.ChewEdit(id="ch_0", start_ts=0.10, peak_ts=0.30, end_ts=0.50),
        # Peak/end before ch_0 ends: cannot be trimmed without breaking
        # start < peak; must be dropped.
        aro.ChewEdit(id="ch_1", start_ts=0.20, peak_ts=0.35, end_ts=0.45),
    ]
    with caplog.at_level(logging.WARNING, logger="apply_review_overrides"):
        repaired = aro._repair_overlaps(chews)
    assert [c.id for c in repaired] == ["ch_0"]
    assert any("irreparably" in rec.message for rec in caplog.records)


def test_apply_overrides_repairs_overlaps_in_csv() -> None:
    rows = pd.DataFrame({
        "timestamp": np.linspace(0, 2.0, 201),
        "video_ts": np.linspace(0, 2.0, 201),
        "eating": np.zeros(201, dtype=int),
        "chew_phase": ["idle"] * 201,
        "chew_id": [""] * 201,
        "video_confidence": np.zeros(201),
    })
    overrides = aro.ReviewOverrides(
        session_id="t",
        added_chews=[
            aro.ChewEdit(id="ch_0", start_ts=0.10, peak_ts=0.30, end_ts=0.50),
            aro.ChewEdit(id="ch_1", start_ts=0.40, peak_ts=0.60, end_ts=0.80),
        ],
    )
    out = aro.apply_overrides(rows, overrides)

    # No video_ts row should be tagged with both chew ids; ch_1 must start
    # strictly after ch_0's end.
    ch0_rows = out.loc[out["chew_id"] == "ch_0", "video_ts"]
    ch1_rows = out.loc[out["chew_id"] == "ch_1", "video_ts"]
    assert ch0_rows.max() < ch1_rows.min()
