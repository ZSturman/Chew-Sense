"""Deployable eating-detector specification, reference runtime and Swift parity.

Synthetic data only: these tests prove that the exported trees reproduce
scikit-learn exactly, that the streaming reference matches the experiment's
windows, and (when ``swiftc`` is available) that Live's Swift runtime matches
the Python reference.  They say nothing about detection accuracy.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.eating.dataset import build_windows, discover_sessions  # noqa: E402
from pipeline.eating.features import compute_features  # noqa: E402
from pipeline.eating.models import SklearnModel  # noqa: E402
from pipeline.eating.parity import check_live_bundle, live_fixture, run_parity  # noqa: E402
from pipeline.eating.runtime import (  # noqa: E402
    TreeEnsemble,
    export_spec,
    load_spec,
    run_stream,
    stream_end_times,
    write_spec,
)

from test_eating_detection import _span, make_canonical  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


def _fake_run(tmp_path: Path) -> tuple[Path, list, np.ndarray]:
    raw = tmp_path / "raw"
    for index in range(3):
        make_canonical(raw, f"Eat-{index}", seed=index, duration=60.0, chewing=[(15, 40)], intervals=[
            _span(0, 15, "not_chewing"), _span(15, 40, "chewing"), _span(40, 60, "not_chewing"),
        ])
    sessions, _ = discover_sessions(canonical_roots=[raw])
    windows = build_windows(sessions)
    labelled = windows.label >= 0
    features = compute_features(windows.X)
    model = SklearnModel("hgb").fit(features[labelled], windows.label[labelled])
    run = tmp_path / "runs" / "eating-20260101-000000"
    (run / "models").mkdir(parents=True)
    joblib.dump(model, run / "models" / "hgb.joblib")
    (run / "config.json").write_text(json.dumps({"window_s": 3.0, "stride_s": 0.5, "min_valid_fraction": 0.9}))
    (run / "metrics.json").write_text(json.dumps({
        "run_id": run.name, "selected_model": "hgb", "dataset_fingerprint": "f" * 64,
        "selection_rule": "test", "git": {"commit": "abc"}, "counts": {"participants": 1},
        "config": {"label_target": "human_coarse_chewing_bout"},
        "results": {"hgb": {"threshold": 0.6, "dev_oof": None, "test": None}},
    }))
    return run, sessions, features


def test_exported_trees_reproduce_sklearn_exactly(tmp_path):
    run, _, features = _fake_run(tmp_path)
    spec = export_spec(run)
    sklearn_p = joblib.load(run / "models" / "hgb.joblib").predict_proba(features)
    assert np.array_equal(TreeEnsemble.from_spec(spec).probability(features), sklearn_p)
    assert spec["decision"]["decoder"]["high_threshold"] == 0.6
    assert spec["decision"]["decoder"]["low_threshold"] == pytest.approx(0.4)
    assert spec["features"]["autocorr_lags"] == [16, 62]  # round-half-even, not 17/63
    assert spec["status"] == "experimental_candidate"
    path = tmp_path / "spec.json"
    write_spec(spec, path)
    assert load_spec(path)["model_id"] == spec["model_id"]


def test_model_id_changes_with_trees(tmp_path):
    run, _, _ = _fake_run(tmp_path)
    first = export_spec(run)
    metrics = json.loads((run / "metrics.json").read_text())
    metrics["results"]["hgb"]["threshold"] = 0.7
    (run / "metrics.json").write_text(json.dumps(metrics))
    assert export_spec(run)["model_id"] != first["model_id"]


def test_stream_matches_experiment_windows_and_masks_gaps(tmp_path):
    run, sessions, features = _fake_run(tmp_path)
    spec = export_spec(run)
    session = sessions[0]
    windows = build_windows([session])
    ends = windows.t_end[windows.t_end <= session.motion_t[-1]]
    stream = run_stream(spec, session.motion_t, session.imu, ends)
    expected = compute_features(windows.X[: len(ends)])
    assert np.array_equal(stream.attrs["features"], expected)

    t = session.motion_t.copy()
    keep = (t < 20) | (t > 22)
    gapped = run_stream(spec, t[keep], session.imu[keep], stream_end_times(t[keep], 0.5, first_end=3.0))
    invalid = gapped["probability"].isna()
    assert invalid.any()
    assert (gapped.loc[invalid, "decoder_input"] == 0.0).all()


def test_stream_end_times_have_no_drift():
    t = np.arange(0, 100, 0.02) + 291953.157447375
    ends = stream_end_times(t, 0.5)
    assert ends[0] == t[0] + 3.0
    assert np.allclose(np.diff(ends), 0.5, atol=1e-9)
    assert ends[-1] <= t[-1]


def test_saved_live_check_replays_absolute_clock_and_rejects_damaged_motion(tmp_path):
    spec = load_spec(REPO / "apps/live-ios/ChewSenseLive/EatingDetector/eating-detector.json")
    time = 575_076.0 + np.arange(400) * 0.02
    imu = np.zeros((len(time), 6), dtype=np.float32)
    motion = pd.DataFrame(imu, columns=["ax", "ay", "az", "gx", "gy", "gz"])
    motion.insert(0, "timestamp", time)
    motion_path = tmp_path / "motion.csv"
    motion.to_csv(motion_path, index=False)
    (tmp_path / "session.json").write_text(json.dumps({
        "session_id": "fixture", "source_app": "live", "video": None,
        "quality": {"motion_samples": len(time)},
        "motion": {"path": "motion.csv", "sha256": hashlib.sha256(motion_path.read_bytes()).hexdigest()},
    }))
    (tmp_path / "session_metadata.json").write_text(json.dumps({
        "session_id": "fixture", "eating_detector": {"model_id": spec["model_id"], "drives_live": False},
    }))
    ends = stream_end_times(time, spec["decision"]["stride_s"], window_s=spec["input"]["window_s"])
    log = run_stream(spec, time, imu, ends).rename(columns={"t_end": "window_end"})
    log["state"] = np.where(log["chewing"], "chewing", "idle")
    log["drives_live"] = 0
    log_path = tmp_path / "eating_detector.csv"
    log.to_csv(log_path, index=False, float_format="%.8f")

    result = check_live_bundle(spec, tmp_path)
    assert result["status"] == "pass"
    assert result["windows"] == len(ends) and result["scored_windows"] > 0
    assert not result["has_reference_video"] and not result["drives_live"]

    log.loc[0, "state"] = "idle" if log.loc[0, "state"] == "chewing" else "chewing"
    log.to_csv(log_path, index=False)
    assert check_live_bundle(spec, tmp_path)["status"] == "fail"
    with motion_path.open("a") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="SHA-256"):
        check_live_bundle(spec, tmp_path)


def test_live_fixture_is_current():
    spec_path = REPO / "apps/live-ios/ChewSenseLive/EatingDetector/eating-detector.json"
    fixture_path = REPO / "apps/live-ios/ChewSenseLiveTests/Fixtures/eating_detector_parity.json"
    if not spec_path.exists():
        pytest.skip("no eating detector bundled in Live")
    spec = load_spec(spec_path)
    stored = json.loads(fixture_path.read_text())
    assert stored == json.loads(json.dumps(live_fixture(spec))), (
        "regenerate with `chewsense-motion eating-parity --write-live-fixture`"
    )
    assert stored["bouts"], "the fixture should exercise the decoder"


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="swiftc not available")
def test_swift_runtime_matches_python_reference(tmp_path):
    run, sessions, _ = _fake_run(tmp_path)
    spec = export_spec(run)
    tool = tmp_path / "eating-parity"
    subprocess.run([str(REPO / "tools/eating-parity/build"), str(tool)], check=True, capture_output=True)
    summary = run_parity(spec=spec, sessions=sessions, swift_tool=tool, work_dir=tmp_path / "parity",
                         noise_sessions=1)
    assert summary["status"] == "pass", summary["per_session"]
    assert summary["feature_values_bit_identical"] == 1.0
    assert summary["max_probability_abs_diff"] == 0.0
