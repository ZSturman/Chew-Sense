from __future__ import annotations

import json
from pathlib import Path

from pipeline.promotion import PromotionThresholds, assess_registry_entry


def _write_entry(path: Path, *, metrics: dict, sessions: list[dict]) -> Path:
    path.mkdir()
    (path / "metadata.json").write_text(json.dumps({"model_id": path.name}))
    (path / "metrics.json").write_text(json.dumps(metrics))
    (path / "dataset_manifest.json").write_text(json.dumps({"sessions": sessions}))
    return path


def test_promotion_passes_complete_locked_evidence(tmp_path: Path):
    sessions = [
        *[{"label": "eating", "verified": True} for _ in range(2)],
        *[{"label": "not-eating", "verified": True} for _ in range(2)],
    ]
    entry = _write_entry(
        tmp_path / "model-1",
        metrics={
            "test_locked": {
                "recall": 0.94,
                "hard_negative_fpr": 0.10,
                "chew_f1_at_250ms": 0.84,
                "chew_count_error": 0.08,
            }
        },
        sessions=sessions,
    )
    thresholds = PromotionThresholds(
        verified_eating_sessions=2,
        verified_hard_negative_sessions=2,
    )

    result = assess_registry_entry(entry, thresholds)

    assert result.passed
    assert result.evaluation_split == "test_locked"


def test_promotion_rejects_validation_only_and_missing_chew_metrics(tmp_path: Path):
    entry = _write_entry(
        tmp_path / "model-2",
        metrics={"validation_locked": {"recall": 0.99, "false_positive_rate": 0.01}},
        sessions=[],
    )

    result = assess_registry_entry(entry)

    assert not result.passed
    assert result.evaluation_split is None
    assert any("no test_locked" in reason for reason in result.reasons)
    assert result.checks["chew_f1_at_250ms"]["passed"] is False
