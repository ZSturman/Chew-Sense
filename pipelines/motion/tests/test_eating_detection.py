"""Software tests for the chewing-bout track.

All data here is synthetic.  These tests prove label provenance, windowing,
split immutability, metric arithmetic and orchestration; they say nothing
about real-world detection performance.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.eating.dataset import (  # noqa: E402
    build_windows,
    discover_sessions,
    load_canonical_session,
    load_field_test,
    SessionRejected,
)
from pipeline.eating.evaluate import (  # noqa: E402
    DecoderConfig,
    bout_metrics,
    choose_threshold,
    decode_bouts,
    window_metrics,
)
from pipeline.eating.features import compute_features, feature_names  # noqa: E402
from pipeline.eating.splits import assign_splits  # noqa: E402


RATE = 50.0


def _motion(duration: float, chewing: list[tuple[float, float]], seed: int = 0, gap: tuple[float, float] | None = None):
    rng = np.random.default_rng(seed)
    t = np.arange(0.0, duration, 1.0 / RATE)
    imu = rng.normal(0.0, 0.01, size=(len(t), 6))
    for start, end in chewing:
        inside = (t >= start) & (t <= end)
        imu[inside, 3] += 0.3 * np.sin(2 * np.pi * 1.5 * t[inside])
        imu[inside, 4] += 0.15 * np.sin(2 * np.pi * 1.5 * t[inside] + 0.4)
    if gap:
        keep = (t < gap[0]) | (t > gap[1])
        t, imu = t[keep], imu[keep]
    return t, imu


def _write_csv(path: Path, t: np.ndarray, imu: np.ndarray, origin: float, header: bool = True) -> None:
    rows = ["timestamp,ax,ay,az,gx,gy,gz"] if header else []
    rows += [",".join([f"{origin + ti:.6f}"] + [f"{v:.6f}" for v in row]) for ti, row in zip(t, imu)]
    path.write_text("\n".join(rows) + "\n")


def make_canonical(
    root: Path,
    session_id: str,
    *,
    duration: float = 60.0,
    intervals: list[dict],
    chewing: list[tuple[float, float]] = (),
    activity: str = "unknown",
    source_app: str = "collect",
    clock_method: str = "shared_host_clock",
    seed: int = 0,
    gap: tuple[float, float] | None = None,
) -> Path:
    folder = root / session_id
    (folder / "annotations").mkdir(parents=True)
    origin = 1000.0 + seed
    t, imu = _motion(duration, list(chewing), seed=seed, gap=gap)
    _write_csv(folder / f"{session_id}.csv", t, imu, origin)
    (folder / "session.json").write_text(json.dumps({
        "schema_version": "2.0.0",
        "session_id": session_id,
        "created_at": "2026-01-01T00:00:00Z",
        "activity": activity,
        "source_app": source_app,
        "motion": {"path": f"{session_id}.csv"},
        "clock_sync": {"video_clock_origin_host_time_s": origin, "method": clock_method},
        "capture": {"duration_s": duration},
    }))
    (folder / "annotations" / "coarse_labels.json").write_text(json.dumps({
        "artifact_type": "coarse_labels", "schema_version": "2.0.0", "session_id": session_id,
        "intervals": intervals,
    }))
    return folder


def _span(start, end, label, source="human_coarse", **extra):
    return {"start_ts": start, "end_ts": end, "label": label, "source": source, **extra}


def test_canonical_tiers_ignore_non_human_sources(tmp_path):
    folder = make_canonical(tmp_path, "Guided-x-20261101-120000-AAAA", intervals=[
        _span(0, 20, "not_chewing"),
        _span(20, 40, "chewing", source="automatic"),
        _span(40, 60, "unknown"),
    ])
    session = load_canonical_session(folder)
    assert [(i.label, i.tier) for i in session.intervals] == [("not_chewing", "human_coarse")]
    assert any("non-human source" in note for note in session.notes)
    assert session.created_at.startswith("2026-11-01T12:00")


def test_legacy_negative_capture_is_session_declared(tmp_path):
    folder = make_canonical(
        tmp_path, "Not-eating-20251201-101010", activity="not_eating", source_app="legacy_import",
        clock_method="legacy_first_motion_fallback",
        intervals=[_span(0, 60, "not_chewing", source="legacy")],
    )
    session = load_canonical_session(folder)
    assert session.intervals[0].tier == "session_declared_negative"
    assert session.clock == "first_motion_fallback"


def test_attestation_and_diagnostic_role(tmp_path):
    folder = make_canonical(tmp_path, "Guided-quiet-1", intervals=[
        _span(0, 60, "not_chewing", basis="wearer_no_food_attestation"),
    ])
    assert load_canonical_session(folder).intervals[0].tier == "wearer_no_food_attestation"
    with pytest.raises(SessionRejected, match="diagnostic"):
        load_canonical_session(folder, loop_roles={"Guided-quiet-1": "diagnostic"})


def _field_test(root: Path, name: str, *, segments, activity="", expected="", scenario="", seed=0) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    t, imu = _motion(40.0, [(5, 30)], seed=seed)
    _write_csv(folder / "motion.csv", t, imu, 5000.0, header=False)
    (folder / "session_metadata.json").write_text(json.dumps({
        "session_id": name, "activity": activity, "expected_label_class": expected,
        "test_scenario": scenario, "started_at": "2026-05-01T00:00:00Z",
    }))
    (folder / "review_segments.json").write_text(json.dumps(segments))
    return folder


def test_field_test_unmarked_time_is_unknown(tmp_path):
    folder = _field_test(tmp_path, "FieldTest-1", segments=[{"kind": "chewing", "startTime": 5, "endTime": 30}])
    session = load_field_test(folder)
    assert [(i.start, i.end, i.label) for i in session.intervals] == [(5.0, 30.0, "chewing")]
    negative = load_field_test(_field_test(tmp_path, "FieldTest-2", segments=[], activity="walking", expected="negative"))
    assert negative.intervals[0].tier == "session_declared_negative"
    with pytest.raises(SessionRejected):
        load_field_test(_field_test(
            tmp_path, "FieldTest-3", segments=[], activity="eating_soft", expected="positive",
            scenario="Not eating / baseline",
        ))


def test_duplicate_motion_is_loaded_once(tmp_path):
    field = _field_test(tmp_path / "field", "FieldTest-dup", segments=[{"kind": "chewing", "startTime": 5, "endTime": 30}])
    raw = tmp_path / "raw"
    copy = make_canonical(raw, "Eating-fieldtest-dup", intervals=[_span(0, 40, "chewing", source="legacy")], duration=40)
    (copy / "Eating-fieldtest-dup.csv").write_bytes((field / "motion.csv").read_bytes())
    sessions, rejected = discover_sessions(canonical_roots=[raw], field_test_roots=[tmp_path / "field"])
    assert [s.session_id for s in sessions] == ["FieldTest-dup"]
    assert "duplicate motion recording" in rejected[0]["reason"]


def test_windows_respect_guard_and_gaps(tmp_path):
    folder = make_canonical(
        tmp_path, "S-20260101-000000", duration=40.0, chewing=[(20, 40)], gap=(8.0, 10.0),
        intervals=[_span(0, 20, "not_chewing"), _span(20, 40, "chewing")],
    )
    windows = build_windows([load_canonical_session(folder)], window_s=3.0, stride_s=0.5, guard_s=1.0)
    t, label, reason = windows.t_end, windows.label, windows.exclusion
    # No labelled window may come within the guard of the 20 s transition.
    assert not np.any((label >= 0) & (t > 19.0) & (t - 3.0 < 21.0))
    assert np.all(label[(t >= 24.0) & (t <= 39.0)] == 1)
    assert np.all(label[(t >= 4.0) & (t <= 7.5)] == 0)
    assert set(reason[(t >= 9.0) & (t <= 10.0)]) == {"sensor_gap"}


def test_split_assignments_never_move(tmp_path):
    root = tmp_path / "raw"
    sessions = []
    for day in range(1, 9):
        chewing = day % 2 == 0
        folder = make_canonical(
            root, f"S-202601{day:02d}-000000", seed=day,
            intervals=[_span(0, 60, "chewing" if chewing else "not_chewing")],
            chewing=[(0, 60)] if chewing else [],
        )
        sessions.append(load_canonical_session(folder))
    path = tmp_path / "splits.json"
    first = assign_splits(sessions, path)
    tests = {k for k, v in first["assignments"].items() if v["split"] == "test"}
    assert tests == {"S-20260108-000000", "S-20260107-000000"}
    newcomer = load_canonical_session(make_canonical(
        root, "S-20260120-000000", seed=20, intervals=[_span(0, 60, "chewing")], chewing=[(0, 60)],
    ))
    newcomer.loop_role = "evaluation"
    second = assign_splits([*sessions, newcomer], path)
    for key, value in first["assignments"].items():
        assert second["assignments"][key]["split"] == value["split"]
    assert second["assignments"]["S-20260120-000000"]["split"] == "test"


def test_window_metrics_arithmetic():
    y = np.array([1, 1, 1, 0, 0, 0, 0, 0])
    p = np.array([0.9, 0.8, 0.2, 0.7, 0.1, 0.1, 0.1, 0.1])
    m = window_metrics(y, p, 0.5)
    assert (m["tp"], m["fn"], m["fp"], m["tn"]) == (2, 1, 1, 4)
    assert m["precision"] == pytest.approx(2 / 3)
    assert m["recall"] == pytest.approx(2 / 3)
    assert m["false_positive_rate"] == pytest.approx(0.2)
    # Lowering to 0.2 recovers the third chew for one extra false positive: F1 6/7.
    assert choose_threshold(y, p) == pytest.approx(0.2)


def test_decoder_matches_live_hysteresis():
    times = np.arange(20) * 0.5
    p = np.array([0.0] * 4 + [1.0] * 8 + [0.0] * 8)
    states, bouts = decode_bouts(times, p, DecoderConfig(high_threshold=0.6, low_threshold=0.4))
    # EMA(0.4) first reaches 0.6 on the 2nd high step (index 5); Live then
    # needs three consecutive steps above it, so chewing is confirmed at index 7.
    assert bouts and bouts[0][0] == pytest.approx(times[7])
    assert not states[:7].any() and states[7]
    assert bouts[0][1] < times[-1]


def test_false_bouts_in_unlabelled_time_are_not_scored(tmp_path):
    folder = make_canonical(tmp_path, "S-20260101-000001", duration=60.0, chewing=[(20, 40)], intervals=[
        _span(0, 15, "not_chewing"), _span(20, 40, "chewing"),
    ])
    session = load_canonical_session(folder)
    times = np.arange(0, 60, 0.5)
    p = np.where(((times > 22) & (times < 38)) | (times > 45), 1.0, 0.0)
    result = bout_metrics(session, times, p, DecoderConfig.from_threshold(0.6))
    assert result["detected_bouts"] == 1
    assert result["false_bouts"] == 0  # the 45 s+ bout lies in unlabelled time


def test_features_capture_chewing_periodicity():
    t = np.arange(150) / RATE
    rng = np.random.default_rng(1)
    windows = np.zeros((2, 7, 150), dtype=np.float32)
    windows[:, 6] = 1.0
    windows[0, 3] = 0.3 * np.sin(2 * np.pi * 1.5 * t)
    windows[1, 3] = rng.normal(0, 0.3, 150)
    features = compute_features(windows)
    names = feature_names()
    assert features[0, names.index("gx__dominant_hz")] == pytest.approx(1.33, abs=0.34)
    assert features[0, names.index("gx__chew_autocorr")] > features[1, names.index("gx__chew_autocorr")]
    assert features[0, names.index("gx__band_chew")] > 0.8


def test_end_to_end_smoke(tmp_path):
    from pipeline.eating.experiment import ExperimentConfig, rebuild_report, run_experiment

    workspace = tmp_path / "workspace"
    raw = workspace / "sessions" / "raw"
    for day in range(1, 13):
        chewing = day % 3 != 0
        spans = (
            [_span(0, 20, "not_chewing"), _span(20, 50, "chewing"), _span(50, 70, "not_chewing")]
            if chewing else [_span(0, 70, "not_chewing")]
        )
        make_canonical(raw, f"S-202601{day:02d}-000000", duration=70.0, seed=day,
                       intervals=spans, chewing=[(20, 50)] if chewing else [])
    config = ExperimentConfig(models=("prior", "logreg"), folds=3, bootstrap_resamples=50, note="smoke")
    run_dir = run_experiment(workspace=workspace, config=config, log=lambda *_: None)
    summary = json.loads((run_dir / "metrics.json").read_text())
    assert summary["selected_model"] == "logreg"
    assert summary["results"]["logreg"]["test"]["auc_roc"] > 0.9  # synthetic, separable by design
    assert (run_dir / "report.html").read_text().count("<details") >= 12
    index = json.loads((workspace / "experiments" / "eating" / "index.json").read_text())
    assert index["test_consultations"] == 1
    frame_test = json.loads((run_dir / "splits.json").read_text())["assignments"]
    second = run_experiment(workspace=workspace, config=config, log=lambda *_: None)
    assert json.loads((second / "splits.json").read_text())["assignments"].keys() == frame_test.keys()
    assert rebuild_report(second, workspace=workspace).exists()
    from dataclasses import replace

    exploratory = run_experiment(
        workspace=workspace, config=replace(config, evaluate_test=False), log=lambda *_: None
    )
    assert json.loads((exploratory / "metrics.json").read_text())["results"]["logreg"]["test"] is None
    index = json.loads((workspace / "experiments" / "eating" / "index.json").read_text())
    assert index["test_consultations"] == 2 and len(index["runs"]) == 3
    latest = json.loads((workspace / "experiments" / "eating" / "latest.json").read_text())
    assert latest["run_id"] == second.name  # exploratory runs never become the current model


def test_temporal_materializer_can_skip_invalid_sessions(tmp_path):
    from pipeline.temporal_dataset import TemporalDatasetError, materialize_temporal_dataset

    good = make_canonical(tmp_path / "raw", "S-20260101-000000", intervals=[_span(0, 60, "not_chewing")])
    bad = make_canonical(tmp_path / "raw", "S-20260102-000000", intervals=[_span(0, 60, "not_chewing")])
    (bad / "annotations" / "coarse_labels.json").unlink()
    with pytest.raises(TemporalDatasetError, match="--skip-invalid"):
        materialize_temporal_dataset([good, bad], tmp_path / "strict")
    result = materialize_temporal_dataset([good, bad], tmp_path / "lenient", skip_invalid=True)
    manifest = json.loads(result.manifest_path.read_text())
    assert [s["session_id"] for s in manifest["sessions"]] == ["S-20260101-000000"]
    assert manifest["rejected_sessions"][0]["session_dir"].endswith("S-20260102-000000")
    with pytest.raises(TemporalDatasetError, match="no valid schema-v2 sessions remain"):
        materialize_temporal_dataset([bad], tmp_path / "none", skip_invalid=True)


def test_guided_contexts_keep_confounds_apart():
    from pipeline.eating.dataset import guided_context_name

    assert guided_context_name("chew", "chew-1", "mixed", "baseline-90s") == "guided:chew"
    assert guided_context_name("chew", "gum-1", "gum", "gum-chewing") == "guided:chew:gum"
    assert guided_context_name("chew", "chew-talk", "mixed", "talking-while-eating") == "guided:chew+talk"
    assert guided_context_name("chew", "chew-slow", "mixed", "chewing-pace") == "guided:chew:slow"
    assert guided_context_name("drink", "drink-1", "water", "drinking") == "guided:drink:water"
    assert guided_context_name("other", "meal-1", "meal", "natural-meal") == "guided:other:natural-meal"
    assert guided_context_name("talk", "talk-1", None, "talking") == "guided:talk"
