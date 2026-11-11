"""Software tests for sittings, the sample-aligned grid and the validity study.

All data here is synthetic.  These tests prove grouping, resampling and
bookkeeping; they say nothing about real-world detection performance.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.eating.dataset import build_windows, discover_sessions, sample_aligned_end  # noqa: E402
from pipeline.eating.evaluate import exact_interval, nested_threshold_metrics, recording_level  # noqa: E402
from pipeline.eating.experiment import ExperimentConfig, run_experiment  # noqa: E402
from pipeline.eating.splits import development_folds, shared_sittings, sitting_groups  # noqa: E402
from pipeline.eating.validity import band_filter, band_power, load_development, run_validity_study  # noqa: E402

from test_eating_detection import _span, _write_csv, make_canonical  # noqa: E402


def _meal(root: Path, name: str, seed: int, chewing: bool = True) -> None:
    spans = ([_span(0, 20, "not_chewing"), _span(20, 50, "chewing"), _span(50, 70, "not_chewing")]
             if chewing else [_span(0, 70, "not_chewing")])
    make_canonical(root, name, duration=70.0, seed=seed, intervals=spans, chewing=[(20, 50)] if chewing else [])


def _workspace(tmp_path: Path) -> Path:
    """Twelve recordings in nine sittings; two sittings hold back-to-back takes."""

    workspace = tmp_path / "workspace"
    raw = workspace / "sessions" / "raw"
    names = [
        ("S-20260101-080000", True), ("S-20260101-080200", True), ("S-20260101-080400", True),
        ("S-20260102-080000", True), ("S-20260103-080000", True), ("S-20260104-080000", True),
        ("S-20260105-080000", False), ("S-20260105-080300", False),
        ("S-20260106-080000", False), ("S-20260107-080000", False),
        ("S-20260108-080000", True), ("S-20260109-080000", False),
    ]
    for seed, (name, chewing) in enumerate(names, start=1):
        _meal(raw, name, seed, chewing)
    return workspace


def test_back_to_back_recordings_share_a_sitting(tmp_path):
    workspace = _workspace(tmp_path)
    sessions, _ = discover_sessions(canonical_roots=[workspace / "sessions" / "raw"])
    sittings = sitting_groups(sessions)
    assert len(set(sittings.values())) == 9
    first = {sittings[f"S-20260101-0{clock}"] for clock in ("80000", "80200", "80400")}
    assert first == {"sitting-20260101-0800"}
    assert sittings["S-20260105-080000"] == sittings["S-20260105-080300"]
    assert sittings["S-20260102-080000"] != sittings["S-20260101-080400"]
    # A longer allowed gap merges nothing here; a zero gap separates everything.
    assert len(set(sitting_groups(sessions, gap_minutes=0.5).values())) == 12


def test_sitting_folds_never_split_a_sitting_and_session_folds_are_unchanged(tmp_path):
    workspace = _workspace(tmp_path)
    sessions, _ = discover_sessions(canonical_roots=[workspace / "sessions" / "raw"])
    sittings = sitting_groups(sessions)
    by_sitting = development_folds(sessions, k=3, grouping="sitting", sittings=sittings)
    folds_of: dict[str, set[int]] = {}
    for session in sessions:
        folds_of.setdefault(sittings[session.session_id], set()).add(by_sitting[session.session_id])
    assert all(len(folds) == 1 for folds in folds_of.values())
    # Per-recording dealing is the rule earlier runs used: chronological round-robin per stratum.
    by_session = development_folds(sessions, k=3)
    chewing = sorted(s.session_id for s in sessions if s.has_chewing)
    assert [by_session[s] for s in chewing] == [i % 3 for i in range(len(chewing))]
    assert len({by_session[f"S-20260101-0{clock}"] for clock in ("80000", "80200", "80400")}) == 3
    with pytest.raises(ValueError):
        development_folds(sessions, grouping="meal")


def test_shared_sittings_lists_test_recordings_recorded_with_development_ones():
    sittings = {"a": "s1", "b": "s1", "c": "s2", "d": "s3"}
    shared = shared_sittings(sittings, {"a": "dev", "b": "test", "c": "test", "d": "dev"})
    assert shared == [{"session_id": "b", "sitting": "s1", "development_recordings": ["a"]}]


def test_sample_grid_keeps_high_band_amplitude_that_the_nominal_grid_damps(tmp_path):
    """A steady 50 Hz clock that sits half a sample off the nominal grid."""

    folder = tmp_path / "Guided-x-20261101-120000-AAAA"
    (folder / "annotations").mkdir(parents=True)
    t = 0.010 + np.arange(0.0, 30.0, 0.02)          # samples 10 ms after each nominal grid point
    imu = np.zeros((len(t), 6))
    imu[:, 0] = 0.02 * np.sin(2 * np.pi * 20.0 * t)  # 20 Hz vibration on one accelerometer axis
    _write_csv(folder / f"{folder.name}.csv", t, imu, origin=500.0)
    (folder / "session.json").write_text(json.dumps({
        "schema_version": "2.0.0", "session_id": folder.name, "activity": "unknown", "source_app": "collect",
        "motion": {"path": f"{folder.name}.csv"},
        "clock_sync": {"video_clock_origin_host_time_s": 500.0, "method": "shared_host_clock"},
        "capture": {"duration_s": 30.0},
    }))
    (folder / "annotations" / "coarse_labels.json").write_text(json.dumps({
        "artifact_type": "coarse_labels", "schema_version": "2.0.0", "session_id": folder.name,
        "intervals": [_span(0, 30, "not_chewing")],
    }))
    sessions, rejected = discover_sessions(canonical_roots=[tmp_path])
    assert not rejected
    nominal = build_windows(sessions)
    aligned = build_windows(sessions, grid="sample")
    assert np.array_equal(nominal.t_end, aligned.t_end) and np.array_equal(nominal.label, aligned.label)
    band = (("12-25", 12.0, 25.01),)
    full = nominal.valid_fraction >= 0.999
    damped = np.median(band_power(nominal.X[full], band)["accel:12-25"])
    kept = np.median(band_power(aligned.X[full], band)["accel:12-25"])
    assert kept == pytest.approx(0.02 / np.sqrt(2), rel=0.05)   # the sine's true RMS
    assert damped < 0.45 * kept                                 # cos(pi * 20/50) = 0.31
    with pytest.raises(ValueError):
        build_windows(sessions, grid="nearest")


def test_sample_aligned_end_moves_at_most_one_sample_back():
    t = np.array([0.0, 0.02, 0.04, 0.30])
    assert sample_aligned_end(t, 0.05) == 0.04
    assert sample_aligned_end(t, 0.04) == 0.04
    assert sample_aligned_end(t, 0.20) == 0.20      # inside a gap: nothing to align to
    assert sample_aligned_end(t, -1.0) == -1.0


def test_nested_threshold_never_uses_the_scored_group():
    y = np.array([0, 0, 1, 1, 0, 0, 1, 1])
    p = np.array([0.1, 0.2, 0.6, 0.7, 0.3, 0.75, 0.8, 0.9])
    groups = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    nested = nested_threshold_metrics(groups, y, p)
    assert nested["available"]
    # Group 0 alone is separated by 0.6; group 1 alone by 0.8.  Each is applied to the other.
    assert nested["thresholds"] == {"0": 0.8, "1": 0.6}
    assert (nested["tp"], nested["fp"], nested["fn"], nested["tn"]) == (2, 1, 2, 3)
    one_class = nested_threshold_metrics(np.array([0, 0, 1, 1]), np.array([0, 0, 0, 1]), np.array([0.1, 0.2, 0.3, 0.9]))
    assert one_class["available"] is False


def test_recording_level_counts_one_decision_per_recording():
    def item(chewing_s, negative_s, spans):
        return {"chewing_seconds": chewing_s, "negative_seconds": negative_s, "predicted_bouts": len(spans),
                "predicted_bout_spans": [{"start": a, "end": b} for a, b in spans]}

    block = recording_level({
        "meal": item(30, 20, [(5, 25)]),
        "short_blip": item(0, 60, [(10, 13)]),
        "long_false": item(0, 60, [(10, 16), (30, 36)]),
        "quiet": item(0, 60, []),
    })
    assert (block["eating_recordings_flagged"], block["eating_recordings"]) == (1, 1)
    assert (block["non_eating_recordings_flagged"], block["non_eating_recordings"]) == (1, 3)
    low, high = block["non_eating_false_positive_rate_ci95"]
    assert low < 1 / 3 < high
    assert exact_interval(0, 15) == pytest.approx([0.0, 0.2180], abs=1e-3)
    assert exact_interval(0, 0) is None


def test_band_filter_and_power_isolate_a_band():
    t = np.arange(150) / 50.0
    window = np.zeros((1, 7, 150), dtype=np.float32)
    window[0, 3] = 0.1 * np.sin(2 * np.pi * 1.5 * t) + 0.05 * np.sin(2 * np.pi * 15.0 * t)
    bands = (("0.3-5", 0.3, 5.0), ("12-25", 12.0, 25.01))
    high_only = band_power(band_filter(window, low_hz=5.0), bands)
    assert high_only["gyro:0.3-5"][0] < 1e-3 < high_only["gyro:12-25"][0]
    low_only = band_power(band_filter(window, high_hz=5.0), bands)
    assert low_only["gyro:12-25"][0] < 1e-3 < low_only["gyro:0.3-5"][0]


def test_experiment_reports_sittings_nested_threshold_and_recordings(tmp_path):
    workspace = _workspace(tmp_path)
    config = ExperimentConfig(models=("prior", "logreg"), folds=3, bootstrap_resamples=30)
    run_dir = run_experiment(workspace=workspace, config=config, log=lambda *_: None)
    summary = json.loads((run_dir / "metrics.json").read_text())
    manifest = json.loads((run_dir / "dataset_manifest.json").read_text())
    assert summary["independence"]["cv_grouping"] == "sitting"
    rows = {row["session_id"]: row for row in manifest["sessions"]}
    dev = [row for row in rows.values() if row["split"] == "dev"]
    by_sitting: dict[str, set[int]] = {}
    for row in dev:
        by_sitting.setdefault(row["sitting"], set()).add(row["fold"])
    assert all(len(folds) == 1 for folds in by_sitting.values())
    block = summary["results"]["logreg"]["dev_oof"]
    assert block["ci95_resampled_over"] == "sitting"
    assert block["nested_threshold"]["available"] and 0 <= block["nested_threshold"]["f1"] <= 1
    assert block["recordings"]["eating_recordings"] + block["recordings"]["non_eating_recordings"] == len(dev)
    assert "How independent is the evidence?" in (run_dir / "report.html").read_text()

    # The earlier per-recording rule is still available and leaves the dataset fingerprint alone.
    legacy = run_experiment(workspace=workspace, config=replace(config, cv_grouping="session", evaluate_test=False),
                            log=lambda *_: None)
    legacy_summary = json.loads((legacy / "metrics.json").read_text())
    assert legacy_summary["dataset_fingerprint"] == summary["dataset_fingerprint"]
    assert legacy_summary["results"]["logreg"]["dev_oof"]["ci95_resampled_over"] == "session"
    # A different grid is a different dataset.
    aligned = run_experiment(workspace=workspace, config=replace(config, window_grid="sample", evaluate_test=False),
                             log=lambda *_: None)
    assert json.loads((aligned / "metrics.json").read_text())["dataset_fingerprint"] != summary["dataset_fingerprint"]


def test_validity_study_reads_the_locked_files_without_changing_them(tmp_path):
    workspace = _workspace(tmp_path)
    run_experiment(workspace=workspace, config=ExperimentConfig(models=("prior", "logreg"), folds=3,
                                                              bootstrap_resamples=20), log=lambda *_: None)
    root = workspace / "experiments" / "eating"
    before = {name: (root / name).read_bytes() for name in ("splits.json", "index.json", "latest.json")}
    dev, sessions, split_of, pending = load_development(workspace)
    assert pending == [] and {split_of[s.session_id] for s in dev} == {"dev"}

    run_dir = run_validity_study(workspace=workspace, permutations=20, bootstrap_resamples=30, log=lambda *_: None)
    assert {name: (root / name).read_bytes() for name in before} == before
    result = json.loads((run_dir / "validity.json").read_text())
    assert result["scope"]["locked_test_windows_built"] == 0
    assert result["scope"]["test_consultations_before"] == result["scope"]["test_consultations_after"] == 1
    dev_ids = {s.session_id for s in dev}
    studied = {rec for members in result["independence"]["sittings"].values() for rec in members}
    assert studied == dev_ids and not studied & {sid for sid, split in split_of.items() if split == "test"}
    assert len(result["protocols"]["hgb"]) == 3 and "leave one sitting out" in result["protocols"]["hgb"]
    assert result["within_meal_null"]["permutations"] == 20
    assert (run_dir / "summary.md").read_text().startswith("# Chewing-bout validity study")
    assert json.loads((root / "validity" / "latest.json").read_text())["study_id"] == run_dir.name


def test_prospective_ledger_scores_each_new_development_recording_once(tmp_path):
    from pipeline.eating.prospective import update_ledger
    from pipeline.eating.runtime import export_spec

    from test_eating_runtime import _fake_run

    run, trained, _ = _fake_run(tmp_path)
    spec = export_spec(run)
    later = tmp_path / "later"
    make_canonical(later, "Guided-a-20270101-120000-AAAA", seed=11, duration=60.0, chewing=[(15, 40)], intervals=[
        _span(0, 15, "not_chewing"), _span(15, 40, "chewing"), _span(40, 60, "not_chewing")])
    make_canonical(later, "Guided-b-20270102-120000-BBBB", seed=12, duration=60.0,
                   intervals=[_span(0, 60, "not_chewing")])
    make_canonical(later, "Old-20250101-120000", seed=13, duration=60.0, intervals=[_span(0, 60, "not_chewing")])
    make_canonical(later, "Crossing-20270103-120000", seed=14, duration=60.0,
                   intervals=[_span(0, 60, "not_chewing")])
    sessions, _ = discover_sessions(canonical_roots=[later])
    # The session ID is local time; only the manifest supplies an absolute time.
    for session in sessions:
        manifest_path = Path(session.source_path) / "session.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["created_at"] = session.created_at + "Z"
        if session.session_id.startswith("Crossing-"):
            # Finished after training, but capture began before it: not prospective.
            manifest["created_at"] = "2026-11-04T02:06:30Z"
        manifest_path.write_text(json.dumps(manifest))
    split_of = {"Guided-a-20270101-120000-AAAA": "dev", "Guided-b-20270102-120000-BBBB": "test",
                "Old-20250101-120000": "dev", "Crossing-20270103-120000": "dev",
                **{s.session_id: "dev" for s in trained}}
    path = tmp_path / "prospective" / f"{spec['model_id']}.json"
    kwargs = dict(spec=spec, split_of=split_of, trained_ids={s.session_id for s in trained},
                  model_finished_at="2026-11-04T02:06:06+00:00", ledger_path=path)
    ledger = update_ledger(sessions=[*sessions, *trained], **kwargs)
    # Only the development recording made after the detector: not the test one, the older one or training data.
    assert ledger["added_now"] == ["Guided-a-20270101-120000-AAAA"]
    entry = ledger["entries"][0]
    assert entry["windows"]["positives"] > 0 and entry["bouts"]["reference_bouts"] == 1
    assert ledger["summary"]["recordings"] == 1
    stored = path.read_bytes()
    again = update_ledger(sessions=[*sessions, *trained], **kwargs)
    assert again["added_now"] == [] and path.read_bytes() == stored
    with pytest.raises(ValueError):
        update_ledger(sessions=sessions, **{**kwargs, "spec": {**spec, "model_id": "another"}})


@pytest.mark.parametrize(("finished", "source_app", "expected"), [
    ("2026-11-03T19:06:30-08:00", "collect", "2026-11-04T03:05:30+00:00"),
    ("2026-11-04T04:06:30+02:00", "collect", "2026-11-04T02:05:30+00:00"),
    ("2026-11-04T02:06:30", "collect", None),
    ("2026-11-04T02:06:30Z", "legacy_import", None),
])
def test_prospective_capture_time_uses_absolute_manifest_time(tmp_path, finished, source_app, expected):
    from pipeline.eating.prospective import _capture_started_at

    folder = make_canonical(tmp_path, "Guided-a-20261104-190630-AAAA", duration=60,
                            intervals=[_span(0, 60, "not_chewing")])
    manifest_path = folder / "session.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(created_at=finished, source_app=source_app)
    manifest_path.write_text(json.dumps(manifest))
    # Load first with the ordinary source; the timestamp helper must reject migration metadata itself.
    sessions, _ = discover_sessions(canonical_roots=[tmp_path])
    assert len(sessions) == 1
    captured = _capture_started_at(sessions[0])
    assert (captured.isoformat() if captured else None) == expected
