"""pipeline_status.json: what Review shows after upload.

Synthetic workspace only.  Proves the status reads existing artifacts without
side effects, flags data that changed since the last run, keeps rejected
recordings visible, and that Review's checked-in schema fixture matches what
the builder writes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.eating.experiment import ExperimentConfig, run_experiment  # noqa: E402
from pipeline.eating.status import build_status  # noqa: E402

from test_eating_detection import _span, make_canonical  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "apps/review-macos/ChewSenseReviewTests/Fixtures/pipeline_status_synthetic.json"


def synthetic_workspace(root: Path) -> Path:
    raw = root / "sessions" / "raw"
    for index in range(6):
        chewing = index % 2 == 0
        make_canonical(
            raw, f"Guided-s{index}-2026110{index}-100000-AAAA", seed=index, duration=40.0,
            chewing=[(10, 25)] if chewing else [],
            intervals=[_span(0, 10, "not_chewing"), _span(10, 25, "chewing"), _span(25, 40, "not_chewing")]
            if chewing else [_span(0, 40, "not_chewing")],
        )
    (raw / "Broken-1").mkdir()
    return root


def _shape(value):
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__


def test_status_tracks_data_model_and_changes(tmp_path):
    workspace = synthetic_workspace(tmp_path / "workspace")
    run_experiment(workspace=workspace, config=ExperimentConfig(models=("prior", "logreg"), folds=2,
                                                                bootstrap_resamples=20))
    before = sorted(p.name for p in workspace.rglob("*"))
    status = build_status(workspace=workspace, repo=tmp_path / "no-repo")
    assert sorted(p.name for p in workspace.rglob("*")) == before  # read-only
    assert status["data"]["usable_recordings"] == 6
    rejected = [r for r in status["recordings"] if not r["usable"]]
    assert rejected and "session.json" in rejected[0]["reason"]
    assert status["model"]["trained"] and status["model"]["trained_on_current_data"]
    assert set(status["model"]["timelines"]) == {r["session_id"] for r in status["recordings"] if r["usable"]}
    splits = {r["split"] for r in status["recordings"] if r["usable"]}
    assert splits <= {"dev", "test"}

    # A new labelled recording: the model is no longer trained on current data.
    make_canonical(workspace / "sessions" / "raw", "Guided-new-20261110-100000-BBBB", seed=9, duration=40.0,
                   intervals=[_span(0, 40, "not_chewing")])
    changed = build_status(workspace=workspace, repo=tmp_path / "no-repo")
    assert changed["model"]["trained_on_current_data"] is False
    assert "Guided-new-20261110-100000-BBBB" in changed["model"]["recordings_not_in_run"]
    assert any("development check" in item["title"] for item in changed["next"])
    new_row = next(r for r in changed["recordings"] if r["session_id"] == "Guided-new-20261110-100000-BBBB")
    assert new_row["split"].startswith("unassigned")


def test_review_fixture_matches_status_schema(tmp_path):
    workspace = synthetic_workspace(tmp_path / "workspace")
    run_experiment(workspace=workspace, config=ExperimentConfig(models=("prior", "logreg"), folds=2,
                                                                bootstrap_resamples=20))
    status = json.loads(json.dumps(build_status(workspace=workspace, repo=REPO), default=float))
    fixture = json.loads(FIXTURE.read_text())
    assert set(fixture) == set(status)
    for key in ("data", "model"):
        assert set(fixture[key]) <= set(status[key]), key


def test_latest_development_check_is_visible_without_reusing_historical_test_scores(tmp_path):
    workspace = synthetic_workspace(tmp_path / "workspace")
    run_experiment(workspace=workspace, config=ExperimentConfig(models=("prior", "logreg"), folds=2,
                                                                bootstrap_resamples=20))
    export_pointer = (workspace / "experiments/eating/latest.json").read_bytes()
    run = run_experiment(workspace=workspace, config=ExperimentConfig(models=("prior", "logreg"), folds=2,
                                                                     bootstrap_resamples=20, evaluate_test=False))
    status = build_status(workspace=workspace, repo=tmp_path / "no-repo")
    assert status["model"]["run_id"] == run.name
    assert status["model"]["trained_on_current_data"]
    assert status["model"]["development"] is not None
    assert status["model"]["test"] is None
    assert status["model"]["test_consultations"] == 1
    assert not any("development check" in item["title"] for item in status["next"])
    assert (workspace / "experiments/eating/latest.json").read_bytes() == export_pointer
