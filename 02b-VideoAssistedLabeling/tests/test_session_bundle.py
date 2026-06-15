from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from chewsense.events.schema import (
    Bite,
    Chew,
    Episode,
    MouthStateSegment,
    VideoEvents,
)
from chewsense.session_bundle import finalize_session, migrate_legacy_session


def _write_session(root: Path) -> tuple[Path, Path]:
    session = root / "Eating-test"
    derived = root / "derived"
    session.mkdir()
    derived.mkdir()
    ts = 100.0 + np.arange(300) / 100.0
    pd.DataFrame({
        "timestamp": ts,
        "ax": 0.0, "ay": 0.0, "az": 0.0,
        "gx": 0.0, "gy": 0.0, "gz": 0.0,
        "label": (np.arange(300) >= 50) & (np.arange(300) <= 220),
    }).to_csv(session / "Eating-test.csv", index=False)
    (session / "Eating-test.mov").write_bytes(b"fixture")

    chew = Chew(
        id="ch_0000",
        start_ts=0.8,
        peak_ts=1.0,
        end_ts=1.2,
        duration_s=0.4,
        amplitude=0.5,
        relative_amplitude=1.0,
        confidence=0.9,
    )
    bite = Bite(id="bt_0000", ts=0.7, confidence=0.8)
    episode = Episode(
        id="ep_000",
        start_ts=0.7,
        end_ts=1.5,
        start_reason="bite_detected",
        end_reason="chewing_stopped",
        confidence=0.85,
        bites=[bite],
        chews=[chew],
    )
    events = VideoEvents(
        video_id="Eating-test",
        duration_s=3.0,
        fps=30.0,
        width=1080,
        height=1920,
        episodes=[episode],
        mouth_state_segments=[
            MouthStateSegment(
                state="closed",
                start_ts=0.0,
                end_ts=0.8,
                duration_s=0.8,
            ),
            MouthStateSegment(
                state="opening",
                start_ts=0.8,
                end_ts=1.0,
                duration_s=0.2,
            ),
            MouthStateSegment(
                state="closing",
                start_ts=1.0,
                end_ts=1.2,
                duration_s=0.2,
            ),
        ],
    )
    (derived / "events.json").write_text(json.dumps(events.model_dump()))
    return session, derived


def test_migrate_and_finalize_creates_immutable_training_views(tmp_path: Path):
    session, derived = _write_session(tmp_path)
    manifest = migrate_legacy_session(session, wearer_id="person-1")

    assert manifest.clock_sync.method == "legacy_first_motion_fallback"
    assert manifest.clock_sync.motion_to_video_offset_s == pytest.approx(100.0)
    assert (session / "annotations" / "coarse_labels.json").exists()

    (session / "review_overrides.json").write_text(json.dumps({
        "session_id": session.name,
        "verified": True,
        "full_video_pass_completed": True,
        "verified_chew_ids": ["ch_0000"],
        "verified_bite_ids": ["bt_0000"],
    }))
    annotations = finalize_session(session, derived)

    assert annotations.verified
    assert annotations.revision == 1
    assert len(annotations.chew_cycles) == 1
    assert len(annotations.bite_events) == 1
    assert (session / "annotations" / "review-v0001.json").exists()
    assert (session / "manifests" / "cv_training.json").exists()
    assert (session / "manifests" / "motion_training.json").exists()

    labels = pd.read_csv(session / "annotations" / "motion_labels.csv")
    expected = {
        "mouth_state", "chew_phase", "chew_id", "bite_event", "bite_id",
        "bout_id", "label_valid", "label_source", "annotation_revision",
    }
    assert expected <= set(labels.columns)
    assert labels["annotation_revision"].eq(1).all()
    assert labels["bite_event"].sum() == 1

    second = finalize_session(session, derived)
    assert second.revision == 2
    assert (session / "annotations" / "review-v0001.json").exists()
    assert (session / "annotations" / "review-v0002.json").exists()


def test_finalize_rejects_incomplete_review(tmp_path: Path):
    session, derived = _write_session(tmp_path)
    migrate_legacy_session(session)
    (session / "review_overrides.json").write_text(json.dumps({
        "session_id": session.name,
        "verified": True,
        "full_video_pass_completed": False,
        "verified_chew_ids": ["ch_0000"],
        "verified_bite_ids": ["bt_0000"],
    }))

    with pytest.raises(ValueError, match="review is incomplete"):
        finalize_session(session, derived)


def test_field_test_migration_selects_motion_csv_not_feedback(tmp_path: Path):
    session = tmp_path / "FieldTest-1"
    session.mkdir()
    pd.DataFrame({
        "event": ["false_positive"],
        "timestamp": [1.0],
    }).to_csv(session / "feedback.csv", index=False)
    pd.DataFrame({
        "timestamp": 50.0 + np.arange(20) / 100.0,
        "ax": 0.0, "ay": 0.0, "az": 0.0,
        "gx": 0.0, "gy": 0.0, "gz": 0.0,
        "label": 0,
    }).to_csv(session / "motion.csv", index=False)

    manifest = migrate_legacy_session(session, source_app="live")

    assert manifest.motion.path == "motion.csv"
    assert manifest.clock_sync.motion_to_video_offset_s == pytest.approx(50.0)
