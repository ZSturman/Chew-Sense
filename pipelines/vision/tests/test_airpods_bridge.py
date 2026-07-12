"""Tests for the AirPods motion-alignment bridge."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from chewsense.events.schema import (
    Chew,
    Episode,
    SessionCounters,
    VideoEvents,
)
from chewsense.integrations.airpods_bridge import (
    align_events_to_motion,
    detect_motion_video_offset,
)
from chewsense.integrations.export_to_pipeline import export_session


def _make_motion_csv(tmp_path: Path, t_offset: float = 1000.0, fs: float = 100.0,
                     duration: float = 5.0) -> Path:
    n = int(duration * fs)
    ts = t_offset + np.arange(n) / fs
    df = pd.DataFrame({
        "timestamp": ts,
        "dtNs": np.full(n, int(1e9 / fs), dtype=np.int64),
        "ax": np.zeros(n), "ay": np.zeros(n), "az": np.zeros(n),
        "gx": np.zeros(n), "gy": np.zeros(n), "gz": np.zeros(n),
        "label": ["false"] * n,
    })
    p = tmp_path / "session.csv"
    df.to_csv(p, index=False)
    return p


def _make_events() -> VideoEvents:
    # One episode spanning video-time [1.0, 3.0]s with two chews inside.
    chews = [
        Chew(
            id="ch_0000",
            start_ts=1.0, peak_ts=1.5, end_ts=2.0,
            duration_s=1.0,
            amplitude=0.5, relative_amplitude=1.0,
            confidence=0.9,
        ),
        Chew(
            id="ch_0001",
            start_ts=2.0, peak_ts=2.4, end_ts=3.0,
            duration_s=1.0,
            amplitude=0.4, relative_amplitude=0.8,
            confidence=0.7,
        ),
    ]
    ep = Episode(
        id="ep_000",
        start_ts=1.0, end_ts=3.0,
        start_reason="chewing_without_observed_bite",
        end_reason="chewing_stopped",
        confidence=0.85,
        chews=chews,
    )
    return VideoEvents(
        video_id="synthetic",
        schema_version="1.0.0",
        duration_s=5.0,
        fps=30.0,
        width=1080, height=1920,
        episodes=[ep],
        cycle_counts=SessionCounters(),
    )


def test_detect_offset_uses_first_timestamp():
    ts = np.array([10.5, 10.51, 10.52, 10.53])
    assert detect_motion_video_offset(ts) == pytest.approx(10.5)


def test_align_eating_window(tmp_path: Path):
    csv = _make_motion_csv(tmp_path, t_offset=1000.0)
    events = _make_events()
    out = align_events_to_motion(csv, events)
    # Same row count as motion CSV.
    assert len(out) == 500
    # Eating must be 1 for rows whose video_ts is inside [1.0, 3.0].
    in_ep = (out["video_ts"] >= 1.0) & (out["video_ts"] <= 3.0)
    assert out.loc[in_ep, "eating"].eq(1).all()
    assert out.loc[~in_ep, "eating"].eq(0).all()


def test_align_chew_phase_cycles(tmp_path: Path):
    csv = _make_motion_csv(tmp_path, t_offset=1000.0)
    events = _make_events()
    out = align_events_to_motion(csv, events)

    # Within ch_0000, video_ts in [1.0, 1.5] -> opening, (1.5, 2.0] -> closing.
    opening = out[(out["chew_id"] == "ch_0000") & (out["chew_phase"] == "opening")]
    closing = out[(out["chew_id"] == "ch_0000") & (out["chew_phase"] == "closing")]
    assert (opening["video_ts"] <= 1.5).all()
    assert (opening["video_ts"] >= 1.0).all()
    assert (closing["video_ts"] > 1.5).all()
    assert (closing["video_ts"] <= 2.0).all()

    # Outside any chew -> idle, empty chew_id.
    idle = out[out["chew_phase"] == "idle"]
    assert (idle["chew_id"] == "").all()


def test_align_confidence_priority(tmp_path: Path):
    csv = _make_motion_csv(tmp_path, t_offset=1000.0)
    events = _make_events()
    out = align_events_to_motion(csv, events)
    # Inside a chew, confidence comes from the chew (0.9 or 0.7), not the
    # episode (0.85).
    ch0 = out[out["chew_id"] == "ch_0000"]
    assert np.allclose(ch0["video_confidence"], 0.9)
    ch1 = out[out["chew_id"] == "ch_0001"]
    assert np.allclose(ch1["video_confidence"], 0.7)
    # Inside the eating span but outside any chew, fall back to episode conf.
    in_ep_no_chew = out[
        (out["eating"] == 1) & (out["chew_id"] == "")
    ]
    if not in_ep_no_chew.empty:
        assert np.allclose(in_ep_no_chew["video_confidence"], 0.85)


def test_export_session_writes_both_files(tmp_path: Path):
    csv = _make_motion_csv(tmp_path, t_offset=1000.0)
    events = _make_events()
    out_dir = tmp_path / "out"
    summary = export_session(csv, events, out_dir)

    aligned_csv = out_dir / "motion_aligned_labels.csv"
    cycles_json = out_dir / "chew_cycles.json"
    assert aligned_csv.exists()
    assert cycles_json.exists()

    df = pd.read_csv(aligned_csv)
    assert {
        "timestamp", "video_ts", "eating", "mouth_state", "chew_phase",
        "chew_id", "bite_event", "bite_id", "bout_id", "label_valid",
        "label_source", "model_id", "annotation_revision", "video_confidence",
    } <= set(df.columns)
    assert len(df) == summary["n_motion_rows"]

    payload = json.loads(cycles_json.read_text())
    assert payload["video_id"] == "synthetic"
    assert payload["n_motion_rows"] == summary["n_motion_rows"]
    assert len(payload["episodes"]) == 1
    assert len(payload["episodes"][0]["chews"]) == 2


def test_align_explicit_offset_override(tmp_path: Path):
    csv = _make_motion_csv(tmp_path, t_offset=1000.0)
    events = _make_events()
    # Pretend the camera started 0.2s late: events should shift earlier.
    out = align_events_to_motion(csv, events, time_offset=1000.2)
    assert out.attrs["time_offset"] == pytest.approx(1000.2)
    assert np.allclose(out["video_ts"].iloc[0], -0.2)
