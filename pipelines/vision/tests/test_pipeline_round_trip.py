"""One complete learning cycle on isolated fixtures with known event truth.

Vision proposals are a fixture here; the live physical recordings exercise real
Vision separately. Training, finalization, splits, evaluation, registry and
planning below execute their actual implementations. No wearer labels are made.
"""
import hashlib
import json
import subprocess
import threading
from concurrent.futures import Future
from pathlib import Path

from chewsense.canonical import AddedChew, ChewReviewDecision, ReviewContext, ReviewDraft, load_candidate_artifact
from chewsense.loop.controller import LoopController
from chewsense.loop.server import LoopService
from chewsense.loop.store import LoopPaths, read_json
from chewsense.loop.training import _run, readiness
from pipeline.config import load_config

from .loop_fixtures import fake_vision, loop_config, make_session, make_video, zip_session


def _upload(service, folder, archive):
    data = zip_session(folder, archive).read_bytes()
    created = service.create_upload("fixture-device", dict(
        session_id=folder.name, archive_sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data)))
    service.append_chunk(created["upload_id"], 0, data)
    return service.complete_upload(created["upload_id"])


def _process(service):
    for _ in range(50):
        if not service.processor.run_once():
            return
    raise AssertionError("fixture processing did not become idle")


def _review(workspace, sid, *, positive, blind=False):
    session = workspace / "sessions/raw" / sid
    candidate = load_candidate_artifact(next((workspace / "derived/vision" / sid).glob("candidates-v*.json")))
    draft = ReviewDraft(
        session_id=sid, candidate_revision=candidate.candidate_revision,
        candidate_sha256=candidate.content_sha256, updated_at="2026-11-02",
        verified=True, full_video_pass_completed=True, reviewer="known-synthetic-fixture",
        chew_decisions=tuple(ChewReviewDecision(candidate_id=c.id, disposition="accepted") for c in candidate.chews),
        added_chews=(AddedChew(id="known-event", start_closed_ts=1, open_peak_ts=1.2, end_closed_ts=1.4),)
        if blind and positive else (),
        review_context=ReviewContext(mode="blind" if blind else "assisted", sync_verified=True, active_seconds=10))
    (session / "annotations/review-draft.json").write_text(draft.model_dump_json())


def test_upload_review_train_test_request_and_collect_again(tmp_path, monkeypatch):
    paths = LoopPaths(tmp_path / "loop")
    config = loop_config(**{
        "roles.boosted_evaluation_fraction": 0, "roles.evaluation_fraction": 0,
        "training.min_verified_training_sessions": 3, "training.min_verified_negative_sessions": 3,
        "training.min_new_sessions_between_runs": 1,
        "evaluation.min_evaluation_sessions": 1, "evaluation.min_gold_events": 1,
    })
    model_config = load_config()
    model_config.training.epochs = 1
    model_config.training.batch_size = 128
    model_config.temporal_model.channels = [4]
    model_config.temporal_model.dropout = 0
    monkeypatch.setattr("pipeline.config.load_config", lambda: model_config)

    def train(request_path):
        request = read_json(request_path)
        with monkeypatch.context() as scoped:
            scoped.setenv("CHEWSENSE_WORKSPACE", request["experimental_workspace"])
            return subprocess.CompletedProcess(["actual-fixture-training"], _run(request_path))

    def vision(workspace, sid, log):
        positive = "positive" in sid or "gold" in sid or "correction" in sid
        fake_vision([(1, 1.2, 1.4)] if positive else [])(workspace, sid, log)

    service = LoopService(paths, config, vision_runner=vision, trainer=train)
    video = make_video(tmp_path / "synthetic.mov", 12)
    for positive in (True, False):
        for index in range(3):
            sid = f"{'positive' if positive else 'negative'}-{index}"
            folder = make_session(tmp_path / "device", sid, video=video,
                experiment_id="baseline-90s" if positive else "talking",
                coarse=[dict(start_ts=0, end_ts=12, label="chewing" if positive else "not_chewing", source="human_coarse")])
            _upload(service, folder, tmp_path / f"{sid}.zip")
            _process(service)
            _review(paths.assisted, sid, positive=positive)
    service.periodic()
    assert not read_json(paths.reports / "loop/latest.json")["training"]["readiness"]["ready"]
    assert not list((paths.root / "training").glob("run-*/result.json"))

    config["roles"]["boosted_evaluation_fraction"] = 1
    gold = make_session(tmp_path / "device", "gold-evaluation", video=video,
        coarse=[dict(start_ts=0, end_ts=12, label="chewing", source="human_coarse")])
    _upload(service, gold, tmp_path / "gold.zip")
    _process(service)
    assert read_json(paths.session_record("gold-evaluation"))["role"] == "evaluation"
    _review(paths.blind, "gold-evaluation", positive=True, blind=True)
    service.periodic()

    report = read_json(paths.reports / "loop/latest.json")
    assert report["evaluation"]["evaluation_sessions"] == 1
    assert report["training"]["history"][0]["state"] == "completed"
    assert report["training"]["history"][0]["test_metrics"]["split"] == "test_locked"
    assert any(w["id"] == "motion-chew-events" for w in report["weaknesses"])
    assert any(r["weakness_id"] == "motion-chew-events" for r in report["plan"]["requests"]["baseline-90s"])
    request = report["training"]["requested"]
    assert not any(Path(p).name == "gold-evaluation" for p in request["sessions"])
    assert request["evaluation_sessions_excluded"] == ["gold-evaluation"]
    result = read_json(Path(request["run_dir"]) / "result.json")
    assert result["promoted"] is False
    assert result["promotion_assessment"]["passed"] is False
    assert not (Path(request["experimental_workspace"]) / "models/exports/ChewNet.mlpackage").exists()
    assert service.phone_status()["pipeline"]["stages"]
    json.dumps(service.phone_status(), allow_nan=False)

    locked_before = read_json(Path(request["experimental_workspace"]) / "locked_splits.json")
    config["roles"]["boosted_evaluation_fraction"] = 0
    correction = make_session(tmp_path / "device", "correction-next", video=video,
        coarse=[dict(start_ts=0, end_ts=12, label="chewing", source="human_coarse")])
    _upload(service, correction, tmp_path / "correction.zip")
    _process(service)
    service.periodic()
    assert read_json(paths.session_record("correction-next"))["processing"] == "completed"
    assert len(list((paths.root / "training").glob("run-*/result.json"))) == 1
    _review(paths.assisted, "correction-next", positive=True)
    service.periodic()
    assert len(list((paths.root / "training").glob("run-*/result.json"))) == 2
    updated = read_json(paths.reports / "loop/latest.json")
    assert len(updated["training"]["history"]) == 2
    assert all(h["state"] == "completed" for h in updated["training"]["history"])
    locked_after = read_json(Path(request["experimental_workspace"]) / "locked_splits.json")
    for split in ("train", "validation_locked", "test_locked"):
        assert set(locked_before["splits"][split]).issubset(locked_after["splits"][split])
    assert updated["evaluation"]["evaluation_sessions"] == 1


def test_background_training_does_not_block_service_and_requests_reanalysis(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def launch(_request, **_kwargs):
        started.set()
        assert release.wait(3)
        return subprocess.CompletedProcess(["fixture"], 0)

    monkeypatch.setattr("chewsense.loop.server.launch", launch)
    service = LoopService(LoopPaths(tmp_path / "loop"), loop_config(), vision_runner=fake_vision())
    try:
        future = service.schedule_training(tmp_path / "request.json")
        assert started.wait(2)
        assert not future.done()
        assert service.phone_status()["service"] == "chewsense-loop"
        assert service.processor.run_once() is False
    finally:
        release.set()
    assert future.result(timeout=2).returncode == 0
    assert service._analysis_requested.wait(2)


def test_pending_training_blocks_another_run_until_the_future_finishes(tmp_path):
    paths = LoopPaths(tmp_path / "loop").ensure()
    future = Future()
    controller = LoopController(paths, loop_config(), trainer=lambda _: future)
    dataset = dict(training=dict(verified_sessions=40, verified_positive_sessions=20,
        verified_negative_sessions=20, verified_hard_negative_sessions=20, sync_verified_fraction=1,
        eligible_sessions=[]), role_overlap=0, evaluation_sessions=[])
    request = controller.maybe_request_training(dict(ready=True, auto_experimental_training=True), dataset)
    history = controller.training_history()
    assert history[0]["state"] == "running"
    gates = readiness(dataset, {"evaluation_sessions": 6}, history, loop_config())
    assert not next(g for g in gates["gates"] if g["id"] == "no_active_training")["passed"]
    future.set_exception(RuntimeError("fixture training failure"))
    assert read_json(Path(request["run_dir"]) / "state.json")["state"] == "failed"


def test_completed_timing_check_requests_inspection_instead_of_more_nods(tmp_path):
    from chewsense.loop.requirements import build_requirements
    service = LoopService(LoopPaths(tmp_path / "loop"), loop_config(), vision_runner=fake_vision())
    report = service.analyze()
    record = dict(session_id="timing", guided=True, experiment_id="timing-check",
                  role="diagnostic", device_validation_complete=True, definition_verified=True)
    sync = dict(consistent=0, inconclusive=3, exceeds_budget=0, human_confirmed=0,
                worst_median_s=None)
    requirements = build_requirements([record], report["dataset"], report["evaluation"], sync,
        report["training"]["readiness"], [], [], service.config)
    assert requirements["actions"][0]["id"] == "inspect-timing"
    assert not any(a.get("experiment_id") == "timing-check" for a in requirements["actions"])
    weaknesses = service.controller.weaknesses([record], report["dataset"], report["evaluation"],
        sync, report["training"]["readiness"])
    assert any(w["id"] == "sync-review" for w in weaknesses)
    assert not any(w["action"] == "collect_sync_check" for w in weaknesses)


def test_incomplete_and_diagnostic_recordings_cannot_enter_model_data(tmp_path, monkeypatch):
    controller = LoopController(LoopPaths(tmp_path / "loop").ensure(), loop_config())
    def forbidden_pointer(_path):
        raise AssertionError("Excluded recording must not reach annotation loading")
    monkeypatch.setattr("chewsense.loop.controller._pointer", forbidden_pointer)
    records = [dict(session_id="short", role="training", guided=True,
                    device_validation_complete=False, definition_verified=True),
               dict(session_id="timing", role="diagnostic", guided=True,
                    device_validation_complete=True, definition_verified=True),
               dict(session_id="unknown-definition", role="evaluation", guided=True,
                    device_validation_complete=True, definition_verified=False)]
    for r in records:
        (controller.paths.assisted / "sessions/raw" / r["session_id"]).mkdir(parents=True)
        (controller.paths.blind / "sessions/raw" / r["session_id"]).mkdir(parents=True)
    dataset = controller.dataset_summary(records)
    assert dataset["training"]["verified_sessions"] == 0
    assert dataset["training"]["eligible_sessions"] == []
    assert dataset["evaluation_sessions"] == []
    monkeypatch.setattr(controller, "_evaluate_session", forbidden_pointer)
    assert controller.evaluation_summary(records)["evaluation_sessions"] == 0


def test_nod_with_different_dominant_phases_is_ambiguous(tmp_path):
    import numpy as np
    import pandas as pd
    from chewsense.loop.sync_assess import assess_markers
    t = np.arange(0, 30, 1 / 30)
    speed = np.zeros_like(t)
    gyro = np.full_like(t, 0.01)
    nods = [3, 15, 27]
    for nod in nods:
        down = np.exp(-((t - (nod - .4)) / .08) ** 2)
        up = np.exp(-((t - (nod + .4)) / .08) ** 2)
        speed += down - .8 * up
        gyro += .8 * down + up
    frames, motion = tmp_path / "frames.csv", tmp_path / "motion.csv"
    pd.DataFrame(dict(ts=t, head_pitch=np.cumsum(speed) / 30,
                      head_yaw=0., head_roll=0., face_detected=True)).to_csv(frames, index=False)
    pd.DataFrame(dict(timestamp=t + 5000, gx=gyro, gy=0., gz=0.)).to_csv(motion, index=False)
    intended = {"intervals": [dict(activity_id=str(n), sync_marker=True, start_ts=n-1, end_ts=n+1) for n in nods]}
    report = assess_markers(intended, frames, motion, 5000)
    assert report["status"] == "inconclusive"
    assert all(m.get("ambiguous") and not m["usable"] for m in report["markers"])
    assert report["sets_sync_verified"] is False
    assert report["mapping_changed"] is False


def test_timing_only_confirmation_refreshes_phone_without_event_finalization(tmp_path):
    from chewsense.loop.guided import write_json_atomic
    from chewsense.loop.requirements import timing_check_definition
    paths = LoopPaths(tmp_path / "loop").ensure()
    service = LoopService(paths, loop_config(), vision_runner=fake_vision())
    service.controller._publishable(timing_check_definition())
    sid = "timing-only"
    record = dict(session_id=sid, role="diagnostic", guided=True, experiment_id="timing-check",
                  definition_verified=True, device_validation_complete=True, processing="completed")
    write_json_atomic(paths.session_record(sid), record)
    write_json_atomic(paths.session_reports(sid) / "sync-assessment.json",
                      dict(status="consistent", median_absolute_residual_s=.02))
    annotations = paths.assisted / "sessions/raw" / sid / "annotations"
    annotations.mkdir(parents=True)
    service.periodic()
    assert read_json(paths.reports / "loop/latest.json")["synchronization"]["human_confirmed"] == 0
    draft = ReviewDraft(session_id=sid, candidate_revision=1, candidate_sha256="a" * 64,
                        updated_at="2026-11-02", review_context=ReviewContext(sync_verified=True))
    (annotations / "review-draft.json").write_text(draft.model_dump_json())
    service.periodic()
    report = read_json(paths.reports / "loop/latest.json")
    assert report["synchronization"]["human_confirmed"] == 1
    assert report["dataset"]["training"]["verified_sessions"] == 0
    assert not (annotations / "canonical_events.json").exists()
    assert not any(t["kind"] == "synchronization_review" for t in read_json(paths.review_tasks)["tasks"])
    assert next(s for s in service.phone_status()["pipeline"]["stages"] if s["id"] == "timing")["state"] == "complete"
    assert not any(e["definition"]["experiment_id"] == "timing-check"
                   for e in read_json(paths.catalog)["experiments"])
