"""Tests for Phase-4 video-label overlay in pipeline/transform.py."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.config import SoftLabelsConfig
from pipeline.transform import transform_csv
from pipeline.video_labels import (
    find_artifacts,
    overlay_video_labels,
)


def _write_raw_csv(path: Path, n: int = 200, fs: float = 100.0, label: int = 0) -> None:
    t = np.arange(n) / fs
    df = pd.DataFrame({
        "timestamp": t,
        "dtNs": np.full(n, int(1e9 / fs), dtype=np.int64),
        "ax": np.zeros(n),
        "ay": np.zeros(n),
        "az": np.full(n, 9.81),
        "gx": np.zeros(n),
        "gy": np.zeros(n),
        "gz": np.zeros(n),
        "label": np.full(n, label, dtype=int),
    })
    df.to_csv(path, index=False)


def _write_video_artifacts(
    derived_root: Path,
    session_id: str,
    n: int = 200,
    fs: float = 100.0,
    eating_window: tuple[float, float] = (0.5, 1.5),
) -> None:
    sess_dir = derived_root / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)

    t = np.arange(n) / fs
    eating = ((t >= eating_window[0]) & (t < eating_window[1])).astype(int)
    chew_phase = np.where(eating, "opening", "idle")
    chew_id = np.where(eating, "ep0_chew0", "")
    conf = np.where(eating, 0.9, 0.0)

    aligned = pd.DataFrame({
        "timestamp": t,
        "video_ts": t,
        "eating": eating,
        "chew_phase": chew_phase,
        "chew_id": chew_id,
        "video_confidence": conf,
    })
    aligned.to_csv(sess_dir / "motion_aligned_labels.csv", index=False)

    payload = {
        "episodes": [
            {
                "id": "ep0",
                "start_ts": float(eating_window[0]),
                "end_ts": float(eating_window[1]),
                "chews": [
                    {
                        "id": "ep0_chew0",
                        "start_ts": float(eating_window[0]),
                        "peak_ts": float((eating_window[0] + eating_window[1]) / 2),
                        "end_ts": float(eating_window[1]),
                        "duration_s": float(eating_window[1] - eating_window[0]),
                        "amplitude": 1.0,
                        "confidence": 0.9,
                    }
                ],
            }
        ],
    }
    (sess_dir / "chew_cycles.json").write_text(json.dumps(payload))


def test_legacy_only_csv_unchanged_when_no_video_root(tmp_path):
    bundle = tmp_path / "session_legacy"
    bundle.mkdir()
    src = bundle / "session_legacy.csv"
    _write_raw_csv(src, label=1)
    out = bundle / "session_legacy_transformed.csv"

    transform_csv(
        src,
        out,
        SoftLabelsConfig(),
        label_col="label",
        apply_review=False,
        video_labels_root=None,
    )

    df = pd.read_csv(out)
    assert "chewing_soft" in df.columns
    assert "chew_phase_sin" not in df.columns
    assert df["label"].sum() == len(df)  # untouched


def test_video_overlay_adds_columns_and_overrides_label(tmp_path):
    raw_root = tmp_path / "raw_sessions"
    derived_root = tmp_path / "derived" / "video_labels"
    sid = "session_video"
    bundle = raw_root / sid
    bundle.mkdir(parents=True)
    src = bundle / f"{sid}.csv"
    _write_raw_csv(src, label=0)  # legacy says not-eating everywhere
    _write_video_artifacts(derived_root, sid, eating_window=(0.5, 1.5))

    out = bundle / f"{sid}_transformed.csv"
    transform_csv(
        src,
        out,
        SoftLabelsConfig(),
        label_col="label",
        apply_review=False,
        video_labels_root=derived_root,
    )

    df = pd.read_csv(out)
    # Labeller's eating column now drives `label`.
    assert df["label"].sum() == int(((df["timestamp"] >= 0.5) & (df["timestamp"] < 1.5)).sum())
    # New columns are present.
    for col in ("eating", "chew_phase", "chew_id", "video_confidence", "chew_phase_sin", "chew_phase_cos"):
        assert col in df.columns
    # Phase sin/cos are nonzero somewhere inside the chew window.
    inside = (df["timestamp"] >= 0.5) & (df["timestamp"] < 1.5)
    assert np.any(np.abs(df.loc[inside, "chew_phase_sin"]) > 0)


def test_find_artifacts_reports_counts_and_verified(tmp_path):
    raw_root = tmp_path / "raw_sessions"
    derived_root = tmp_path / "derived" / "video_labels"
    sid = "session_counts"
    bundle = raw_root / sid
    bundle.mkdir(parents=True)
    _write_raw_csv(bundle / f"{sid}.csv")
    _write_video_artifacts(derived_root, sid)
    (bundle / "review_overrides.json").write_text(json.dumps({"verified": True, "overrides": []}))

    art = find_artifacts(sid, derived_root, raw_bundle_dir=bundle)
    assert art.has_video_labels
    assert art.eating_span_count == 1
    assert art.chew_cycle_count == 1
    assert art.verified is True


def test_transform_rejects_unverified_video_labels_when_training_gate_is_enabled(tmp_path):
    raw_root = tmp_path / "raw_sessions"
    derived_root = tmp_path / "derived" / "video_labels"
    sid = "session_unverified"
    bundle = raw_root / sid
    bundle.mkdir(parents=True)
    src = bundle / f"{sid}.csv"
    _write_raw_csv(src)
    _write_video_artifacts(derived_root, sid)

    with pytest.raises(ValueError, match="not verified"):
        transform_csv(
            src,
            bundle / "out.csv",
            SoftLabelsConfig(),
            apply_review=False,
            video_labels_root=derived_root,
            require_verified_video_labels=True,
        )


def test_overlay_handles_empty_string_columns(tmp_path):
    # Regression: pandas reads empty CSV cells as float NaN; the overlay
    # must coerce them to strings without raising.
    raw_root = tmp_path / "raw"
    derived_root = tmp_path / "derived"
    sid = "s"
    bundle = raw_root / sid
    bundle.mkdir(parents=True)
    _write_raw_csv(bundle / f"{sid}.csv", n=50)

    sess_dir = derived_root / sid
    sess_dir.mkdir(parents=True)
    # Aligned CSV with all empty string columns.
    t = np.arange(50) / 100.0
    pd.DataFrame({
        "timestamp": t,
        "video_ts": t,
        "eating": np.zeros(50, dtype=int),
        "chew_phase": ["" for _ in range(50)],
        "chew_id": ["" for _ in range(50)],
        "video_confidence": np.zeros(50),
    }).to_csv(sess_dir / "motion_aligned_labels.csv", index=False)

    art = find_artifacts(sid, derived_root, raw_bundle_dir=bundle)
    raw = pd.read_csv(bundle / f"{sid}.csv")
    merged = overlay_video_labels(raw, art)
    assert (merged["chew_id"] == "").all()
    assert (merged["chew_phase_sin"] == 0).all()
