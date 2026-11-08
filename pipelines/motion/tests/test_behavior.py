"""Chewing-behaviour foundation: synthetic checks of the arithmetic only."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.behavior.cycle_context import apply_table, evaluate, fit_table, rhythm_neighbors  # noqa: E402
from pipeline.behavior.measures import (  # noqa: E402
    REGISTRY,
    bouts_from_states,
    cadence_track,
    dominant_frequency,
    summarize_bouts,
)


def test_registry_separates_measurable_from_not():
    names = {d.name: d for d in REGISTRY}
    assert names["bite_events"].observability == "not_measurable"
    assert names["individual_chew_events"].observability == "not_measurable"
    assert names["chewing_cadence"].observability == "inferred"
    assert all(d.reference and d.needed for d in REGISTRY)


def test_bouts_use_window_centres_and_pauses():
    t = np.arange(3.0, 60.0, 0.5)
    states = ((t > 10) & (t < 20)) | ((t > 30) & (t < 45))
    bouts = bouts_from_states(t, states, window_s=3.0)
    assert bouts == [(10.5 - 1.5, 20.0 - 1.5), (30.5 - 1.5, 45.0 - 1.5)]
    summary = summarize_bouts(bouts, [], 60.0)
    assert summary["chewing_bouts"] == 2 and summary["pauses_s"] == [10.5]
    assert summary["cadence_hz_median"] is None  # no cadence without motion


def test_cadence_recovers_a_chewing_rhythm():
    rate = 50.0
    t = np.arange(0, 30, 1 / rate)
    imu = np.zeros((len(t), 6), dtype=np.float32)
    imu[:, 3] = 0.3 * np.sin(2 * np.pi * 1.6 * t)
    hz, share = dominant_frequency(imu[:, 3], rate)
    assert abs(hz - 1.6) < 0.05 and share > 0.1
    track = cadence_track(t, imu, [(0.0, 30.0)])
    assert track and all(abs(row["hz"] - 1.6) < 0.1 for row in track)


def test_rhythm_neighbors_and_calibration_table():
    ends = [1.0, 1.7, 2.4, 3.1, 10.0]
    assert list(rhythm_neighbors(ends)) == [2, 3, 3, 2, 0]
    rows = (
        [{"session_id": "a", "neighbors": 0, "in_chewing": 0, "fixed_confidence": 0.75, "kind": "closed_lip"}] * 8
        + [{"session_id": "b", "neighbors": 3, "in_chewing": 1, "fixed_confidence": 0.75, "kind": "closed_lip"}] * 8
    )
    table = fit_table(rows)
    assert table["0"]["probability"] == 0.1 and table["2"]["probability"] == 0.9
    assert list(apply_table(table, [0, 1, 5])) == [0.1, 0.5, 0.9]
    result = evaluate(rows)
    assert result["fixed_constants"]["auc_roc"] == 0.5
    assert result["meaning"].startswith("P(proposed cycle lies inside")


def test_chew_count_against_verified_chews_reports_cadence_and_span_padding():
    from types import SimpleNamespace

    from pipeline.behavior.validate import chew_count_against_verified

    rate = 50.0
    t = np.arange(0, 40, 1 / rate)
    imu = np.zeros((len(t), 6), dtype=np.float32)
    chewing = (t >= 10) & (t < 30)
    imu[chewing, 3] = 0.3 * np.sin(2 * np.pi * 1.5 * t[chewing])
    session = SimpleNamespace(session_id="S", clock="shared_host_clock", motion_t=t, imu=imu, duration_s=40.0)
    truth = np.arange(10.0, 30.0, 1 / 1.5)  # 30 chews at 1.5 Hz
    # Adjacent human spans are merged; the second span pads 2 s past the last chew.
    result = chew_count_against_verified(session, [(10.0, 20.0), (20.0, 30.0)], truth)
    span = result["spans"][0]
    assert len(result["spans"]) == 1 and span["verified_chews"] == 30
    assert abs(span["motion_cadence_hz"] - 1.5) < 0.1 and abs(span["cadence_relative_error"]) < 0.07
    assert abs(span["count_error"]) <= 2
    padded = chew_count_against_verified(session, [(10.0, 32.0)], truth)
    assert padded["spans"][0]["count_error"] > span["count_error"]  # padding inflates the count
    assert padded["outside_spans"] == 0 and not padded["caveats"] == []
