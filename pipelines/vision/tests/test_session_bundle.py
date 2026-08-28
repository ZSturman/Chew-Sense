from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from chewsense.canonical import build_candidate_artifact
from chewsense.events.schema import FoodEntryProposal, MouthCycle, VideoEvents
from chewsense.session_bundle import (
    ClockSync,
    LegacySchemaError,
    coarse_intervals_from_motion,
    create_candidates_from_events,
    load_coarse_intervals,
    load_session_manifest,
    migrate_legacy_session,
    save_session_manifest,
)


def _motion(path: Path, *, labeled: bool) -> None:
    data: dict[str, object] = {
        "timestamp": 50.2 + np.arange(10) / 10.0,
        "ax": 0.0,
        "ay": 0.0,
        "az": 0.0,
        "gx": 0.0,
        "gy": 0.0,
        "gz": 0.0,
    }
    if labeled:
        data["label"] = False
    pd.DataFrame(data).to_csv(path, index=False)


def test_explicit_legacy_migration_requires_fresh_review(tmp_path: Path):
    session = tmp_path / "Legacy"
    session.mkdir()
    _motion(session / "motion.csv", labeled=True)

    manifest = migrate_legacy_session(session)

    assert manifest.schema_version == "2.0.0"
    assert manifest.legacy_review_required
    assert manifest.clock_sync.video_clock_origin_host_time_s == pytest.approx(50.2)
    assert manifest.clock_sync.motion_to_video_offset_s == pytest.approx(0.0)
    assert load_coarse_intervals(session) is not None
    all_negative = build_candidate_artifact(
        session_id="Legacy",
        candidate_revision=1,
        chew_hypotheses=[{"start_ts": 0.1, "peak_ts": 0.2, "end_ts": 0.3}],
        food_entry_hypotheses=[{"ts": 0.05}],
        coarse_intervals=load_coarse_intervals(session),
    )
    assert all_negative.coarse_label_state == "all_negative"
    assert not all_negative.chews
    assert not all_negative.food_entries


def test_unlabeled_legacy_motion_remains_missing_coarse_review(tmp_path: Path):
    session = tmp_path / "Unlabeled"
    session.mkdir()
    _motion(session / "motion.csv", labeled=False)

    manifest = migrate_legacy_session(session)

    assert load_coarse_intervals(session) is None
    assert manifest.activity == "unknown"
    artifact = build_candidate_artifact(
        session_id="Unlabeled",
        candidate_revision=1,
        chew_hypotheses=[{"start_ts": 0.1, "peak_ts": 0.2, "end_ts": 0.3}],
        food_entry_hypotheses=[{"ts": 0.05}],
        coarse_intervals=None,
    )
    assert artifact.coarse_label_state == "missing"
    assert artifact.requires_full_coarse_review
    assert len(artifact.chews) == 1
    assert len(artifact.food_entries) == 1


def test_legacy_one_sample_clock_glitch_is_recovered_without_mutating_source(
    tmp_path: Path,
):
    session = tmp_path / "video-legacy"
    session.mkdir()
    source = session / "video-legacy.csv"
    rows = pd.DataFrame(
        {
            "timestamp": [10.00, 10.02, 10.04, 10.02, 10.08, 10.10],
            "ax": np.arange(6),
            "ay": 0.0,
            "az": 0.0,
            "gx": 0.0,
            "gy": 0.0,
            "gz": 0.0,
        }
    )
    rows.to_csv(source, index=False)
    source_bytes = source.read_bytes()

    manifest = migrate_legacy_session(session)

    assert source.read_bytes() == source_bytes
    assert manifest.motion.path == "motion-v2.csv"
    recovered = pd.read_csv(session / manifest.motion.path)
    assert np.all(np.diff(recovered["timestamp"].to_numpy()) > 0)
    assert len(recovered) == 5
    assert manifest.capture.motion_sample_gap_count == 1
    assert any("original preserved" in note for note in manifest.quality.notes)


def test_v1_manifest_is_never_silently_reinterpreted(tmp_path: Path):
    session = tmp_path / "v1"
    session.mkdir()
    (session / "session.json").write_text(
        json.dumps({"schema_version": "1.0.0", "session_id": "v1"})
    )

    with pytest.raises(LegacySchemaError, match="read-only"):
        load_session_manifest(session)
    with pytest.raises(LegacySchemaError, match="migrate-session --force"):
        migrate_legacy_session(session)


def test_coarse_materialization_uses_video_origin_not_first_motion(tmp_path: Path):
    motion = tmp_path / "motion.csv"
    pd.DataFrame(
        {
            "timestamp": [100.4, 100.5, 100.6, 100.7],
            "ax": 0,
            "ay": 0,
            "az": 0,
            "label": [False, True, True, False],
        }
    ).to_csv(motion, index=False)

    intervals = coarse_intervals_from_motion(motion, offset_s=100.0)

    assert intervals[0].start_ts == pytest.approx(0.4)
    assert intervals[1].label == "chewing"
    assert intervals[1].start_ts == pytest.approx(0.5)
    assert intervals[1].end_ts == pytest.approx(0.6)


def test_collect_v2_manifest_shape_loads_without_rewriting(tmp_path: Path):
    session = tmp_path / "Collect-v2"
    session.mkdir()
    _motion(session / "motion.csv", labeled=False)
    payload = {
        "schema_version": "2.0.0",
        "session_id": session.name,
        "wearer_id": "local-user",
        "source_app": "collect",
        "status": "coarse_reviewed",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "video": None,
        "motion": {"path": "motion.csv", "media_type": "motion_csv"},
        "audio": {
            "enabled": False,
            "permission": "denied",
            "purpose": "vad_and_human_review_only",
            "shipping_model_eligible": False,
            "safe_to_delete_after_finalization": True,
        },
        "clock_sync": {
            "time_domain": "host_monotonic",
            "video_clock_origin_host_time_s": 50.0,
            "motion_to_video_offset_s": 0.2,
            "drift_status": "not_estimated",
            "method": "capture_clock_fallback",
            "confidence": "low",
        },
        "capture": {
            "duration_s": 1.1,
            "interruption_count": 1,
            "motion_sample_gap_count": 2,
            "largest_motion_sample_gap_s": 0.15,
        },
        "quality": {
            "video_present": False,
            "motion_present": True,
            "review_audio_present": False,
        },
    }
    (session / "session.json").write_text(json.dumps(payload))

    manifest = load_session_manifest(session)

    assert manifest.status == "coarse_reviewed"
    assert manifest.audio is not None and not manifest.audio.enabled
    assert manifest.capture.interruption_count == 1
    assert manifest.clock_sync.video_clock_origin_host_time_s == 50.0

    save_session_manifest(session, manifest)
    saved = json.loads((session / "session.json").read_text())
    assert saved["audio"]["purpose"] == "vad_and_human_review_only"
    assert saved["audio"]["shipping_model_eligible"] is False
    assert saved["audio"]["safe_to_delete_after_finalization"] is True
    assert saved["quality"]["review_audio_present"] is False


def test_clock_latency_is_relative_to_video_origin():
    with pytest.raises(ValueError, match="motion_to_video_offset_s"):
        ClockSync(
            video_clock_origin_host_time_s=100.0,
            first_motion_host_time_s=100.25,
            motion_to_video_offset_s=100.25,
            method="shared_host_clock",
        )


def test_coarse_artifact_session_id_must_match_manifest(tmp_path: Path):
    session = tmp_path / "Mismatch"
    session.mkdir()
    _motion(session / "motion.csv", labeled=False)
    migrate_legacy_session(session)
    annotations = session / "annotations"
    annotations.mkdir()
    (annotations / "coarse_labels.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0.0",
                "artifact_type": "coarse_labels",
                "session_id": "someone-else",
                "intervals": [],
            }
        )
    )

    with pytest.raises(ValueError, match="session_id"):
        load_coarse_intervals(session)

    payload = json.loads((annotations / "coarse_labels.json").read_text())
    payload["session_id"] = session.name
    payload["artifact_type"] = "automatic_telemetry"
    (annotations / "coarse_labels.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="artifact_type"):
        load_coarse_intervals(session)


def test_food_entry_proposals_are_independent_of_episode_bouts(tmp_path: Path):
    session = tmp_path / "IndependentFoodEntry"
    session.mkdir()
    _motion(session / "motion.csv", labeled=False)
    migrate_legacy_session(session)
    events = VideoEvents(
        video_id=session.name,
        duration_s=2.0,
        fps=30.0,
        width=1,
        height=1,
        episodes=[],
        mouth_cycles=[
            MouthCycle(
                id="raw-cycle",
                start_ts=0.4,
                peak_ts=0.6,
                end_ts=0.8,
                duration_s=0.4,
                kind="visible_open",
                peak_magnitude=2.0,
                confidence=0.9,
            )
        ],
        food_entry_proposals=[
            FoodEntryProposal(
                id="raw-food",
                food_entry_ts=0.3,
                confidence=0.8,
            )
        ],
    )

    _, candidates = create_candidates_from_events(session, tmp_path / "derived", events)

    assert [item.id for item in candidates.chews] == ["ch_0000"]
    assert [item.id for item in candidates.food_entries] == ["fe_0000"]
