"""The shared "where we are" message shown by Collect, Review and Live (synthetic JSON)."""
from __future__ import annotations

import json

from chewsense.loop.project_status import (
    build_project_status,
    pending_span_reviews,
    project_status_path,
    recording_name,
    write_project_status,
)
from chewsense.loop.store import LoopPaths

GOALS = [
    dict(id="positive", title="Verified chewing training recordings", current=0, target=20),
    dict(id="negative", title="Verified hard negatives", current=0, target=20),
    dict(id="evaluation", title="Independent evaluation recordings", current=1, target=6),
    dict(id="gold", title="Independent chew events", current=45, target=300),
]


def _catalog():
    def entry(eid, title, priority, required, done, target):
        return dict(definition=dict(experiment_id=eid, title=title, priority=priority, required=required),
                    effective_priority=priority, progress=dict(completed_valid=done, remaining=target - done, target=target))
    return dict(experiments=[
        entry("talking", "Talking", "medium", False, 0, 3),
        entry("natural-meal", "Natural Meal", "high", False, 0, 3),
        entry("baseline-90s", "90-Second Baseline", "high", True, 1, 3),
        entry("done", "Already Done", "high", True, 3, 3),
    ])


def _report(ready=False, gold=45):
    return {
        "training": {"readiness": {"ready": ready}},
        "evaluation": {"tolerances": {"0.250": {"gold_events": gold, "tp": 28, "f1": 0.727}}},
        "acceptance": {"checks": [{"id": "chew_event_f1_at_250ms", "target": 0.85}]},
        "eating_detector": {"run_id": "eating-20261104-015911", "development": {"f1": 0.87}},
    }


def _build(**overrides):
    args = dict(service_status={}, tasks=[], requirements={"goals": GOALS, "actions": [], "incomplete_sessions": 0},
                report=_report(), catalog=_catalog(), records=[], span_reviews=[],
                live={"model_id": "eating-hgb-20261104-015911-7a28dd15"}, now="2026-11-08T00:00:00Z")
    args.update(overrides)
    return build_project_status(**args)


def test_collect_phase_follows_collect_catalog_order_and_answers_the_three_questions():
    status = _build()
    assert status["phase"] == "collect" and status["headline"] == "Collect more recordings"
    titles = [s["title"] for s in status["steps"]]
    # Required first, then priority — the order Collect itself lists experiments in.
    assert titles[:2] == ["Record 90-Second Baseline", "Record Natural Meal"]
    assert status["steps"][0]["experiment_id"] == "baseline-90s"
    assert status["steps"][-1]["app"] == "live" and status["steps"][-1]["optional"]
    assert "chewing recordings 0/20" in status["summary"]
    checks = {c["id"]: c for c in status["checks"]}
    assert checks["test_model"]["state"] == "partial" and "0.87" in checks["test_model"]["answer"]
    assert "28" in checks["vision"]["answer"] and "0.85" in checks["vision"]["answer"]
    assert checks["more_data"]["state"] == "blocked" and "hand-marked chews 45/300" in checks["more_data"]["answer"]
    assert [s["id"] for s in status["stages"] if s["current"]] == ["collect"]


def test_label_tasks_come_before_collection_and_span_reviews_wait_for_blind_review():
    blind = "Not-eating-20261026-174428-C27C9380"
    status = _build(
        tasks=[dict(kind="blind_review", session_id=blind, blocked=False, workspace="/private/blind"),
               dict(kind="assisted_review", session_id=blind, blocked=True),
               dict(kind="recording_incomplete", session_id="Guided-natural-meal-20261106-174058-8029B059",
                    blocked=True)],
        span_reviews=[blind, "Guided-talking-20261109-100000-AAAA"],
        records=[dict(session_id="Guided-talking-20261109-100000-AAAA", experiment_id="talking")])
    assert status["phase"] == "label"
    steps = [s["title"] for s in status["steps"]]
    assert steps[0] == "Blind review: Not eating (Oct 26)"
    assert steps[1] == "Confirm chewing spans: Talking (Nov 9)"
    assert not any(blind in s.get("session_id", "") and s["id"].startswith("spans-") for s in status["steps"])
    assert "/private" not in json.dumps(status)


def test_problems_and_processing_take_priority():
    status = _build(service_status={"attention": [{"error": "boom"}], "current": {"session_id": "X-20261101-100000-A"}})
    assert status["phase"] == "fix" and "errors" in status["steps"][0]["title"]
    assert status["steps"][1]["stage"] == "wait"
    assert _build(service_status={"current": {"session_id": "X"}})["phase"] == "wait"


def test_training_and_missing_models_are_stated_plainly():
    status = _build(requirements={"goals": GOALS, "actions": [], "training_state": "running"}, report=_report(ready=True),
                    catalog={"experiments": []}, live=None)
    assert status["phase"] == "train"
    checks = {c["id"]: c for c in status["checks"]}
    assert checks["more_data"]["state"] == "ok"
    assert checks["test_model"]["state"] == "blocked"
    assert _build(report=_report(gold=0))["checks"][1]["state"] == "blocked"


def test_recording_names_are_readable():
    assert recording_name("Guided-baseline-90s-20261102-171334-F83AB32C", {"experiment_id": "baseline-90s"},
                          {"baseline-90s": "90-Second Baseline"}) == "90-Second Baseline (Nov 2)"
    assert recording_name("Eating-20251123-055649") == "Eating (Nov 23)"


def test_pending_span_reviews_skip_incomplete_diagnostic_and_reviewed(tmp_path):
    paths = LoopPaths(tmp_path / "loop").ensure()
    records = [dict(session_id=sid, **extra) for sid, extra in (
        ("A", {}), ("B", {"guided": True, "device_validation_complete": False}),
        ("C", {"role": "diagnostic"}), ("D", {}), ("E", {}))]
    for sid in "ABCD":
        folder = paths.assisted / "derived/assist" / sid
        folder.mkdir(parents=True)
        (folder / "coarse-proposals-v0001.json").write_text("{}")
    reviewed = paths.assisted / "sessions/raw/D/annotations"
    reviewed.mkdir(parents=True)
    (reviewed / "coarse-review-v0001.json").write_text("{}")
    assert pending_span_reviews(paths, records) == ["A"]


def test_write_skips_timestamp_only_changes(tmp_path):
    paths = LoopPaths(tmp_path / "loop").ensure()
    assert write_project_status(paths, _build(now="1"))
    assert not write_project_status(paths, _build(now="2"))
    assert json.loads(project_status_path(paths).read_text())["updated_at"] == "1"
    assert write_project_status(paths, _build(live=None, now="3"))


def test_swift_fixtures_match_builder_output():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]

    def shape(value):
        if isinstance(value, dict):
            return {k: shape(v) for k, v in value.items()}
        if isinstance(value, list):
            return [shape(value[0])] if value else []
        return type(value).__name__

    built = _build(tasks=[dict(kind="blind_review", session_id="Not-eating-20261026-174428-SYNTH", blocked=False)])
    for path in ("apps/live-ios/ChewSenseLiveTests/Fixtures/project_status_synthetic.json",
                 "apps/review-macos/ChewSenseReviewTests/Fixtures/project_status_synthetic.json"):
        fixture = json.loads((repo / path).read_text())
        assert set(fixture) == set(built), path
        assert shape(fixture["stages"]) == shape(built["stages"]) and shape(fixture["checks"]) == shape(built["checks"])
        assert {k for step in fixture["steps"] for k in step} == {k for step in built["steps"] for k in step}
