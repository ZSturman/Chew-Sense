"""One "where we are" message shared by Collect, Review and Live.

Every app shows this object verbatim, so the phase, the ordered next steps and
the answers to "can I test a model / does visual detection work / do we need
more data" never disagree between apps. It is derived only from files the loop
already writes; computing it never analyzes, trains, publishes a catalog or
consults the locked test. It reaches phones, so it holds no local paths.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..canonical import utc_now
from .requirements import build_requirements
from .store import LoopPaths, read_json, write_json_atomic

SCHEMA_VERSION = 1
REPO = Path(__file__).resolve().parents[4]
LIVE_SPEC = Path("apps/live-ios/ChewSenseLive/EatingDetector/eating-detector.json")

STAGES = (
    ("collect", "Collect", "collect"),
    ("label", "Label", "review"),
    ("train", "Train", "mac"),
    ("test", "Test", "live"),
)
HEADLINES = {
    "fix": "Fix a processing problem",
    "wait": "Processing a new recording",
    "label": "Label recordings in Review",
    "train": "Training a new model",
    "collect": "Collect more recordings",
    "test": "Test the model in Live",
}
_live_cache: dict[str, Any] = {}


def recording_name(session_id: str, record: dict[str, Any] | None = None,
                   titles: dict[str, str] | None = None) -> str:
    """'90-Second Baseline (Nov 2)' rather than a session ID."""
    experiment = (record or {}).get("experiment_id")
    title = (titles or {}).get(experiment) if experiment else None
    if not title:
        stem = re.split(r"-\d{8}", session_id, maxsplit=1)[0]
        title = stem.removeprefix("Guided-").replace("-", " ").strip().capitalize() or session_id
    match = re.search(r"(\d{8})-\d{6}", session_id)
    if match:
        try:
            day = datetime.strptime(match.group(1), "%Y%m%d")
            return f"{title} ({day.strftime('%b')} {day.day})"
        except ValueError:
            pass
    return title


def live_model(repo: Path = REPO) -> dict[str, Any] | None:
    """The chewing detector bundled in Live, read from the repository."""
    path = Path(repo) / LIVE_SPEC
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if _live_cache.get("key") != (str(path), mtime):
        spec = read_json(path, {}) or {}
        _live_cache.update(key=(str(path), mtime), value={
            "model_id": spec.get("model_id"), "status": spec.get("status")} if spec.get("model_id") else None)
    return _live_cache["value"]


def pending_span_reviews(paths: LoopPaths, records: list[dict[str, Any]]) -> list[str]:
    """Usable recordings with chewing-span proposals and no saved span review."""
    pending = []
    for record in records:
        session_id = record["session_id"]
        if record.get("role") == "diagnostic" or (record.get("guided") and not record.get("device_validation_complete")):
            continue
        if not any((paths.assisted / "derived/assist" / session_id).glob("coarse-proposals-v*.json")):
            continue
        if any((paths.assisted / "sessions/raw" / session_id / "annotations").glob("coarse-review-v*.json")):
            continue
        pending.append(session_id)
    return pending


def _collect_steps(catalog: dict[str, Any], limit: int = 2) -> list[dict[str, Any]]:
    # Same order as Collect's experiment list: open first, required, priority, title.
    rank = {"high": 0, "medium": 1, "low": 2}
    rows = []
    for entry in catalog.get("experiments") or []:
        definition, progress = entry.get("definition") or {}, entry.get("progress") or {}
        remaining = int(progress.get("remaining") or 0)
        rows.append(((remaining == 0, not definition.get("required"),
                      rank.get(entry.get("effective_priority") or definition.get("priority"), 3),
                      definition.get("title") or ""), definition, progress, remaining))
    steps = []
    for _, definition, progress, remaining in sorted(rows, key=lambda r: r[0]):
        if remaining <= 0 or len(steps) >= limit:
            continue
        done, target = int(progress.get("completed_valid") or 0), int(progress.get("target") or remaining)
        steps.append(dict(
            id=f"record-{definition.get('experiment_id')}", stage="collect", app="collect",
            experiment_id=definition.get("experiment_id"),
            title=f"Record {definition.get('title')}",
            detail=f"{done} of {target} complete. Follow the on-screen prompts to the end, then send it to the Mac."))
    return steps


def _goal_text(goals: list[dict[str, Any]]) -> str:
    short = {"positive": "verified chewing recordings", "negative": "verified talking/walking/drinking/head-movement recordings",
             "evaluation": "blind-reviewed test recordings", "gold": "hand-marked chews"}
    return ", ".join(f"{short.get(g['id'], g['title'])} {g['current']}/{g['target']}" for g in goals)


def build_project_status(*, service_status: dict[str, Any], tasks: list[dict[str, Any]],
                         requirements: dict[str, Any], report: dict[str, Any], catalog: dict[str, Any],
                         records: list[dict[str, Any]], span_reviews: list[str],
                         live: dict[str, Any] | None, now: str) -> dict[str, Any]:
    by_id = {r["session_id"]: r for r in records}
    titles = {(e.get("definition") or {}).get("experiment_id"): (e.get("definition") or {}).get("title")
              for e in catalog.get("experiments") or []}

    def name(session_id: str) -> str:
        return recording_name(session_id, by_id.get(session_id), titles)

    steps: list[dict[str, Any]] = []
    attention = service_status.get("attention") or []
    if attention:
        steps.append(dict(id="fix", stage="fix", app="review",
                          title="Resolve processing errors on the Mac",
                          detail=f"{len(attention)} recording(s) failed processing. Open Review → Pipeline to see the error and retry."))
    current = service_status.get("current")
    if current:
        steps.append(dict(id="wait", stage="wait", app="mac",
                          title=f"Wait while the Mac processes {name(current.get('session_id') or 'the new recording')}",
                          detail="Next steps update automatically when its checks finish."))

    blind_pending = set()
    for task in tasks:
        if task.get("blocked") or task.get("kind") == "recording_incomplete":
            continue
        sid, kind = task.get("session_id", ""), task.get("kind")
        if kind == "blind_review":
            blind_pending.add(sid)
            steps.append(dict(id=f"blind-{sid}", stage="label", app="review", session_id=sid,
                              title=f"Blind review: {name(sid)}",
                              detail="Open it in Review's blind workspace and mark every chew, or confirm there are none. Predictions stay hidden."))
        elif kind == "synchronization_review":
            steps.append(dict(id=f"timing-{sid}", stage="label", app="review", session_id=sid,
                              title=f"Confirm the timing nods in {name(sid)}",
                              detail="Compare the three nods with the motion traces in Review."))
        elif kind == "assisted_review":
            steps.append(dict(id=f"assisted-{sid}", stage="label", app="review", session_id=sid,
                              title=f"Check suggested chews: {name(sid)}",
                              detail="In Review's assisted workspace, accept, fix or reject each suggested chew."))
    for action in requirements.get("actions") or []:
        if action.get("id") in {"inspect-timing", "confirm-timing"}:
            steps.append(dict(id=action["id"], stage="label", app="review",
                              title=action["title"], detail=action["detail"]))
    for sid in span_reviews:
        if sid in blind_pending:
            continue  # its spans come after the blind review
        steps.append(dict(id=f"spans-{sid}", stage="label", app="review", session_id=sid,
                          title=f"Confirm chewing spans: {name(sid)}",
                          detail="Review → Chewing spans: play each flagged clip and label it chewing or not chewing."))

    readiness = (report.get("training") or {}).get("readiness") or {}
    training = requirements.get("training_state")
    if training == "running":
        steps.append(dict(id="training", stage="train", app="mac", title="Training runs on the Mac",
                          detail="Nothing to do. New results appear in Review → Training and Evaluation."))
    if any(a.get("id") == "timing-check" for a in requirements.get("actions") or []):
        steps.append(dict(id="record-timing-check", stage="collect", app="collect", experiment_id="timing-check",
                          title="Record the 30-Second Timing Check",
                          detail="Three distinct nods are needed before timing can be checked. No food required."))
    steps.extend(_collect_steps(catalog))
    if live:
        steps.append(dict(id="try-live", stage="test", app="live", optional=True,
                          title="Optional: try the experimental model in Live",
                          detail="Live → Connect AirPods → New model check. It shows what the model detects; it is not an accuracy test yet."))

    phase = next((s["stage"] for s in steps if not s.get("optional")), "test" if live else "collect")
    goals = requirements.get("goals") or []
    incomplete = int(requirements.get("incomplete_sessions") or 0)
    gates_text = _goal_text(goals)
    summaries = {
        "fix": "Processing stopped on at least one recording. Fix it before recording more.",
        "wait": "A recording is being checked on the Mac.",
        "label": f"{sum(s['stage'] == 'label' for s in steps)} labelling task(s) are waiting in Review. "
                 "After that, keep collecting recordings.",
        "train": "Data gates passed; an experimental model is training. Promotion is never automatic.",
        "collect": "The next model is waiting for more verified data." + (f" Have/need: {gates_text}." if gates_text else ""),
        "test": "No data or labelling tasks are open. Try the model in Live.",
    }
    summary = summaries[phase]
    if incomplete and phase in {"collect", "label"}:
        summary += f" {incomplete} earlier take(s) ended early and do not count."

    label_steps = sum(s["stage"] == "label" for s in steps)
    stage_detail = {
        "collect": f"{sum(int((e.get('progress') or {}).get('remaining') or 0) > 0 for e in catalog.get('experiments') or [])} experiments still requested",
        "label": f"{label_steps} waiting" if label_steps else "nothing waiting",
        "train": {"running": "running", "completed": "waiting for new verified data"}.get(training or "", "waiting for data gates"),
        "test": "experimental model in Live" if live else "no model in Live",
    }
    stage_of_phase = {"fix": "collect", "wait": "collect"}.get(phase, phase)
    stages = [dict(id=sid, title=title, app=app, current=sid == stage_of_phase, detail=stage_detail[sid])
              for sid, title, app in STAGES]

    checks = []
    eating = report.get("eating_detector") or {}
    dev = eating.get("development") or {}
    if live:
        trained = ""
        if eating.get("run_id") and eating["run_id"].removeprefix("eating-") in (live.get("model_id") or ""):
            f1 = dev.get("f1")
            trained = f" Development F1 {f1:.2f} on one wearer's labels." if isinstance(f1, (int, float)) else ""
        checks.append(dict(id="test_model", question="Can I test a model?", state="partial",
                           answer="Yes, as an experiment: Live has the AirPods chewing-span model." + trained +
                                  " Its accuracy in real use is not measured yet."))
    else:
        checks.append(dict(id="test_model", question="Can I test a model?", state="blocked",
                           answer="Not yet: no chewing model is installed in Live."))
    evaluation = report.get("evaluation") or {}
    primary = (evaluation.get("tolerances") or {}).get("0.250") or {}
    target = next((c.get("target") for c in (report.get("acceptance") or {}).get("checks") or []
                   if c.get("id") == "chew_event_f1_at_250ms"), None)
    if primary.get("gold_events"):
        target_text = f"; target {target:.2f}" if isinstance(target, (int, float)) else ""
        checks.append(dict(
            id="vision", question="Is visual chew detection working?", state="partial",
            answer=f"Partly. It runs on every recording and suggests chews. On {primary['gold_events']} hand-marked "
                   f"chews it found {primary.get('tp', 0)} (F1 {primary.get('f1', 0):.2f} within ±250 ms{target_text}). "
                   "Use it as labelling help, not a chew counter yet."))
    else:
        checks.append(dict(id="vision", question="Is visual chew detection working?", state="blocked",
                           answer="Not measured yet. It needs a blind-reviewed recording to compare against."))
    if readiness.get("ready"):
        checks.append(dict(id="more_data", question="Do we need more data?", state="ok",
                           answer="Not for the next training run: the data gates passed."))
    else:
        checks.append(dict(id="more_data", question="Do we need more data?", state="blocked",
                           answer="Yes. Have/need before the next model: " + (gates_text or "more verified recordings") + "."))

    return dict(schema_version=SCHEMA_VERSION, updated_at=now, phase=phase, headline=HEADLINES[phase],
                summary=summary, stages=stages, steps=steps[:6], checks=checks)


def compute_project_status(paths: LoopPaths, config: dict[str, Any], *, records: list[dict[str, Any]],
                           service_status: dict[str, Any] | None = None, repo: Path = REPO,
                           now: str | None = None) -> dict[str, Any]:
    status = service_status if service_status is not None else read_json(paths.status, {}) or {}
    report = read_json(paths.reports / "loop/latest.json", {}) or {}
    tasks = (read_json(paths.review_tasks, {}) or {}).get("tasks", [])
    requirements = report.get("requirements") or {}
    readiness = (report.get("training") or {}).get("readiness") or {}
    if report.get("dataset") and readiness:
        # Tasks change without a full analysis; refresh actions the same way phone status does.
        requirements = build_requirements(
            records, report["dataset"], report["evaluation"], report["synchronization"], readiness,
            report["training"]["history"], tasks, config, eating=report.get("eating_detector"))
    return build_project_status(
        service_status=status, tasks=tasks, requirements=requirements, report=report,
        catalog=read_json(paths.catalog, {}) or {}, records=records,
        span_reviews=pending_span_reviews(paths, records), live=live_model(repo), now=now or utc_now())


def project_status_path(paths: LoopPaths) -> Path:
    return paths.root / "project_status.json"


def write_project_status(paths: LoopPaths, status: dict[str, Any]) -> bool:
    """Rewrite only when something other than the timestamp changed."""
    path = project_status_path(paths)
    previous = read_json(path, {}) or {}
    if {k: v for k, v in previous.items() if k != "updated_at"} == {k: v for k, v in status.items() if k != "updated_at"}:
        return False
    write_json_atomic(path, json.loads(json.dumps(status, default=str)))
    return True
