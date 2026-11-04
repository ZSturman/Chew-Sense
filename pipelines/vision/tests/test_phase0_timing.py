from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from chewsense.canonical import (
    ReviewContext,
    ReviewExclusion,
    ReviewRevision,
    content_sha256,
    load_candidate_artifact,
    load_review_draft,
    materialize_annotations,
    materialize_motion_rows,
    prepare_review_revision,
)
from chewsense.eval.phase0 import event_metrics, prepare_experiment
from chewsense.features import _models
from chewsense.ingest import analysis_grid, iter_frames, presentation_timeline, probe

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/golden/session-v2"


def motion_frame(data):
    return pd.DataFrame(data).assign(ax=0.0, ay=0.0, az=0.0, gx=0.0, gy=0.0, gz=0.0)


def artifacts(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "golden")
    session, derived = tmp_path / "golden/session", tmp_path / "golden/derived"
    candidates = load_candidate_artifact(next(derived.glob("candidates-v*.json")))
    draft = load_review_draft(session / "annotations/review-draft.json")
    _, review = prepare_review_revision(session / "annotations", draft)
    return candidates, review, materialize_annotations(candidates, review)


def test_projection_never_moves_events_into_distant_samples(tmp_path):
    candidates, review, annotations = artifacts(tmp_path)
    event = annotations.chew_cycles[0]
    # Sensor coverage brackets the video but the event neighborhood is missing.
    times = np.array([0, 0.02, 10, 10.02])
    path = tmp_path / "motion.csv"
    motion_frame({"timestamp": times + 100000}).to_csv(path, index=False)
    rows = materialize_motion_rows(
        path,
        annotations,
        candidates=candidates,
        time_offset=100000,
        duration_s=12,
        coarse_intervals=[],
        review_context=ReviewContext(sync_verified=True),
    )
    assert rows.chew_event.sum() == 0
    assert rows.food_entry_event.sum() == 0
    assert rows.phase_valid.sum() == 0
    # Event before/after measured range is not projected to an endpoint.
    motion_frame(
        {"timestamp": [100000 + event.chew_event_ts + 1, 100002 + event.chew_event_ts]}
    ).to_csv(path, index=False)
    rows = materialize_motion_rows(
        path,
        annotations,
        candidates=candidates,
        time_offset=100000,
        duration_s=12,
        coarse_intervals=[],
        review_context=ReviewContext(sync_verified=True),
    )
    assert rows.chew_event.sum() == 0


def test_review_unknown_and_unconfirmed_clock_mask_all_targets(tmp_path):
    candidates, review, annotations = artifacts(tmp_path)
    path = tmp_path / "motion.csv"
    motion_frame({"timestamp": 3600 + np.arange(0, 4, 0.02)}).to_csv(path, index=False)
    args = dict(candidates=candidates, time_offset=3600, duration_s=4, coarse_intervals=[])
    rows = materialize_motion_rows(path, annotations, review_context=ReviewContext(), **args)
    assert (
        not rows[["label_valid", "event_valid", "food_entry_valid", "phase_valid"]].to_numpy().any()
    )
    rows = materialize_motion_rows(
        path,
        annotations,
        review_context=ReviewContext(
            sync_verified=True, excluded_spans=(ReviewExclusion(start_ts=0.1, end_ts=2.5),)
        ),
        **args,
    )
    excluded = rows.video_ts.between(0.1, 2.5)
    assert (
        not rows.loc[
            excluded,
            ["label_valid", "event_valid", "food_entry_valid", "phase_valid", "chew_event"],
        ]
        .to_numpy()
        .any()
    )
    assert rows.loc[~excluded, "label_valid"].any()


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_projection_rejects_nonfinite_clock(tmp_path, bad):
    candidates, review, annotations = artifacts(tmp_path)
    path = tmp_path / "motion.csv"
    motion_frame({"timestamp": [bad, 1]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="finite"):
        materialize_motion_rows(
            path,
            annotations,
            candidates=candidates,
            time_offset=0,
            duration_s=4,
            coarse_intervals=[],
        )


def test_historical_review_hash_and_additive_context_round_trip(tmp_path):
    _, review, _ = artifacts(tmp_path)
    payload = review.model_dump(mode="json")
    assert "review_context" not in payload
    assert ReviewRevision.model_validate(payload).content_sha256 == content_sha256(payload)
    payload.pop("content_sha256")
    payload["review_context"] = {
        "mode": "blind",
        "active_seconds": 12.5,
        "sync_verified": False,
        "excluded_spans": [{"start_ts": 0.1, "end_ts": 0.3, "disposition": "unobservable"}],
    }
    # Defaults participate in newly written hashes, exactly as in freeze_review.
    draft = load_review_draft(FIXTURE / "session/annotations/review-draft.json")
    draft.review_context = ReviewContext.model_validate(payload["review_context"])
    _, frozen = prepare_review_revision(tmp_path, draft)
    assert ReviewRevision.model_validate_json(frozen.model_dump_json()) == frozen


def test_models_cache_rejects_corruption_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.setenv("CHEWSENSE_MODELS_DIR", str(tmp_path))
    target = tmp_path / "face_landmarker.task"
    target.write_bytes(b"corrupt cache" * 200)
    with pytest.raises(ValueError, match="cache hash mismatch"):
        _models.get_model(target.name)
    assert target.read_bytes().startswith(b"corrupt")


def test_matching_maximizes_count_before_distance():
    # Greedy nearest would consume g2 and miss g1; optimal assignment finds both.
    result = event_metrics([("p1", 0.19), ("p2", 0.3)], [("g1", 0), ("g2", 0.2)], 0.2)
    assert result["tp"] == 2
    assert result["f1"] == 1
    empty = event_metrics([], [("g", 1)], 0.25)
    assert empty["fn"] == 1 and empty["timestamp_mae_s"] is None
    assert empty["missed_gold_ids"] == ["g"]


def test_analysis_grid_preserves_real_time_and_masks_gaps():
    pts = np.array([0.2, 0.23, 0.28, 0.3, 1, 1.04])
    grid, indices, valid = analysis_grid(pts)
    assert grid[0] == 0.2
    assert grid[-1] == pytest.approx(1.03333333)
    assert not valid[(grid > 0.36) & (grid < 0.94)].any()
    assert np.all(abs(pts[indices[valid]] - grid[valid]) <= 0.05)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_variable_rate_video_uses_display_pts_and_decodes_all_frames(tmp_path):
    video = tmp_path / "vfr.mov"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x64:rate=30:duration=1",
            "-vf",
            "select=not(mod(n\\,3))+eq(n\\,1)",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    pts, duration = presentation_timeline(video)
    assert np.ptp(np.diff(pts)) > 0.01
    frames = list(iter_frames(video))
    assert [t for _, t, _ in frames] == pytest.approx(pts)
    assert probe(video).duration_s == duration


def test_copy_never_changes_source_or_imports_old_event_truth(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(FIXTURE / "session", source)
    # This synthetic fixture has a video reference with a deliberately fake hash; test motion-only copy.
    data = json.loads((source / "session.json").read_text())
    data["video"] = None
    (source / "session.json").write_text(json.dumps(data))
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    result = prepare_experiment(source, tmp_path / "pilot")
    assert not (result / "annotations/review-draft.json").exists()
    assert before == {
        p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    with pytest.raises(FileExistsError):
        prepare_experiment(source, tmp_path / "pilot")


def test_finalize_without_clock_confirmation_preserves_visual_truth_but_masks_motion(tmp_path):
    from chewsense.session_bundle import finalize_session

    shutil.copytree(FIXTURE, tmp_path / "golden")
    session, derived = tmp_path / "golden/session", tmp_path / "golden/derived"
    annotations = finalize_session(session, derived)
    assert annotations.verified and len(annotations.chew_cycles) == 3
    labels = pd.read_csv(session / "annotations/motion-labels-v0001.csv")
    assert (
        not labels[["label_valid", "event_valid", "phase_valid", "food_entry_valid"]]
        .to_numpy()
        .any()
    )


def test_independent_evaluation_and_legacy_rejection(tmp_path, monkeypatch):
    from chewsense.canonical import (
        AddedChew,
        ReviewDraft,
        build_candidate_artifact,
        write_candidate_artifact,
    )
    from chewsense.eval.phase0 import evaluate_experiment
    from chewsense.session_bundle import load_coarse_intervals, load_session_manifest

    session = tmp_path / "session"
    shutil.copytree(FIXTURE / "session", session)
    payload = json.loads((session / "session.json").read_text())
    payload.update(source_app="collect", legacy_review_required=False)
    video = session / "test-video.mov"
    video.write_bytes(b"synthetic video bytes; timing inspected separately")
    payload["video"] = {
        "path": video.name,
        "media_type": "video",
        "sha256": _models.hashlib.sha256(video.read_bytes()).hexdigest(),
    }
    (session / "session.json").write_text(json.dumps(payload))
    manifest = load_session_manifest(session)
    coarse = load_coarse_intervals(session)
    pred = build_candidate_artifact(
        session_id=manifest.session_id,
        candidate_revision=1,
        chew_hypotheses=[
            dict(start_ts=0.2, peak_ts=0.4, end_ts=0.6),
            dict(start_ts=0.7, peak_ts=0.8, end_ts=0.9),
        ],
        food_entry_hypotheses=[],
        coarse_intervals=coarse,
        debug_telemetry={"video_sha256": payload["video"]["sha256"]},
    )
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(pred.model_dump_json())
    candidate_path, candidates = write_candidate_artifact(
        tmp_path / "blind",
        session_id=manifest.session_id,
        chew_hypotheses=[],
        food_entry_hypotheses=[],
        coarse_intervals=coarse,
    )
    draft = ReviewDraft(
        session_id=manifest.session_id,
        candidate_revision=1,
        candidate_sha256=candidates.content_sha256,
        updated_at="2026-10-26",
        verified=True,
        full_video_pass_completed=True,
        added_chews=(
            AddedChew(id="human-1", start_closed_ts=0.2, open_peak_ts=0.4, end_closed_ts=0.6),
        ),
        review_context=ReviewContext(mode="blind", active_seconds=40),
    )
    review_path, frozen = prepare_review_revision(session / "annotations", draft)
    review_path.write_text(frozen.model_dump_json())
    monkeypatch.setattr(
        "chewsense.eval.phase0.timing_report", lambda _: {"sensor_coverage_fraction": 0.9}
    )
    metrics = evaluate_experiment(prediction_path, review_path, candidate_path)
    assert metrics["tp"] == 1 and metrics["fp"] == 1 and metrics["fn"] == 0
    assert metrics["gold_active_seconds"] == 40
    payload["source_app"] = "legacy_import"
    (session / "session.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="legacy"):
        evaluate_experiment(prediction_path, review_path, candidate_path)


def test_nonzero_unapplied_drift_blocks_before_freezing(tmp_path):
    from chewsense.session_bundle import finalize_session

    shutil.copytree(FIXTURE, tmp_path / "golden")
    session = tmp_path / "golden/session"
    payload = json.loads((session / "session.json").read_text())
    payload["clock_sync"]["estimated_drift_ppm"] = 150
    (session / "session.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="drift"):
        finalize_session(session, tmp_path / "golden/derived")
    assert not list((session / "annotations").glob("review-v*.json"))


def test_known_video_missing_spans_mask_motion_negatives(tmp_path):
    candidates, review, annotations = artifacts(tmp_path)
    candidates.debug_telemetry["run_manifest"] = {
        "video_meta": {
            "duration_s": 4,
            "source_first_pts_s": 0.3,
            "missing_video_spans": [[1, 2]],
        }
    }
    motion = tmp_path / "motion.csv"
    motion_frame({"timestamp": 100000 + np.arange(0, 4, 0.02)}).to_csv(motion, index=False)
    labels = materialize_motion_rows(
        motion,
        annotations,
        candidates=candidates,
        time_offset=100000,
        duration_s=4,
        coarse_intervals=[],
        review_context=ReviewContext(sync_verified=True),
    )
    absent = (labels.video_ts < 0.3) | labels.video_ts.between(1.01, 1.99)
    assert not labels.loc[absent, ["label_valid", "event_valid", "phase_valid"]].to_numpy().any()
    assert labels.loc[~absent, "label_valid"].any()


def test_clock_marker_residuals_and_coverage_are_diagnostics(tmp_path):
    from chewsense.eval.phase0 import timing_report

    session = tmp_path / "session"
    shutil.copytree(FIXTURE / "session", session)
    payload = json.loads((session / "session.json").read_text())
    payload["video"] = None
    (session / "session.json").write_text(json.dumps(payload))
    origin = payload["clock_sync"]["video_clock_origin_host_time_s"]
    markers = [dict(video_ts=t, motion_host_ts=origin + t + 0.04) for t in [0.5, 1.5, 2.5]]
    report = timing_report(session, markers)
    assert report["sync_markers"]["median_absolute_error_s"] == pytest.approx(0.04)
    assert report["sync_markers"]["pilot_event_target_met"]
    assert report["physical_sync_validated"] is False
    assert report["missing_sensor_seconds"] > 0


def test_missing_coarse_review_never_becomes_all_negative_at_finalization(tmp_path):
    from chewsense.session_bundle import finalize_session

    shutil.copytree(FIXTURE, tmp_path / "golden")
    session = tmp_path / "golden/session"
    (session / "annotations/coarse_labels.json").unlink()
    with pytest.raises(ValueError, match="coarse review is missing"):
        finalize_session(session, tmp_path / "golden/derived")
    assert not list((session / "annotations").glob("review-v*.json"))


def test_tampered_motion_blocks_before_any_frozen_review_is_written(tmp_path):
    from chewsense.session_bundle import finalize_session, load_session_manifest

    shutil.copytree(FIXTURE, tmp_path / "golden")
    session = tmp_path / "golden/session"
    motion = session / load_session_manifest(session).motion.path
    motion.write_text(motion.read_text() + "\n")
    with pytest.raises(ValueError, match="media hash mismatch"):
        finalize_session(session, tmp_path / "golden/derived")
    assert not list((session / "annotations").glob("review-v*.json"))


def test_native_writer_empty_edit_pts_match_playback_clock():
    video = Path(__file__).resolve().parents[3] / "fixtures/sessions/timing/leading-empty-edit.mov"
    pts, duration = presentation_timeline(video)
    np.testing.assert_allclose(pts, [0.3, 0.333333, 0.4], atol=1e-6, rtol=0)
    assert duration == pytest.approx(0.466667, abs=1e-6)
    assert [t for _, t, _ in iter_frames(video)] == pytest.approx(pts)


def test_explicit_unknown_coarse_spans_are_excluded_without_blocking_known_review():
    from chewsense.canonical import build_candidate_artifact
    from chewsense.session_bundle import CoarseInterval

    candidate = build_candidate_artifact(
        session_id="known-unknown",
        candidate_revision=1,
        chew_hypotheses=[dict(start_ts=1.2, peak_ts=1.4, end_ts=1.6)],
        food_entry_hypotheses=[],
        coarse_intervals=[
            CoarseInterval(start_ts=0, end_ts=1, label="unknown"),
            CoarseInterval(start_ts=1, end_ts=2, label="chewing"),
        ],
    )
    assert not candidate.requires_full_coarse_review
    assert candidate.coarse_label_state == "reviewed_intervals"
    assert len(candidate.chews) == 1


def test_confirmed_clock_projects_positive_events_to_existing_samples(tmp_path):
    candidates, review, annotations = artifacts(tmp_path)
    motion = tmp_path / "motion.csv"
    motion_frame({"timestamp": 100000 + np.arange(0, 3.2, 0.02)}).to_csv(motion, index=False)
    rows = materialize_motion_rows(
        motion,
        annotations,
        candidates=candidates,
        time_offset=100000,
        duration_s=3.2,
        coarse_intervals=[],
        review_context=ReviewContext(sync_verified=True),
    )
    assert rows.chew_event.sum() == 3
    assert rows.loc[rows.chew_event == 1, "event_valid"].eq(1).all()
    expected = {c.id: c.chew_event_ts for c in annotations.chew_cycles}
    for row in rows[rows.chew_event == 1].itertuples():
        assert abs(row.video_ts - expected[row.chew_event_id]) <= 0.01


def test_legacy_diagnostic_copy_cannot_be_finalized_as_training_truth(tmp_path):
    from chewsense.session_bundle import finalize_session

    shutil.copytree(FIXTURE, tmp_path / "golden")
    session = tmp_path / "golden/session"
    (session / "pilot-provenance.json").write_text('{"legacy_diagnostic_only":true}')
    with pytest.raises(ValueError, match="legacy diagnostic"):
        finalize_session(session, tmp_path / "golden/derived")
    assert not list((session / "annotations").glob("review-v*.json"))


def test_events_cannot_project_to_nonfinite_sensor_rows(tmp_path):
    candidates, review, annotations = artifacts(tmp_path)
    first_event = annotations.chew_cycles[0].chew_event_ts
    data = motion_frame({"timestamp": np.arange(0, 3.2, 0.02)})
    row = int(np.argmin(abs(data.timestamp.to_numpy() - first_event)))
    data.loc[row, "gx"] = np.nan
    path = tmp_path / "motion.csv"
    data.to_csv(path, index=False)
    labels = materialize_motion_rows(
        path,
        annotations,
        candidates=candidates,
        time_offset=0,
        duration_s=3.2,
        coarse_intervals=[],
        review_context=ReviewContext(sync_verified=True),
    )
    assert labels.loc[row, ["chew_event", "label_valid", "phase_valid", "event_valid"]].eq(0).all()
    assert annotations.chew_cycles[0].id not in labels.chew_event_id.to_list()
    assert labels.chew_event.sum() == len(annotations.chew_cycles) - 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_optional_audio_retains_initial_presentation_offset(tmp_path):
    from chewsense.ingest import extract_audio

    audio = tmp_path / "review.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=0.5",
            "-af",
            "asetpts=PTS+0.3/TB",
            "-c:a",
            "aac",
            str(audio),
        ],
        check=True,
    )
    pair = extract_audio(audio)
    assert pair is not None
    samples, sr = pair
    assert np.sqrt(np.mean(samples[: int(0.2 * sr)] ** 2)) < 1e-8
    assert np.sqrt(np.mean(samples[int(0.35 * sr) : int(0.5 * sr)] ** 2)) > 0.01
