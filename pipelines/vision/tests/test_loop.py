"""Guided collection loop: definitions, sidecars, import, processing, service,
review prioritization, evaluation independence, planning and training gates.

All media is synthetic. No MediaPipe inference or model training runs here.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import ssl
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from chewsense.canonical import AddedChew, ReviewContext, ReviewDraft, load_candidate_artifact
from chewsense.loop import experiments as ex
from chewsense.loop.controller import LoopController
from chewsense.loop.guided import (
    GuidedSession,
    intended_activity_track,
    load_guided_session,
    verify_guided_session,
    write_json_atomic,
)
from chewsense.loop.importer import ArchiveError, import_archive
from chewsense.loop.lmstudio import LMStudio, validate_summary
from chewsense.loop.pairing import DeviceRegistry, pairing_proof, server_proof
from chewsense.loop.processing import SESSION_STEPS, Processor, StepInterrupted
from chewsense.loop.review_tasks import priority_segments
from chewsense.loop.store import LoopPaths, read_json
from chewsense.loop.sync_assess import MIN_MARKERS, assess_markers
from chewsense.loop.training import readiness

from .loop_fixtures import (
    ORIGIN,
    fake_vision,
    guided_sidecar,
    make_session,
    make_video,
    seed,
    sha,
    loop_config,
    zip_session,
)

REPO = Path(__file__).resolve().parents[3]
BUNDLED_CATALOG = REPO / "apps/collect-ios/ChewSenseCollect/Resources/SeedExperimentCatalog.json"


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("media") / "clip.mov", 12.0)


@pytest.fixture
def loop(tmp_path):
    return LoopPaths(tmp_path / "loop").ensure()


def registry_for(paths: LoopPaths) -> ex.ExperimentRegistry:
    registry = ex.ExperimentRegistry(paths.experiments / "definitions")
    registry.seed()
    return registry


def test_status_publication_survives_concurrent_writers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    status = tmp_path / "status.json"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: write_json_atomic(status, {"sequence": n, "rows": [n] * 100}), range(100)))
    value = read_json(status)
    assert value["rows"] == [value["sequence"]] * 100
    assert not list(tmp_path.glob("*.tmp"))


# --------------------------------------------------------------- definitions
def test_seed_definitions_parse_and_cover_examples():
    ids = {p.stem for p in ex.SEED_DIR.glob("*.json")}
    assert {"baseline-90s", "quiet-sitting", "walking-no-food", "crunchy-food", "soft-food",
            "drinking", "talking", "head-movement"} <= ids
    baseline = seed()
    assert baseline.total_seconds == 90
    assert [a.duration_s for a in baseline.activities] == [5, 15, 20, 10, 5, 15, 10, 5, 5]
    assert sum(a.sync_marker for a in baseline.activities) == 3


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["activities"][2].update(intended_label="not_chewing"), "chew activities"),
    (lambda d: d["activities"][0].update(intended_label="chewing"), "sync markers"),
    (lambda d: d.update(food={"kind": "none"}), "require food"),
    (lambda d: d["activities"].extend(
        [dict(d["activities"][1], id=f"x{i}", duration_s=170) for i in range(4)]), "limited to"),
    (lambda d: d["activities"][1].update(instruction="Chew while driving"), "safety"),
    (lambda d: d["activities"][1].update(duration_s=1), "duration"),
    (lambda d: d.update(unknown_field=1), "Extra inputs"),
])
def test_invalid_definitions_are_rejected(mutate, message):
    payload = seed().model_dump(mode="json")
    mutate(payload)
    with pytest.raises(ValueError, match=message):
        ex.ExperimentDefinition.model_validate(payload)


def test_walking_requires_safety_notes_and_no_face_requirement():
    payload = seed("walking-no-food").model_dump(mode="json")
    payload["safety_notes"] = []
    with pytest.raises(ValueError, match="safety notes"):
        ex.ExperimentDefinition.model_validate(payload)


def test_definition_hash_is_stable_across_number_spellings():
    payload = seed().model_dump(mode="json")
    as_floats = json.loads(json.dumps(payload).replace('"duration_s": 5,', '"duration_s": 5.0,'))
    assert ex.definition_sha256(payload) == ex.definition_sha256(as_floats)


def test_registry_versions_are_immutable_and_catalog_bumps_only_on_change(loop):
    registry = registry_for(loop)
    definition = seed()
    registry.add(definition)  # identical re-add is a no-op
    changed = definition.model_dump()
    changed["title"] = "Changed Title"
    with pytest.raises(FileExistsError):
        registry.add(changed)
    changed["version"] = 2
    registry.add(changed)
    assert registry.versions("baseline-90s") == [1, 2]
    assert registry.state("baseline-90s")["published_version"] == 1
    registry.publish("baseline-90s", 2, by="test")
    assert registry.load("baseline-90s", 1).title == "90-Second Baseline"
    registry.retire("talking", by="test", reason="enough data")
    assert "talking" not in {d.experiment_id for d in registry.published()}

    controller = LoopController(loop, loop_config())
    first = controller.publish_catalog({}, {})
    again = controller.publish_catalog({}, {})
    assert again["catalog_version"] == first["catalog_version"]
    bumped = controller.publish_catalog({"baseline-90s": 1}, {})
    assert bumped["catalog_version"] == first["catalog_version"] + 1
    entry = next(e for e in bumped["experiments"] if e["definition"]["experiment_id"] == "baseline-90s")
    assert entry["progress"] == {"target": 3, "completed_valid": 1, "remaining": 2}
    assert entry["definition"]["version"] == 2


def test_lower_version_is_rejected(loop):
    registry = registry_for(loop)
    registry.add(dict(seed().model_dump(), version=3))
    with pytest.raises(ValueError, match="greater"):
        registry.add(dict(seed().model_dump(), version=2, title="Older"))


def test_natural_meal_update_meets_marker_gate_without_rewriting_v1(loop):
    registry = registry_for(loop)
    original = registry.load("natural-meal", 1)
    v1_digest = ex.definition_sha256(original)
    path = REPO / "pipelines/vision/configs/experiment-updates/natural-meal-v2.json"
    revised = registry.add(json.loads(path.read_text()))
    assert revised.completion.required_sync_markers == MIN_MARKERS
    assert [a.id for a in revised.activities if a.sync_marker] == ["nod-start", "nod-midpoint", "nod-end"]
    assert 300 < revised.total_seconds < original.total_seconds
    assert all(a.intended_label == "unknown" for a in revised.activities if not a.sync_marker)
    assert registry.state("natural-meal")["published_version"] == 1
    registry.publish("natural-meal", revised.version, by="test")
    catalog = LoopController(loop, loop_config()).publish_catalog({}, {})
    entry = next(e for e in catalog["experiments"] if e["definition"]["experiment_id"] == "natural-meal")
    assert entry["definition"]["version"] == 2
    assert ex.definition_sha256(registry.load("natural-meal", 1)) == v1_digest


@pytest.mark.parametrize("name", ["baseline-90s", "talking", "chewing-pace", "head-movement"])
def test_one_nod_updates_meet_marker_gate_and_keep_activities(loop, name):
    registry = registry_for(loop)
    original = registry.load(name, 1)
    v1_digest = ex.definition_sha256(original)
    path = REPO / f"pipelines/vision/configs/experiment-updates/{name}-v2.json"
    revised = registry.add(json.loads(path.read_text()))
    markers = [a for a in revised.activities if a.sync_marker]
    assert revised.version == 2 and revised.completion.required_sync_markers == MIN_MARKERS == len(markers)
    assert all("Nod down once" in a.instruction or "one downward nod" in a.instruction for a in markers)

    def core(definition):  # everything except nods and the stillness framing them
        return [(a.id, a.kind, a.duration_s, a.intended_label) for a in definition.activities
                if not a.sync_marker and not a.id.startswith("still-") or a.id in {"still-1", "still-2"}]
    assert core(revised) == core(original)
    registry.publish(name, 2, by="test")
    catalog = LoopController(loop, loop_config()).publish_catalog({}, {})
    entry = next(e for e in catalog["experiments"] if e["definition"]["experiment_id"] == name)
    assert entry["definition"]["version"] == 2
    assert ex.definition_sha256(registry.load(name, 1)) == v1_digest


def test_bundled_ios_catalog_matches_seed_definitions():
    bundled = json.loads(BUNDLED_CATALOG.read_text())
    shipped = {e["definition"]["experiment_id"]: e["definition_sha256"] for e in bundled["experiments"]}
    expected = {p.stem: ex.definition_sha256(json.loads(p.read_text())) for p in ex.SEED_DIR.glob("*.json")}
    assert shipped == expected
    for entry in bundled["experiments"]:
        assert ex.definition_sha256(entry["definition"]) == entry["definition_sha256"]


# ------------------------------------------------------------------ sidecar
def test_sidecar_verification_and_intended_track(tmp_path, loop, video):
    registry = registry_for(loop)
    folder = make_session(tmp_path, "g1", video=video)
    check = verify_guided_session(folder, registry, manifest_session_id="g1", video_clock_origin=ORIGIN)
    assert check["definition_verified"] and check["guided"]
    track = intended_activity_track(load_guided_session(folder))
    assert track["verified"] is False and track["provenance"] == "guided_instruction"
    assert track["intervals"][0]["start_ts"] == pytest.approx(0.6)
    assert track["invalid_intervals"][0]["reasons"] == ["face_missing"]

    sidecar = json.loads((folder / "guided_session.json").read_text())
    sidecar["definition"]["title"] = "Silently edited"
    (folder / "guided_session.json").write_text(json.dumps(sidecar))
    check = verify_guided_session(folder, registry, manifest_session_id="g1", video_clock_origin=ORIGIN + 1)
    assert not check["definition_verified"]
    assert any("clock" in p for p in check["problems"])
    assert any("sha256" in p for p in check["problems"])


def test_actual_timestamps_are_preserved_and_order_is_enforced():
    sidecar = guided_sidecar("s", seed())
    # A paused activity: the real end differs from presented + scheduled duration.
    sidecar["attempts"][2].update(paused_seconds=4.0, ended_host_s=sidecar["attempts"][2]["ended_host_s"] + 4)
    guided = GuidedSession.model_validate(sidecar)
    track = intended_activity_track(guided)
    chew = next(i for i in track["intervals"] if i["activity_id"] == "chew-1")
    assert chew["end_ts"] - chew["start_ts"] == pytest.approx(1.2 + 4.0)
    sidecar["attempts"][0]["started_host_s"] = sidecar["attempts"][0]["presented_host_s"] - 1
    with pytest.raises(ValueError, match="before it was presented"):
        GuidedSession.model_validate(sidecar)
    bad = guided_sidecar("s", seed())
    bad["events"][1]["host_time_s"] = bad["events"][0]["host_time_s"] - 1
    with pytest.raises(ValueError, match="order"):
        GuidedSession.model_validate(bad)


def test_interrupted_attempts_never_become_intended_intervals():
    sidecar = guided_sidecar("s", seed())
    sidecar["attempts"][2].update(outcome="interrupted")
    sidecar["attempts"][3].update(outcome="abandoned", ended_host_s=None)
    track = intended_activity_track(GuidedSession.model_validate(sidecar))
    assert {"chew-1", "stop-1"}.isdisjoint(i["activity_id"] for i in track["intervals"])
    assert [i["activity_id"] for i in track["interrupted_attempts"]] == ["chew-1"]


# ------------------------------------------------------------------- import
def test_import_is_verified_read_only_and_idempotent(tmp_path, loop, video):
    folder = make_session(tmp_path / "phone", "s1", video=video)
    archive = zip_session(folder, tmp_path / "s1.zip")
    result = import_archive(archive, loop, expected_sha256=sha(archive))
    assert result.state == "imported"
    source = loop.source("s1")
    assert (source / "session.json").exists()
    assert not (source / "session.json").stat().st_mode & 0o222
    assert import_archive(archive, loop).state == "duplicate"
    rezipped = zip_session(folder, tmp_path / "s1-again.zip", extra={})
    assert import_archive(rezipped, loop).state == "duplicate"


def test_changed_recording_with_same_id_is_quarantined(tmp_path, loop, video):
    folder = make_session(tmp_path / "phone", "s1", video=video)
    import_archive(zip_session(folder, tmp_path / "a.zip"), loop)
    before = sha(loop.source("s1") / "s1.csv")
    (folder / "s1.csv").write_text("timestamp,dtNs,ax,ay,az,gx,gy,gz\n5000.0,0,0,0,0,0,0,0\n")
    manifest = json.loads((folder / "session.json").read_text())
    manifest["motion"]["sha256"] = sha(folder / "s1.csv")
    (folder / "session.json").write_text(json.dumps(manifest))
    result = import_archive(zip_session(folder, tmp_path / "b.zip"), loop)
    assert result.state == "conflict" and result.archive_path.exists()
    assert sha(loop.source("s1") / "s1.csv") == before


@pytest.mark.parametrize("problem", ["corrupt", "traversal", "symlink", "hash", "checksum", "two"])
def test_bad_archives_are_rejected_without_import(tmp_path, loop, video, problem):
    folder = make_session(tmp_path / "phone", "s2", video=video)
    archive = tmp_path / "bad.zip"
    expected = None
    if problem == "corrupt":
        data = zip_session(folder, archive).read_bytes()
        archive.write_bytes(data[: len(data) // 2])
    elif problem == "traversal":
        zip_session(folder, archive, extra={"../escape.txt": b"x"})
    elif problem == "symlink":
        zip_session(folder, archive)
        with zipfile.ZipFile(archive, "a") as handle:
            info = zipfile.ZipInfo("s2/link")
            info.external_attr = (0o120777 << 16)
            handle.writestr(info, "/etc/passwd")
    elif problem == "hash":
        (folder / "s2.csv").write_text("tampered\n")
        zip_session(folder, archive)
    elif problem == "checksum":
        zip_session(folder, archive)
        expected = "0" * 64
    elif problem == "two":
        other = make_session(tmp_path / "phone", "s3", video=video)
        with zipfile.ZipFile(archive, "w") as handle:
            for f in (folder, other):
                for path in f.rglob("*"):
                    if path.is_file():
                        handle.write(path, f"{f.name}/{path.relative_to(f)}")
    with pytest.raises(ArchiveError):
        import_archive(archive, loop, expected_sha256=expected)
    assert not loop.source("s2").exists()


# --------------------------------------------------------------- processing
def fake_proposer(calls=None):
    def propose(assisted, session_id, log_path):
        if calls is not None:
            calls.append(session_id)
        return {"summary": {"coverage": 0.5, "review_items": 2, "batch_items": 3, "label_conflicts": 0}}
    return propose


def _processor(loop, vision=None, proposer=None, **config):
    registry_for(loop)
    return Processor(loop, loop_config(**config), vision_runner=vision or fake_vision(),
                     proposer=proposer or fake_proposer())


def _enqueue(processor, tmp_path, session_id, video, **kwargs):
    folder = make_session(tmp_path / "phone", session_id, video=video, **kwargs)
    archive = zip_session(folder, tmp_path / f"{session_id}.zip")
    staged = processor.paths.uploads / session_id / "data.zip"
    staged.parent.mkdir(parents=True)
    shutil.copy2(archive, staged)
    return processor.enqueue_archive(staged, archive_sha256=sha(archive), device="test")


def _drain(processor, limit=50):
    for _ in range(limit):
        if not processor.run_once():
            return
    raise AssertionError("processor did not become idle")


def test_automatic_processing_creates_separate_assisted_and_blind_copies(tmp_path, loop, video):
    processor = _processor(loop, **{"roles.boosted_evaluation_fraction": 1.0})
    _enqueue(processor, tmp_path, "e1", video)
    _drain(processor)
    record = read_json(loop.session_record("e1"))
    assert record["role"] == "evaluation" and record["processing"] == "completed"
    assisted = loop.assisted / "sessions/raw/e1"
    blind = loop.blind / "sessions/raw/e1"
    assert (assisted / "guided_session.json").exists()
    assert not (blind / "guided_session.json").exists()
    assert not any((loop.blind / "derived/vision/e1").glob("candidates-v*.json"))
    assert len(load_candidate_artifact(next((loop.assisted / "derived/vision/e1").glob("candidates-v*.json"))).chews) == 2
    tasks = read_json(loop.review_tasks)["tasks"]
    assert [t["kind"] for t in tasks] == ["blind_review", "assisted_review"]
    # No coarse review yet: the blind task asks for it first; assisted stays locked.
    assert tasks[0]["blocked"] and "Coarse review" in tasks[0]["detail"]
    assert tasks[1]["blocked"] and "locked" in tasks[1]["title"]
    for name in ("timing.json", "guided-check.json", "intended-activity.json", "sync-assessment.json"):
        assert (loop.session_reports("e1") / name).exists()
    # Source remains byte-identical to the uploaded archive contents.
    assert sha(loop.source("e1") / "e1.mov") == sha(video)


def test_chewing_span_proposals_follow_vision(tmp_path, loop, video):
    calls = []
    processor = _processor(loop, proposer=fake_proposer(calls))
    _enqueue(processor, tmp_path, "a1", video)
    _drain(processor)
    record = read_json(loop.session_record("a1"))
    assert calls == ["a1"]
    assert record["coarse_proposals"] == "created"
    assert record["coarse_proposal_summary"]["review_items"] == 2
    assert SESSION_STEPS.index("coarse_proposals") == SESSION_STEPS.index("vision") + 2


def test_failed_proposals_are_visible_but_do_not_block_review(tmp_path, loop, video):
    def broken(assisted, session_id, log_path):
        raise RuntimeError("no motion")

    processor = _processor(loop, proposer=broken)
    _enqueue(processor, tmp_path, "f1", video)
    _drain(processor)
    record = read_json(loop.session_record("f1"))
    assert record["processing"] == "completed"
    assert record["coarse_proposals"] == "failed"
    assert "chewing-span proposals failed: no motion" in record["attention"]


def test_same_upload_never_creates_two_jobs(tmp_path, loop, video):
    processor = _processor(loop)
    folder = make_session(tmp_path / "phone", "d1", video=video)
    archive = zip_session(folder, tmp_path / "d1.zip")
    first = processor.enqueue_archive(archive, archive_sha256=sha(archive), device="t")
    second = processor.enqueue_archive(archive, archive_sha256=sha(archive), device="t")
    assert first["job_id"] == second["job_id"]


def test_role_is_persistent_and_repeats_inherit_parent_role(tmp_path, loop, video):
    processor = _processor(loop, **{"roles.boosted_evaluation_fraction": 1.0})
    _enqueue(processor, tmp_path, "p1", video)
    _drain(processor)
    processor.config["roles"]["boosted_evaluation_fraction"] = 0.0
    processor.config["roles"]["evaluation_fraction"] = 0.0
    _enqueue(processor, tmp_path / "r", "p1-repeat", video, repeat_of="p1")
    _enqueue(processor, tmp_path / "n", "n1", video)
    _drain(processor)
    assert read_json(loop.session_record("p1-repeat"))["role"] == "evaluation"
    assert read_json(loop.session_record("n1"))["role"] == "training"
    processor._step_assign_role({"session_id": "p1"})
    assert read_json(loop.session_record("p1"))["role"] == "evaluation"


def test_pause_stop_resume_and_crash_recovery(tmp_path, loop, video):
    vision = fake_vision()
    stops = {"count": 0}

    def interrupted_once(workspace, session_id, log):
        if stops["count"] == 0:
            stops["count"] += 1
            processor.stop()
            raise StepInterrupted("stopped")
        vision(workspace, session_id, log)

    processor = _processor(loop, interrupted_once)
    job = _enqueue(processor, tmp_path, "c1", video)
    processor.pause()
    assert processor.run_once() is False
    assert read_json(loop.status)["state"] == "paused"
    processor.resume()
    _drain(processor)
    stored = read_json(processor._job_path(job["job_id"]))
    assert stored["state"] == "queued" and "vision" not in stored["steps_done"]
    assert stored["interrupted_step"] == "vision"
    assert read_json(loop.status)["state"] == "stopped"
    # Simulate a crash mid-step, then restart: running jobs return to the queue.
    stored["state"] = "running"
    (processor._job_path(job["job_id"])).write_text(json.dumps(stored))
    processor = _processor(loop, interrupted_once)
    assert read_json(processor._job_path(job["job_id"]))["state"] == "queued"
    processor.resume()
    _drain(processor)
    stored = read_json(processor._job_path(job["job_id"]))
    assert stored["state"] == "completed" and stored["steps_done"] == list(SESSION_STEPS)
    assert len(list((loop.assisted / "derived/vision/c1").glob("candidates-v*.json"))) == 1


def test_vision_failure_needs_attention_and_can_be_retried(tmp_path, loop, video):
    attempts = {"n": 0}
    good = fake_vision()

    def flaky(workspace, session_id, log):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("mediapipe crashed")
        good(workspace, session_id, log)

    processor = _processor(loop, flaky)
    job = _enqueue(processor, tmp_path, "f1", video)
    _drain(processor)
    assert read_json(processor._job_path(job["job_id"]))["state"] == "needs_attention"
    assert read_json(loop.status)["attention"][0]["session_id"] == "f1"
    processor.retry(job["job_id"])
    _drain(processor)
    assert read_json(processor._job_path(job["job_id"]))["state"] == "completed"


def test_rejected_archive_is_reported(tmp_path, loop):
    processor = _processor(loop)
    bad = loop.uploads / "x" / "data.zip"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not a zip")
    job = processor.enqueue_archive(bad, archive_sha256=sha(bad), device="t")
    _drain(processor)
    assert read_json(processor._job_path(job["job_id"]))["state"] == "rejected"


# ------------------------------------------------------------ review hints
def test_priority_segments_target_disagreements():
    candidates = type("C", (), {"chews": [
        type("X", (), dict(id="a", start_closed_ts=11.0, end_closed_ts=11.4, confidence=0.9))(),
        type("X", (), dict(id="b", start_closed_ts=30.0, end_closed_ts=30.4, confidence=0.3))(),
    ]})()
    intended = {"intervals": [
        dict(activity_id="chew-1", kind="chew", intended_label="chewing", sync_marker=False, start_ts=0, end_ts=5),
        dict(activity_id="talk-1", kind="talk", intended_label="not_chewing", sync_marker=False, start_ts=10, end_ts=12),
        dict(activity_id="nod", kind="sync_marker", intended_label="not_chewing", sync_marker=True, start_ts=20, end_ts=21),
    ], "invalid_intervals": [dict(start_ts=40, end_ts=41, reasons=["face_missing"])]}
    reasons = [s["reason"] for s in priority_segments(candidates, intended)]
    assert any("missed chews" in r for r in reasons)
    assert any("talk" in r and "false positives" in r for r in reasons)
    assert any("low-confidence" in r for r in reasons)
    assert any("face_missing" in r for r in reasons)
    assert any("sync marker" in r for r in reasons)


# ------------------------------------------------------------ evaluation
def _blind_review(loop, session_id, chews, seconds=60.0):
    session = loop.blind / "sessions/raw" / session_id
    candidates = load_candidate_artifact(next((loop.blind / "derived/vision" / session_id).glob("candidates-v*.json")))
    draft = ReviewDraft(
        session_id=session_id, candidate_revision=candidates.candidate_revision,
        candidate_sha256=candidates.content_sha256, updated_at="2026-10-26", verified=True,
        full_video_pass_completed=True, reviewer="blind",
        added_chews=tuple(AddedChew(id=f"h{i}", start_closed_ts=a, open_peak_ts=b, end_closed_ts=c)
                          for i, (a, b, c) in enumerate(chews)),
        review_context=ReviewContext(mode="blind", active_seconds=seconds))
    (session / "annotations/review-draft.json").write_text(draft.model_dump_json())


def test_evaluation_uses_only_frozen_blind_reviews(tmp_path, loop, video):
    coarse = [dict(start_ts=0, end_ts=12, label="chewing", source="human_coarse")]
    processor = _processor(loop, **{"roles.boosted_evaluation_fraction": 1.0})
    _enqueue(processor, tmp_path, "ev1", video, coarse=coarse)
    _drain(processor)
    controller = LoopController(loop, loop_config())
    report = controller.analyze()
    assert report["evaluation"]["evaluation_sessions"] == 0  # draft absent: nothing evaluated
    _blind_review(loop, "ev1", [(1.0, 1.2, 1.4), (8.0, 8.2, 8.4)])
    report = controller.analyze()
    assert report["evaluation"]["evaluation_sessions"] == 0  # unfrozen draft is not truth
    assert processor.finalize_verified_drafts() == ["blind:ev1"]
    report = controller.analyze()
    evaluation = report["evaluation"]
    assert evaluation["evaluation_sessions"] == 1
    primary = evaluation["tolerances"]["0.250"]
    assert (primary["tp"], primary["fp"], primary["fn"]) == (1, 1, 1)
    assert set(evaluation["by_instructed_activity"]) >= {"chew", "rest"}
    assert report["acceptance"]["sufficient_evidence"] is False
    assert all(c["state"] == "insufficient_evidence" for c in report["acceptance"]["checks"])
    # Repeated analysis reuses immutable evaluation reports.
    count = len(list((loop.reports / "evaluation/ev1").glob("*.json")))
    controller.analyze()
    assert len(list((loop.reports / "evaluation/ev1").glob("*.json"))) == count == 3


def test_training_sessions_never_enter_evaluation(tmp_path, loop, video):
    processor = _processor(loop, **{"roles.boosted_evaluation_fraction": 0.0, "roles.evaluation_fraction": 0.0})
    _enqueue(processor, tmp_path, "t1", video)
    _drain(processor)
    assert not (loop.blind / "sessions/raw/t1").exists()
    report = LoopController(loop, loop_config()).analyze()
    assert report["evaluation"]["evaluation_sessions"] == 0
    assert report["dataset"]["role_overlap"] == 0


# --------------------------------------------------------- planning/catalog
def test_first_analysis_requests_evaluation_data_without_training(loop):
    registry_for(loop)
    trained = []
    controller = LoopController(loop, loop_config(), trainer=trained.append)
    report = controller.analyze()
    ids = {w["id"] for w in report["weaknesses"]}
    assert "evaluation-data" in ids
    assert not report["training"]["readiness"]["ready"] and not trained
    catalog = read_json(loop.catalog)
    baseline = next(e for e in catalog["experiments"] if e["definition"]["experiment_id"] == "baseline-90s")
    assert baseline["effective_priority"] == "high"
    assert any(r["weakness_id"] == "evaluation-data" for r in baseline["requests"])
    version = catalog["catalog_version"]
    controller.analyze()
    assert read_json(loop.catalog)["catalog_version"] == version
    assert report["stopping"]["single_wearer"] is True


def test_planner_generates_validated_experiment_once(loop):
    registry = registry_for(loop)
    registry.retire("walking-no-food", by="test", reason="test")
    controller = LoopController(loop, loop_config())
    weakness = {"id": "fp-walk", "category": "insufficient_training_data", "title": "Walking false positives",
                "evidence": {"false_events": 9}, "action": "collect", "priority": "high",
                "tags": ["walking"], "experiment_id": None}
    plan = controller.plan([weakness], {}, {"all_met": False})
    assert plan["created"] == ["gen-walking"]
    generated = registry.load("gen-walking", 1)
    assert generated.request.source == "planner" and generated.safety_notes
    assert not any(a.requires_face for a in generated.activities if a.kind == "walk")
    plan = controller.plan([weakness], {}, {"all_met": False})
    assert plan["created"] == [] and "gen-walking" in plan["requests"]
    # Completed experiments get a bounded new version rather than a duplicate.
    plan = controller.plan([weakness], {"gen-walking": 3}, {"all_met": False})
    assert plan["created"] == ["gen-walking@v2"]
    assert registry.load("gen-walking", 2).target_recordings == 6
    plan = controller.plan([weakness], {}, {"all_met": True})
    assert plan["requests"] == {} and plan["stop_reason"]


# ------------------------------------------------------------- training
def _dataset(**training):
    base = dict(verified_sessions=40, verified_positive_sessions=20, verified_negative_sessions=20,
                verified_hard_negative_sessions=20,
                sync_verified_fraction=1.0, eligible_sessions=[])
    base.update(training)
    return {"training": base, "role_overlap": 0, "evaluation_sessions": []}


def test_training_gates():
    config = loop_config()
    ready = readiness(_dataset(), {"evaluation_sessions": 6}, [], config)
    assert ready["ready"], ready["gates"]
    for dataset, evaluation, gate in [
        (_dataset(verified_positive_sessions=3), {"evaluation_sessions": 6}, "verified_positive_sessions"),
        (_dataset(sync_verified_fraction=0.5), {"evaluation_sessions": 6}, "synchronization"),
        (_dataset(verified_hard_negative_sessions=0), {"evaluation_sessions": 6}, "verified_negative_sessions"),
        (_dataset(), {"evaluation_sessions": 1}, "independent_evaluation"),
    ]:
        result = readiness(dataset, evaluation, [], config)
        assert not result["ready"]
        assert not next(g for g in result["gates"] if g["id"] == gate)["passed"]
    now = datetime.now(timezone.utc)
    recent = [dict(state="completed", requested_at=(now - timedelta(days=1)).isoformat(),
                   training_sessions=30, validation_f1=0.5) for _ in range(2)]
    result = readiness(_dataset(), {"evaluation_sessions": 6}, recent, config)
    assert {g["id"] for g in result["gates"] if not g["passed"]} >= {"rate_limit"}
    failed = [dict(h, state="failed") for h in recent]
    assert not next(g for g in readiness(_dataset(), {"evaluation_sessions": 6}, failed, config)["gates"]
                    if g["id"] == "rate_limit")["passed"]
    old = [dict(state="completed", requested_at=(now - timedelta(days=30)).isoformat(),
                training_sessions=10, validation_f1=0.70 + i * 0.001) for i in range(3)]
    result = readiness(_dataset(), {"evaluation_sessions": 6}, old, config)
    assert result["plateaued"] and not result["ready"]


def test_trainer_runs_only_when_ready_and_never_promotes(loop, monkeypatch):
    registry_for(loop)
    calls = []
    controller = LoopController(loop, loop_config(), trainer=calls.append)
    assert controller.maybe_request_training({"ready": False, "auto_experimental_training": True}, _dataset()) is None
    request = controller.maybe_request_training({"ready": True, "auto_experimental_training": True}, _dataset())
    assert calls and request["promotion"] == "never automatic"
    assert Path(request["experimental_workspace"]).name == "workspace"
    assert "training" in Path(request["experimental_workspace"]).parts


@pytest.mark.parametrize("returncode", [1, 0])
def test_training_failure_or_missing_result_is_reported(loop, returncode):
    import subprocess

    registry_for(loop)
    controller = LoopController(loop, loop_config(), trainer=lambda p: subprocess.CompletedProcess(
        ["training", str(p)], returncode))
    request = controller.maybe_request_training(
        {"ready": True, "auto_experimental_training": True}, _dataset())
    state = read_json(Path(request["run_dir"]) / "state.json")
    assert state["state"] == "failed"
    assert "training.log" in state["error"]
    assert controller.training_history()[0]["state"] == "failed"


def test_phone_status_explains_review_and_training_blockers(loop):
    from chewsense.loop.server import LoopService

    service = LoopService(loop, loop_config(), vision_runner=fake_vision(), trainer=lambda p: None, proposer=fake_proposer())
    summary = service.phone_status()["pipeline"]
    assert not summary["training_ready"]
    assert not summary["research_targets_met"]
    assert any("need 20" in reason for reason in summary["blockers"])
    # The legacy one-line message is the shared project status's first step.
    project = summary["project"]
    assert project["phase"] == "collect"
    assert summary["next_action"].startswith(project["headline"] + ": " + project["steps"][0]["title"])
    assert summary["next_experiment_id"] == project["steps"][0]["experiment_id"]
    # Phones get the stored copy: the timestamp is when it last changed, as Review shows.
    assert service.phone_status()["pipeline"]["project"]["updated_at"] == project["updated_at"]
    assert json.loads((loop.root / "project_status.json").read_text()) == project
    write_json_atomic(loop.review_tasks, {"tasks": [{"workspace": "/private/path", "kind": "blind_review"}]})
    summary = service.phone_status()["pipeline"]
    assert summary["review_tasks"] == 1
    assert "Review" in summary["next_action"]
    assert summary["project"]["phase"] == "label"
    assert "/private/path" not in json.dumps(summary)
    write_json_atomic(loop.status, {"attention": [{"error": "failure"}]})
    assert "errors" in service.phone_status()["pipeline"]["next_action"]


def test_motion_test_failures_request_focused_data_and_prevent_early_stop(loop, monkeypatch):
    registry_for(loop)
    controller = LoopController(loop, loop_config())
    initial = controller.analyze()
    history = [dict(state="completed", requested_at="2026-01-01T00:00:00+00:00",
                    training_sessions=0, validation_f1=0.9,
                    test_metrics={"chew_f1_at_250ms": 0.4,
                                  "per_activity": {"talking": {"hard_negative_fpr": 0.6}},
                                  "per_food_category": {"crunchy": {"chew_f1_at_250ms": 0.3,
                                      "chew_true_positives": 20, "chew_false_negatives": 40}}},
                    promotion_assessment={"passed": False})]
    monkeypatch.setattr(controller, "training_history", lambda: history)
    monkeypatch.setattr(controller, "acceptance", lambda *_: dict(initial["acceptance"], all_met=True))
    report = controller.analyze()
    assert report["stopping"]["vision_targets_met"]
    assert not report["stopping"]["acceptance_met"]
    assert not report["stopping"]["motion_assessment_passed"]
    assert {w["id"] for w in report["weaknesses"]} >= {"motion-fp-talking", "motion-chew-events"}
    assert any(r["weakness_id"] == "motion-fp-talking" for r in report["plan"]["requests"]["talking"])
    assert any(r["weakness_id"] == "motion-food-crunchy" for r in report["plan"]["requests"]["crunchy-food"])
    assert report["plan"]["stop_reason"] is None


# -------------------------------------------------------------- LM Studio
def test_lmstudio_output_is_validated_against_evidence():
    evidence = {"weaknesses": [{"id": "fp-walk", "category": "model_limitation", "title": "Walking",
                                "evidence": {"false_events": 9, "rate": 0.25}}], "acceptance": []}
    good = {"overview": "Walking caused 9 false events.",
            "weaknesses": [{"id": "fp-walk", "explanation": "About 25% of walking minutes.",
                            "suggested_wording": "Walk normally."}]}
    assert validate_summary(good, evidence)[0]
    invented = dict(good, overview="Accuracy is 97%.")
    assert not validate_summary(invented, evidence)[0]
    unknown = dict(good, weaknesses=[dict(good["weaknesses"][0], id="made-up")])
    assert not validate_summary(unknown, evidence)[0]
    claim = dict(good, overview="The detector is production-ready.")
    assert not validate_summary(claim, evidence)[0]


def test_lmstudio_unavailable_is_harmless():
    result = LMStudio("http://127.0.0.1:9/v1", timeout_s=0.5).summarize({"weaknesses": []})
    assert result["used"] is False


# --------------------------------------------------------- sync assessment
@pytest.mark.parametrize("offset,status", [(0.02, "consistent"), (0.25, "exceeds_budget"), (None, "inconclusive")])
@pytest.mark.parametrize("axis", ["head_pitch", "head_roll"])
def test_sync_marker_assessment(tmp_path, offset, status, axis):
    t_video = np.arange(0, 30, 1 / 30)
    nods = [3.0, 15.0, 27.0]
    pitch = np.zeros_like(t_video)
    gyro_t = np.arange(0, 30, 0.02)
    gyro = np.full_like(gyro_t, 0.01) + np.random.default_rng(1).normal(0, 0.002, len(gyro_t))
    if offset is not None:
        for nod in nods:
            # Visual head pitch steps at the nod (peak angular speed at `nod`);
            # the AirPods gyro peak precedes it by `offset` seconds.
            pitch += np.radians(20) / (1 + np.exp(-(t_video - nod) / 0.05))
            gyro += 2 * np.exp(-((gyro_t - (nod - offset)) / 0.08) ** 2)
    frames = tmp_path / "frames.csv"
    frame_data = dict(ts=t_video, head_pitch=0., head_yaw=0., head_roll=0., face_detected=True)
    frame_data[axis] = pitch
    pd.DataFrame(frame_data).to_csv(frames, index=False)
    motion = tmp_path / "motion.csv"
    pd.DataFrame(dict(timestamp=gyro_t + ORIGIN, gx=gyro, gy=0.0, gz=0.0)).to_csv(motion, index=False)
    intended = {"intervals": [dict(activity_id=f"n{i}", sync_marker=True, start_ts=n - 1, end_ts=n + 1)
                              for i, n in enumerate(nods)]}
    result = assess_markers(intended, frames, motion, ORIGIN)
    assert result["status"] == status
    assert result["sets_sync_verified"] is False and result["mapping_changed"] is False


# ------------------------------------------------------------- pairing
def test_pairing_binds_code_to_certificate_and_limits_attempts(tmp_path):
    registry = DeviceRegistry(tmp_path / "devices.json", "f" * 64, attempts=2)
    with pytest.raises(PermissionError, match="not open"):
        registry.pair("phone", "iPhone", "x")
    code = registry.open_window()["code"]
    relay = pairing_proof(code, "phone", "0" * 64)  # proof over a relay's certificate
    with pytest.raises(PermissionError):
        registry.pair("phone", "iPhone", relay)
    result = registry.pair("phone", "iPhone", pairing_proof(code, "phone", "f" * 64))
    assert result["server_proof"] == server_proof(code, "phone", "f" * 64, result["device_token"])
    assert registry.authenticate(result["device_token"])[0] == "phone"
    assert result["device_token"] not in (tmp_path / "devices.json").read_text()
    assert registry.authenticate("wrong") is None
    registry.revoke("phone")
    assert registry.authenticate(result["device_token"]) is None
    code = registry.open_window()["code"]
    for _ in range(2):
        with pytest.raises(PermissionError):
            registry.pair("phone2", "iPhone", "bad")
    with pytest.raises(PermissionError, match="not open"):
        registry.pair("phone2", "iPhone", pairing_proof(code, "phone2", "f" * 64))


# ------------------------------------------------------ end-to-end service
class _Client:
    def __init__(self, port, fingerprint=None, token=None):
        self.port, self.token = port, token
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # Pinned by fingerprint below, as Collect does.
        self.context, self.expected = context, fingerprint
        self.seen = None

    def __call__(self, method, path, body=None, raw=None):
        connection = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.context, timeout=10)
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        connection.request(method, path, body=data, headers=headers)
        self.seen = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if self.expected:
            assert self.seen == self.expected, "certificate pin mismatch"
        response = connection.getresponse()
        payload = json.loads(response.read() or b"{}")
        connection.close()
        return response.status, payload


def test_end_to_end_pair_upload_resume_and_process(tmp_path, video):
    from chewsense.loop.server import LoopService, serve

    paths = LoopPaths(tmp_path / "loop")
    config = loop_config(**{"service.max_chunk_bytes": 4096, "roles.boosted_evaluation_fraction": 1.0})
    service = LoopService(paths, config, vision_runner=fake_vision(), trainer=lambda _p: None, proposer=fake_proposer())
    device, admin, stop = serve(service, host="127.0.0.1", device_port=0, admin_port=0, advertise=False)
    try:
        client = _Client(device.server_address[1])
        assert client("GET", "/v1/hello")[1]["pairing_open"] is False
        assert client("GET", "/v1/catalog")[0] == 401
        code = service.devices.open_window()["code"]
        client("GET", "/v1/hello")
        fingerprint = client.seen
        assert fingerprint == service.identity.fingerprint
        status, paired = client("POST", "/v1/pair", {"device_id": "phone-1", "device_name": "Test",
                                                    "proof": pairing_proof(code, "phone-1", fingerprint)})
        assert status == 200
        assert paired["server_proof"] == server_proof(code, "phone-1", fingerprint, paired["device_token"])
        client = _Client(device.server_address[1], fingerprint, paired["device_token"])
        catalog = client("GET", "/v1/catalog")[1]
        assert catalog["schema_version"] == "guided-catalog/1"
        assert len(catalog["experiments"]) == len(list(ex.SEED_DIR.glob("*.json")))
        assert client("GET", f"/v1/catalog?since={catalog['catalog_version']}")[1]["unchanged"]

        folder = make_session(tmp_path / "phone", "up1", video=video)
        data = zip_session(folder, tmp_path / "up1.zip").read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        status, upload = client("POST", "/v1/uploads", {"session_id": "up1", "archive_sha256": digest,
                                                        "size_bytes": len(data)})
        assert status == 200 and upload["offset"] == 0
        upload_id = upload["upload_id"]
        # Send half, then "lose the connection" and resume from the reported offset.
        half = len(data) // 2
        offset = 0
        while offset < half:
            chunk = data[offset:min(offset + 4096, half)]
            offset = client("PUT", f"/v1/uploads/{upload_id}?offset={offset}", raw=chunk)[1]["offset"]
        resumed = client("POST", "/v1/uploads", {"session_id": "up1", "archive_sha256": digest,
                                                 "size_bytes": len(data)})[1]
        assert resumed["upload_id"] == upload_id and resumed["offset"] == half
        status, conflict = client("PUT", f"/v1/uploads/{upload_id}?offset=0", raw=data[:10])
        assert status == 409 and conflict["offset"] == half
        assert client("POST", f"/v1/uploads/{upload_id}/complete")[1]["state"] == "incomplete"
        offset = half
        while offset < len(data):
            chunk = data[offset:offset + 4096]
            offset = client("PUT", f"/v1/uploads/{upload_id}?offset={offset}", raw=chunk)[1]["offset"]
        receipt = client("POST", f"/v1/uploads/{upload_id}/complete")[1]
        assert receipt["state"] == "received" and receipt["archive_sha256"] == digest
        again = client("POST", "/v1/uploads", {"session_id": "up1", "archive_sha256": digest,
                                               "size_bytes": len(data)})[1]
        assert again["state"] == "received"
        for _ in range(200):
            state = client("GET", "/v1/sessions/up1/status")[1]["state"]
            if state == "processed":
                break
            stop.wait(0.05)
        assert state == "processed"
        assert len(service.processor.jobs()) == 1
        token = service.identity.admin_token
        connection = http.client.HTTPConnection("127.0.0.1", admin.server_address[1], timeout=10)
        connection.request("GET", "/admin/review-tasks", headers={"Authorization": f"Bearer {token}"})
        tasks = json.loads(connection.getresponse().read())["tasks"]
        assert tasks[0]["kind"] == "blind_review"
        connection.request("GET", "/admin/status")
        assert connection.getresponse().status == 401
    finally:
        stop.set()
        device.shutdown()
        admin.shutdown()


def test_checksum_mismatch_restarts_upload(tmp_path):
    from chewsense.loop.server import LoopService

    service = LoopService(LoopPaths(tmp_path / "loop"), loop_config(), vision_runner=fake_vision(), proposer=fake_proposer(),
                          trainer=lambda _p: None)
    upload = service.create_upload("phone", {"session_id": "x", "archive_sha256": "a" * 64, "size_bytes": 4})
    service.append_chunk(upload["upload_id"], 0, b"abcd")
    assert service.complete_upload(upload["upload_id"]) == {"state": "checksum_mismatch", "offset": 0}
    assert service.processor.jobs() == []
    with pytest.raises(ValueError):
        service.append_chunk(upload["upload_id"], 0, b"abcdef")


SWIFT_SIDECAR = REPO / "build/contract/guided_session.swift.json"


@pytest.mark.skipif(not SWIFT_SIDECAR.exists(), reason="run the Collect GuidedCollectionTests first")
def test_swift_generated_sidecar_satisfies_mac_contract(loop):
    """Cross-language check: the sidecar Collect writes validates and verifies here."""
    registry = registry_for(loop)
    guided = GuidedSession.model_validate_json(SWIFT_SIDECAR.read_text())
    assert ex.definition_sha256(guided.definition) == guided.definition_sha256
    assert registry.find(guided.experiment_id, guided.experiment_version) is not None
    assert ex.definition_sha256(registry.load(guided.experiment_id, guided.experiment_version)) == guided.definition_sha256
    track = intended_activity_track(guided)
    assert len(track["intervals"]) == 9 and track["verified"] is False
    assert all(i["end_ts"] > i["start_ts"] for i in track["intervals"])


def test_lmstudio_summary_is_cached_against_exact_evidence(loop):
    registry_for(loop)

    class FakeLM:
        calls = 0

        def summarize(self, evidence):
            FakeLM.calls += 1
            return {"used": True, "model": "fake", "advisory_only": True,
                    "summary": {"overview": "More recordings are needed.", "weaknesses": []}}

    config = loop_config(**{"lmstudio.enabled": True})
    controller = LoopController(loop, config, lm=FakeLM())
    report = controller.analyze()
    assert report["lmstudio"] is None  # never computed inline
    controller.summarize(report)
    assert controller.analyze()["lmstudio"]["model"] == "fake"
    controller.summarize(controller.analyze())
    assert FakeLM.calls == 1
    # Metrics and plans never depend on the language model.
    assert report["weaknesses"] == controller.analyze()["weaknesses"]
