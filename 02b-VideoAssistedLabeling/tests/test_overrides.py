"""Tests for the authoritative override merger."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

# The script is reachable as a module because the repo root + the scripts dir
# are not on sys.path by default — we use file-based imports instead.
import importlib.util
import sys

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply_review_overrides.py"
spec = importlib.util.spec_from_file_location("apply_review_overrides", SCRIPT)
assert spec and spec.loader
aro = importlib.util.module_from_spec(spec)
sys.modules["apply_review_overrides"] = aro
spec.loader.exec_module(aro)  # type: ignore[union-attr]


def _make_aligned(n: int = 100, fs: float = 50.0) -> pd.DataFrame:
    import numpy as np
    ts = np.arange(n) / fs
    return pd.DataFrame({
        "timestamp": 1000.0 + ts,
        "video_ts": ts,
        "eating": 0,
        "chew_phase": "idle",
        "chew_id": "",
        "video_confidence": 0.0,
    })


def _write_overrides(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "ov.json"
    p.write_text(json.dumps(payload))
    return p


def test_add_chew_stamps_opening_and_closing(tmp_path: Path):
    df = _make_aligned()
    ov = aro.ReviewOverrides.load(_write_overrides(tmp_path, {
        "session_id": "t",
        "verified": False,
        "added_chews": [
            {"id": "manual_0", "start_ts": 0.4, "peak_ts": 0.6, "end_ts": 0.8, "confidence": 0.9}
        ],
    }))
    out = aro.apply_overrides(df, ov)

    opening = out[(out["chew_id"] == "manual_0") & (out["chew_phase"] == "opening")]
    closing = out[(out["chew_id"] == "manual_0") & (out["chew_phase"] == "closing")]
    assert (opening["video_ts"] >= 0.4).all() and (opening["video_ts"] <= 0.6).all()
    assert (closing["video_ts"] > 0.6).all() and (closing["video_ts"] <= 0.8).all()
    assert (out.loc[out["chew_id"] == "manual_0", "video_confidence"] == 0.9).all()


def test_delete_chew_clears_phase_and_id(tmp_path: Path):
    df = _make_aligned()
    df.loc[10:20, "chew_id"] = "ch_0000"
    df.loc[10:20, "chew_phase"] = "opening"

    ov = aro.ReviewOverrides.load(_write_overrides(tmp_path, {
        "session_id": "t", "verified": False,
        "deleted_chew_ids": ["ch_0000"],
    }))
    out = aro.apply_overrides(df, ov)
    assert (out["chew_id"] != "ch_0000").all()
    assert (out.loc[10:20, "chew_phase"] == "idle").all()


def test_flip_eating_inverts_within_span(tmp_path: Path):
    df = _make_aligned()
    df.loc[10:30, "eating"] = 1
    ov = aro.ReviewOverrides.load(_write_overrides(tmp_path, {
        "session_id": "t", "verified": False,
        "flipped_eating_spans": [{"start_ts": 0.2, "end_ts": 0.6}],
    }))
    out = aro.apply_overrides(df, ov)
    # Indices 10..30 -> video_ts 0.2..0.6 (50Hz). They were 1, should become 0.
    flipped = out[(out["video_ts"] >= 0.2) & (out["video_ts"] <= 0.6)]
    assert (flipped["eating"] == 0).all()
    # Outside the span, unchanged.
    untouched = out[(out["video_ts"] < 0.2) | (out["video_ts"] > 0.6)]
    assert (untouched["eating"] == 0).all()


def test_unreliable_overrides_chew_assignment(tmp_path: Path):
    df = _make_aligned()
    ov = aro.ReviewOverrides.load(_write_overrides(tmp_path, {
        "session_id": "t", "verified": False,
        "added_chews": [
            {"id": "x", "start_ts": 0.4, "peak_ts": 0.6, "end_ts": 0.8}
        ],
        "unreliable_spans": [{"start_ts": 0.5, "end_ts": 0.7}],
    }))
    out = aro.apply_overrides(df, ov)
    span = out[(out["video_ts"] >= 0.5) & (out["video_ts"] <= 0.7)]
    assert (span["chew_phase"] == "unreliable").all()
    assert (span["chew_id"] == "").all()


def test_merge_session_writes_csv_and_snapshot(tmp_path: Path):
    df = _make_aligned()
    aligned = tmp_path / "motion_aligned_labels.csv"
    df.to_csv(aligned, index=False)

    overrides = {
        "session_id": "t", "verified": True,
        "added_chews": [{"id": "manual_0", "start_ts": 0.2, "peak_ts": 0.3, "end_ts": 0.4}],
    }
    ov_path = tmp_path / "review_overrides.json"
    ov_path.write_text(json.dumps(overrides))

    summary = aro.merge_session(aligned, ov_path)
    assert summary["verified"] is True
    assert summary["n_added"] == 1
    assert summary["n_chew_rows"] > 0

    # Snapshot exists, and contains the pre-merge data (no chew rows).
    snap = tmp_path / "motion_aligned_labels.auto.csv"
    assert snap.exists()
    snap_df = pd.read_csv(snap)
    assert (snap_df["chew_id"].fillna("") == "").all()

    # Re-running shouldn't overwrite the snapshot.
    snap_mtime = snap.stat().st_mtime
    aro.merge_session(aligned, ov_path)
    assert snap.stat().st_mtime == snap_mtime


def test_missing_columns_raises():
    df = pd.DataFrame({"timestamp": [0.0], "video_ts": [0.0]})
    ov = aro.ReviewOverrides(session_id="t")
    with pytest.raises(ValueError):
        aro.apply_overrides(df, ov)


def test_bite_edits_use_nearest_row_and_verified_source(tmp_path: Path):
    df = _make_aligned()
    df["bite_event"] = 0
    df["bite_id"] = ""
    df["label_source"] = "automatic"
    df.loc[10, ["bite_event", "bite_id"]] = [1, "bt_auto"]

    ov = aro.ReviewOverrides.load(_write_overrides(tmp_path, {
        "session_id": "t",
        "verified": True,
        "full_video_pass_completed": True,
        "deleted_bite_ids": ["bt_auto"],
        "added_bites": [{"id": "bt_manual", "ts": 0.55, "confidence": 0.8}],
    }))
    out = aro.apply_overrides(df, ov)

    assert "bt_auto" not in set(out["bite_id"])
    event_rows = out[out["bite_event"] == 1]
    assert len(event_rows) == 1
    assert event_rows.iloc[0]["bite_id"] == "bt_manual"
    assert abs(event_rows.iloc[0]["video_ts"] - 0.55) <= 0.011
    assert (out["label_source"] == "human_reviewed").all()
