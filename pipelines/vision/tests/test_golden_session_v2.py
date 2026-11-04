from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from chewsense.canonical import content_sha256, load_candidate_artifact
from chewsense.cli import cli
from chewsense.session_bundle import audit_session, finalize_session


REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "fixtures" / "golden" / "session-v2"


def test_golden_session_v2_end_to_end(tmp_path: Path):
    work = tmp_path / "golden"
    shutil.copytree(GOLDEN, work)
    session = work / "session"
    derived = work / "derived"

    candidates = load_candidate_artifact(derived / "candidates-v0001.json")
    assert candidates.schema_version == "2.0.0"
    assert candidates.chews[1].start_closed_ts == candidates.chews[0].end_closed_ts
    assert candidates.content_sha256 == (
        "458b0545db2646bccdd2f5b9e3e09d8c2d93371f1a4ab5e82c9cbc4873195e11"
    )

    draft_path = session / "annotations/review-draft.json"
    draft = json.loads(draft_path.read_text())
    draft["review_context"] = {"mode": "assisted", "sync_verified": True}
    draft_path.write_text(json.dumps(draft))
    materialized = finalize_session(session, derived)

    assert materialized.verified
    assert [item.id for item in materialized.chew_cycles] == [
        "ch_0000",
        "ch_0001",
        "manual-ch-0001",
    ]
    assert materialized.chew_cycles[1].open_peak_ts == pytest.approx(0.82)
    assert all(item.chew_event_ts == item.end_closed_ts for item in materialized.chew_cycles)
    assert set(materialized.excluded_chew_ids) == {
        "ch_0002",
        "ch_0003",
        "ch_0004",
    }
    assert [item.food_entry_id for item in materialized.chew_cycles] == [
        "fe_0000",
        "fe_0000",
        "manual-fe-0001",
    ]

    pointer = json.loads((session / "annotations" / "canonical_events.json").read_text())
    assert pointer["artifact_type"] == "canonical_pointer"
    assert "chew_cycles" not in pointer
    assert pointer["candidate_sha256"] == candidates.content_sha256
    assert (session / pointer["review_path"]).exists()
    assert (session / pointer["materialized_path"]).exists()

    rows = pd.read_csv(session / "annotations" / "motion-labels-v0001.csv")
    assert rows.iloc[0]["video_ts"] == pytest.approx(0.2)
    assert rows.loc[rows["chew_event_id"] == "ch_0000", "video_ts"].item() == pytest.approx(0.6)
    assert rows.loc[rows["video_ts"].between(1.6, 2.0), "event_valid"].eq(0).all()
    assert rows.loc[rows["video_ts"].between(2.1, 2.5), "event_valid"].eq(0).all()
    assert rows.loc[np.isclose(rows["video_ts"], 1.6), "event_valid"].eq(0).all()
    assert rows["label_source"].eq("human_reviewed").all()

    before = {
        path.relative_to(work): path.read_bytes()
        for path in work.rglob("*")
        if path.is_file()
    }
    report = audit_session(session, derived)
    after = {
        path.relative_to(work): path.read_bytes()
        for path in work.rglob("*")
        if path.is_file()
    }
    assert report.ok, report.model_dump()
    assert before == after, "audit-session must be read-only"


def test_stale_candidate_hash_blocks_before_freezing(tmp_path: Path):
    work = tmp_path / "stale"
    shutil.copytree(GOLDEN, work)
    draft_path = work / "session" / "annotations" / "review-draft.json"
    draft = json.loads(draft_path.read_text())
    draft["candidate_sha256"] = "0" * 64
    draft_path.write_text(json.dumps(draft))

    with pytest.raises(ValueError, match="stale review"):
        finalize_session(work / "session", work / "derived")

    assert not list((work / "session" / "annotations").glob("review-v*.json"))
    assert not list((work / "session" / "annotations").glob("materialized-v*.json"))


def test_invalid_selected_overlap_blocks_before_freezing(tmp_path: Path):
    work = tmp_path / "overlap"
    shutil.copytree(GOLDEN, work)
    annotations = work / "session" / "annotations"
    draft_path = annotations / "review-draft.json"
    draft = json.loads(draft_path.read_text())
    draft["added_chews"] = [
        {
            "id": "manual-overlap",
            "disposition": "added",
            "start_closed_ts": 0.5,
            "open_peak_ts": 0.65,
            "end_closed_ts": 0.9,
            "confidence": 1.0,
        }
    ]
    draft_path.write_text(json.dumps(draft))

    with pytest.raises(ValueError, match="overlap"):
        finalize_session(work / "session", work / "derived")

    assert not list(annotations.glob("review-v*.json"))
    assert not list(annotations.glob("materialized-v*.json"))


def test_uncertain_span_stays_masked_when_an_edit_overlaps_it(tmp_path: Path):
    work = tmp_path / "uncertain-overlap"
    shutil.copytree(GOLDEN, work)
    draft_path = work / "session" / "annotations" / "review-draft.json"
    draft = json.loads(draft_path.read_text())
    edited = next(
        item for item in draft["chew_decisions"] if item["candidate_id"] == "ch_0001"
    )
    edited.update(
        {
            "start_closed_ts": 0.6,
            "open_peak_ts": 1.2,
            "end_closed_ts": 2.05,
        }
    )
    draft_path.write_text(json.dumps(draft))

    finalize_session(work / "session", work / "derived")
    rows = pd.read_csv(work / "session" / "annotations" / "motion-labels-v0001.csv")
    overlap = rows["video_ts"].between(1.6, 2.0)
    assert rows.loc[overlap, "phase_valid"].eq(0).all()
    assert rows.loc[overlap, "event_valid"].eq(0).all()


def test_audit_detects_pointer_revision_tampering(tmp_path: Path):
    work = tmp_path / "pointer"
    shutil.copytree(GOLDEN, work)
    finalize_session(work / "session", work / "derived")
    pointer_path = work / "session" / "annotations" / "canonical_events.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["selected_review_revision"] = 99
    pointer_path.write_text(json.dumps(pointer))

    report = audit_session(work / "session", work / "derived")

    assert not report.ok
    assert any(item.code == "pointer_lineage" for item in report.errors)


def test_audit_detects_rehashed_id_parity_tampering(tmp_path: Path):
    work = tmp_path / "id-parity"
    shutil.copytree(GOLDEN, work)
    finalize_session(work / "session", work / "derived")
    pointer = json.loads(
        (work / "session" / "annotations" / "canonical_events.json").read_text()
    )
    materialized_path = work / "session" / pointer["materialized_path"]
    materialized = json.loads(materialized_path.read_text())
    materialized["excluded_chew_ids"] = ["ch_0003", "ch_0004"]
    materialized["content_sha256"] = content_sha256(materialized)
    materialized_path.write_text(json.dumps(materialized))
    pointer["materialized_sha256"] = materialized["content_sha256"]
    (work / "session" / "annotations" / "canonical_events.json").write_text(
        json.dumps(pointer)
    )

    report = audit_session(work / "session", work / "derived")

    assert not report.ok
    assert any(item.code == "id_parity" for item in report.errors)


def test_finalize_and_audit_cli_use_exact_derived_revision(tmp_path: Path):
    work = tmp_path / "cli"
    shutil.copytree(GOLDEN, work)
    runner = CliRunner()

    finalized = runner.invoke(
        cli,
        [
            "finalize-session",
            str(work / "session"),
            "--derived",
            str(work / "derived"),
        ],
    )
    assert finalized.exit_code == 0, finalized.output
    audited = runner.invoke(
        cli,
        [
            "audit-session",
            str(work / "session"),
            "--derived",
            str(work / "derived"),
        ],
    )
    assert audited.exit_code == 0, audited.output
    assert '"ok": true' in audited.output
