"""Exercise the actual training handoff on isolated, synthetic reviewed sessions.

Fixture reviews are test inputs, never human evidence or accuracy claims.
"""
import json

from chewsense.canonical import ChewReviewDecision, ReviewContext, ReviewDraft, load_candidate_artifact
from chewsense.loop.training import _run
from chewsense.session_bundle import finalize_session
from pipeline.config import load_config

from .loop_fixtures import fake_vision, make_session, make_video


def test_experimental_training_materializes_trains_tests_and_registers(tmp_path, monkeypatch):
    workspace = tmp_path / "experimental"
    monkeypatch.setenv("CHEWSENSE_WORKSPACE", str(workspace))
    movie = make_video(tmp_path / "fixture.mov", 4)
    sessions = []
    for positive in (True, False):
        for index in range(3):
            sid = f"{'positive' if positive else 'negative'}-{index}"
            folder = make_session(
                workspace / "sessions/raw", sid, video_seconds=4, guided=True,
                experiment_id="baseline-90s" if positive else "talking", video=movie,
                coarse=[dict(start_ts=0, end_ts=4, label="chewing" if positive else "not_chewing",
                             source="human_coarse")])
            manifest = json.loads((folder / "session.json").read_text())
            manifest["activity"] = "eating" if positive else "unknown"
            (folder / "session.json").write_text(json.dumps(manifest))
            fake_vision([(1, 1.2, 1.4)] if positive else [])(workspace, sid, tmp_path / "vision.log")
            derived = workspace / "derived/vision" / sid
            candidates = load_candidate_artifact(next(derived.glob("candidates-v*.json")))
            draft = ReviewDraft(
                session_id=sid, candidate_revision=candidates.candidate_revision,
                candidate_sha256=candidates.content_sha256, updated_at="2026-11-02",
                verified=True, full_video_pass_completed=True, reviewer="synthetic-test-fixture",
                chew_decisions=tuple(ChewReviewDecision(candidate_id=c.id, disposition="accepted")
                                     for c in candidates.chews),
                review_context=ReviewContext(mode="assisted", sync_verified=True))
            (folder / "annotations/review-draft.json").write_text(draft.model_dump_json())
            finalize_session(folder, derived)
            sessions.append(str(folder))

    config = load_config()
    config.training.epochs = 1
    config.training.batch_size = 64
    config.temporal_model.channels = [4]
    config.temporal_model.kernel_size = 3
    config.temporal_model.dropout = 0
    monkeypatch.setattr("pipeline.config.load_config", lambda: config)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    request = run_dir / "request.json"
    request.write_text(json.dumps({"run_dir": str(run_dir), "sessions": sessions,
                                   "experimental_workspace": str(workspace)}))
    assert _run(request) == 0
    result = json.loads((run_dir / "result.json").read_text())
    # Activity-specific negative strata have no AUC; they must serialize as
    # null instead of poisoning the strict JSON status/catalog publication.
    json.dumps(result, allow_nan=False)
    assert result["promoted"] is False
    assert "chew_f1_at_250ms" in result["metrics"]
    assert "chew_f1_at_250ms" in result["test_metrics"]
    assert not config.wandb.enabled
    entries = list((workspace / "models/registry").iterdir())
    assert len(entries) == 1
    metrics = json.loads((entries[0] / "metrics.json").read_text())
    assert set(metrics) == {"validation_locked", "test_locked"}
    assert set(metrics["test_locked"]["per_activity"]) == {"eating", "talking"}
    assert set(metrics["test_locked"]["per_food_category"]) == {"mixed"}
    assert not (workspace / "models/exports/ChewNet.mlpackage").exists()
    assert (workspace / "locked_splits.json").read_bytes() == (run_dir / "locked_splits.json").read_bytes()
