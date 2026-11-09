"""Decoder study: development-only selection, one recorded locked-test confirmation (synthetic)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.eating import decoder_study as study  # noqa: E402
from pipeline.eating.dataset import discover_sessions  # noqa: E402
from pipeline.eating.experiment import ExperimentConfig, run_experiment  # noqa: E402

from test_eating_status import synthetic_workspace  # noqa: E402


def test_grid_reads_development_only_and_consultations_persist(tmp_path):
    workspace = synthetic_workspace(tmp_path / "workspace")
    config = dict(models=("prior", "logreg"), folds=2, bootstrap_resamples=20)
    test_run = run_experiment(workspace=workspace, config=ExperimentConfig(**config))
    sessions, _ = discover_sessions(canonical_roots=[workspace / "sessions/raw"], field_test_roots=[],
                                    loop_root=workspace / "loop")
    grid = study.development_grid(test_run, sessions, (3, 6))
    assert grid["split"] == "dev" and [r["min_start_windows"] for r in grid["settings"]] == [3, 6]
    splits = json.loads((workspace / "experiments/eating/splits.json").read_text())["assignments"]
    dev_ids = {k for k, v in splits.items() if v["split"] == "dev"}
    assert all(r["recordings"] <= len(dev_ids) for r in grid["settings"])

    result = study.confirm_on_test(test_run, sessions, {"current_min_start_windows": 3, "candidate_min_start_windows": 6})
    assert result["current"]["min_start_windows"] == 3 and result["candidate"]["min_start_windows"] == 6
    root = workspace / "experiments/eating"
    assert study.record_consultation(root, {"kind": "decoder_confirmation"}) == 2
    run_experiment(workspace=workspace, config=ExperimentConfig(**config, evaluate_test=False))
    assert json.loads((root / "index.json").read_text())["test_consultations"] == 2  # not lost by a later run
