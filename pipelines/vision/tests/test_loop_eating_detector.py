"""Dashboard summary of the experimental chewing-bout detector (synthetic JSON)."""
from __future__ import annotations

import json

from chewsense.loop.eating_detector import eating_detector_stage, eating_detector_summary


def test_missing_experiment_is_waiting(tmp_path):
    summary = eating_detector_summary(tmp_path / "experiments")
    assert summary["available"] is False
    stage = eating_detector_stage(summary)
    assert stage["state"] == "waiting" and "eating-experiment" in stage["detail"]


def test_summary_reports_test_scores_and_weak_contexts(tmp_path):
    root = tmp_path / "experiments" / "eating"
    run = root / "runs" / "eating-1"
    run.mkdir(parents=True)
    split = {
        "f1": 0.81, "precision": 0.8, "recall": 0.82, "false_positive_rate": 0.04, "sessions": 5,
        "positives": 100, "negatives": 400, "ci95": {"f1": {"low": 0.6, "high": 0.9}},
        "bouts": {"detected_bouts": 4, "reference_bouts": 5, "false_bouts": 1, "onset_latency_median_s": 2.0},
        "by_context": {
            "guided:talk": {"false_positive_rate": 0.3, "negatives": 20},
            "guided:rest": {"false_positive_rate": 0.0, "negatives": 50},
            "tiny": {"false_positive_rate": 1.0, "negatives": 2},
        },
    }
    (run / "metrics.json").write_text(json.dumps({
        "selected_model": "hgb",
        "results": {"hgb": {"test": split, "dev_oof": split}},
        "recommendations": [{"title": "Talking", "reason": "r", "experiment_id": "talking", "priority": 2}],
    }))
    (root / "latest.json").write_text(json.dumps({
        "run_id": "eating-1", "run_dir": str(run), "finished_at": "2026-11-03T00:00:00+00:00",
        "dataset_fingerprint": "abc", "selected_model": "hgb",
    }))
    summary = eating_detector_summary(tmp_path / "experiments")
    assert summary["test"]["f1"] == 0.81 and summary["test"]["f1_ci95"] == [0.6, 0.9]
    assert [w["context"] for w in summary["weakest_contexts"]] == ["guided:talk", "guided:talk"]
    assert summary["recommendations"][0]["experiment_id"] == "talking"
    assert summary["deployed"] is False
    stage = eating_detector_stage(summary)
    assert stage["state"] == "complete"
    assert "test F1 0.81" in stage["detail"] and "guided:talk" in stage["detail"]
    assert str(tmp_path) not in stage["detail"]  # stages reach the phone; no local paths
