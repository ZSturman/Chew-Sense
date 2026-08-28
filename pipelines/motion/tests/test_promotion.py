from __future__ import annotations

import json
from pathlib import Path

import torch

from pipeline.promotion import (
    FoodEntryPromotionThresholds,
    PromotionThresholds,
    assess_food_entry_head,
    assess_registry_entry,
)


def _write_entry(
    path: Path,
    *,
    metrics: dict,
    sessions: list[dict],
    food_entry_head: bool = False,
) -> Path:
    path.mkdir()
    normalized_sessions = []
    for index, session in enumerate(sessions):
        normalized = dict(session)
        normalized.setdefault("session_id", f"session-{index:03d}")
        normalized.setdefault("event_supervision", True)
        normalized_sessions.append(normalized)
    (path / "metadata.json").write_text(
        json.dumps(
            {
                "model_id": path.name,
                "model_mode": "temporal_tcn",
                "temporal_contract_version": "2.0.0",
                "food_entry_head": food_entry_head,
                "dataset_hash": "fixture-dataset",
            }
        )
    )
    torch.save(
        {
            "mode": "temporal_tcn",
            "temporal_contract_version": "2.0.0",
            "output_names": [
                "eating_logit",
                "chew_event_logit",
                "phase_sin",
                "phase_cos",
                "phase_confidence",
                *(["food_entry_logit"] if food_entry_head else []),
            ],
            "dataset_hash": "fixture-dataset",
        },
        path / "chewnet.pth",
    )
    (path / "metrics.json").write_text(json.dumps(metrics))
    (path / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0.0",
                "artifact_type": "temporal_motion_dataset",
                "temporal_contract_version": "2.0.0",
                "dataset_hash": "fixture-dataset",
                "sessions": normalized_sessions,
            }
        )
    )
    eating_ids = [item["session_id"] for item in normalized_sessions if item["label"] == "eating"]
    negative_ids = [
        item["session_id"]
        for item in normalized_sessions
        if item["label"] in {"hard-negative", "hard_negative"}
    ]
    (path / "split_manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "dataset_hash": "fixture-dataset",
                "splits": {
                    "test_locked": eating_ids[:1] + negative_ids[:1],
                    "gold_locked": [],
                },
            }
        )
    )
    return path


def test_promotion_passes_complete_locked_evidence(tmp_path: Path):
    sessions = [
        *[{"label": "eating", "verified": True} for _ in range(2)],
        *[{"label": "hard-negative", "verified": True} for _ in range(2)],
    ]
    entry = _write_entry(
        tmp_path / "model-1",
        metrics={
            "test_locked": {
                "recall": 0.94,
                "hard_negative_fpr": 0.10,
                "chew_f1_at_250ms": 0.84,
                "chew_count_error": 0.08,
                "median_inter_chew_interval_mae_sec": 0.075,
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


def test_food_entry_is_an_independent_capability_gate(tmp_path: Path):
    entry = _write_entry(
        tmp_path / "model-food",
        metrics={
            "test_locked": {
                "recall": 0.95,
                "hard_negative_fpr": 0.05,
                "chew_f1_at_250ms": 0.9,
                "chew_count_error": 0.04,
                "median_inter_chew_interval_mae_sec": 0.05,
                "food_entry": {
                    "food_entry_f1_at_500ms": 0.79,
                    "food_entry_count_error": 0.1,
                    "false_entries_per_negative_minute": 0.1,
                    "per_bite_chew_count_mae": 1.5,
                },
            }
        },
        sessions=[],
        food_entry_head=True,
    )

    food = assess_food_entry_head(entry, FoodEntryPromotionThresholds())

    assert not food.passed
    assert food.checks["food_entry_f1_at_500ms"]["passed"] is False
    assert food.checks["per_bite_chew_count_mae"]["passed"] is True


def test_legacy_binary_checkpoint_cannot_promote_from_fabricated_metrics(tmp_path: Path):
    entry = _write_entry(
        tmp_path / "model-legacy",
        metrics={
            "test_locked": {
                "recall": 1.0,
                "hard_negative_fpr": 0.0,
                "chew_f1_at_250ms": 1.0,
                "chew_count_error": 0.0,
                "median_inter_chew_interval_mae_sec": 0.0,
            }
        },
        sessions=[
            *[{"label": "eating", "verified": True} for _ in range(20)],
            *[{"label": "hard-negative", "verified": True} for _ in range(20)],
        ],
    )
    metadata = json.loads((entry / "metadata.json").read_text())
    metadata["model_mode"] = "binary"
    metadata["temporal_contract_version"] = None
    (entry / "metadata.json").write_text(json.dumps(metadata))

    result = assess_registry_entry(entry)

    assert not result.passed
    assert result.checks["temporal_model_contract"]["passed"] is False
