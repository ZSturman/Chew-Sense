from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from chewsense.training.pseudolabel import build_labels


def _write_run(root: Path) -> Path:
    run = root / "run"
    run.mkdir()
    pd.DataFrame({
        "frame_idx": range(10),
        "ts": [index * 0.1 for index in range(10)],
    }).to_csv(run / "frame_predictions.csv", index=False)
    (run / "events.json").write_text(json.dumps({"episodes": []}))
    return run


def test_reviewed_annotations_replace_automatic_training_targets(tmp_path: Path):
    run = _write_run(tmp_path)
    annotations = tmp_path / "canonical_events.json"
    annotations.write_text(json.dumps({
        "verified": True,
        "chewing_bouts": [{"start_ts": 0.2, "end_ts": 0.6}],
        "mouth_states": [{"start_ts": 0.4, "end_ts": 0.5, "state": "unknown"}],
    }))

    labels = build_labels(run, annotations_path=annotations, boundary_soft_s=0)

    assert labels.loc[labels["ts"].between(0.2, 0.5), "label_id"].eq(1).all()
    assert labels.loc[labels["ts"].between(0.4, 0.4), "weight"].eq(0).all()
    assert labels.loc[labels["ts"] >= 0.7, "label_id"].eq(0).all()


def test_unverified_review_is_rejected(tmp_path: Path):
    run = _write_run(tmp_path)
    annotations = tmp_path / "canonical_events.json"
    annotations.write_text(json.dumps({"verified": False}))

    with pytest.raises(ValueError, match="must be verified"):
        build_labels(run, annotations_path=annotations)
