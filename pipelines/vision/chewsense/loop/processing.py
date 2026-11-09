"""Automatic per-session processing with predictable Pause/Stop semantics.

Each job is a persisted list of idempotent steps. The worker records the step
before running it and marks it done only after its outputs exist, so a crash
or Stop resumes at that step without duplicating immutable artifacts.

* Pause: the running step finishes; no further step starts.
* Stop: the running Vision subprocess is terminated, its step is marked
  interrupted and will rerun; the queue stays paused until Resume.
* Resume: continue from the persisted step.

Source recordings are read-only and are never modified here.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable

from ..canonical import utc_now
from ..features.offline import offline_command, prefetch_models
from ..session_bundle import load_session_manifest
from .experiments import ExperimentRegistry
from .guided import (
    SIDECAR_NAME,
    intended_activity_track,
    load_guided_session,
    verify_guided_session,
    write_json_atomic,
)
from .importer import ArchiveError, import_archive
from .retirement import retired_ids
from .review_tasks import review_state, tasks_for_session
from .store import LoopPaths, read_json, update_record
from .sync_assess import assess_markers

SESSION_STEPS = (
    "import",
    "timing_audit",
    "guided_check",
    "assign_role",
    "prepare_copies",
    "vision",
    "sync_assessment",
    "coarse_proposals",
    "review_tasks",
)
RUN_ON_SESSIONS = Path(__file__).resolve().parents[2] / "run_on_sessions.py"


def propose_coarse_spans(assisted: Path, session_id: str, log_path: Path) -> dict[str, Any]:
    """Run ``chewsense-motion assist-propose`` for one recording (proposals, never labels).

    A subprocess keeps the motion pipeline an optional peer of this package.
    """
    tool = Path(sys.executable).with_name("chewsense-motion")
    command = [str(tool), "assist-propose",
               "--session-dir", str(assisted / "sessions/raw" / session_id),
               "--vision-dir", str(assisted / "derived/vision" / session_id),
               "--output", str(assisted / "derived/assist" / session_id)]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as log:
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    revisions = sorted((assisted / "derived/assist" / session_id).glob("coarse-proposals-v*.json"))
    if completed.returncode != 0 or not revisions:
        raise RuntimeError(f"assist-propose exited with status {completed.returncode}; see {log_path}")
    return read_json(revisions[-1]) or {}


class StepInterrupted(Exception):
    pass


class VisionRunner:
    """Runs the existing batch script for one session in a subprocess without network access."""

    def __init__(self, timeout_s: float = 3600):
        self.timeout_s = timeout_s
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.interrupted = False

    def __call__(self, workspace: Path, session_id: str, log_path: Path) -> None:
        env = dict(os.environ, CHEWSENSE_WORKSPACE=str(workspace),
                   CHEWSENSE_LOOP_PARENT_PID=str(os.getpid()),
                   CHEWSENSE_MODELS_DIR=str(prefetch_models()))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as log:
            with self._lock:
                self.interrupted = False
                self._process = subprocess.Popen(
                    offline_command([sys.executable, str(RUN_ON_SESSIONS), "--only", session_id]),
                    env=env, stdout=log, stderr=subprocess.STDOUT,
                    cwd=RUN_ON_SESSIONS.parent, start_new_session=True,
                )
            try:
                code = self._process.wait(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                self.terminate()
                raise RuntimeError("vision timed out")
            finally:
                with self._lock:
                    self._process = None
        if self.interrupted:
            raise StepInterrupted("vision stopped by request")
        if code != 0:
            raise RuntimeError(f"vision exited with status {code}; see {log_path}")

    def terminate(self) -> None:
        with self._lock:
            self.interrupted = True
            if self._process and self._process.poll() is None:
                try:
                    os.killpg(self._process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


def enqueue_job(paths: LoopPaths, archive: Path, *, archive_sha256: str, device: str | None,
                upload_id: str | None = None) -> dict[str, Any]:
    """Persist a session job. The same archive never creates two live jobs."""
    for path in paths.jobs.glob("*.json"):
        job = read_json(path) or {}
        if job.get("archive_sha256") == archive_sha256 and job.get("state") != "rejected":
            return job
    job = {
        "job_id": uuid.uuid4().hex,
        "kind": "session",
        "state": "queued",
        "archive": str(archive),
        "archive_sha256": archive_sha256,
        "upload_id": upload_id,
        "device": device,
        "session_id": None,
        "steps_done": [],
        "current_step": None,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "error": None,
    }
    write_json_atomic(paths.jobs / f"{job['job_id']}.json", job)
    return job


def refresh_review_tasks(paths: LoopPaths) -> list[dict[str, Any]]:
    """Rewrite ``review_tasks.json`` from the session records. Retired recordings get no tasks."""
    tasks = []
    retired = retired_ids(paths.root)
    for path in sorted(paths.session_state.glob("*.json")):
        record = read_json(path, {}) or {}
        if record.get("session_id") in retired:
            continue
        if record.get("processing") != "completed" and record.get("vision") != "completed":
            continue
        reports = paths.session_reports(record["session_id"])
        tasks.extend(tasks_for_session(record, paths.assisted, paths.blind, reports))
    tasks.sort(key=lambda t: (t["blocked"], t["priority"], t["session_id"]))
    write_json_atomic(paths.review_tasks, {"generated_at": utc_now(), "tasks": tasks})
    return tasks


def _role_bucket(session_id: str) -> float:
    return int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


class Processor:
    def __init__(
        self,
        paths: LoopPaths,
        config: dict[str, Any],
        *,
        vision_runner: Callable[[Path, str, Path], None] | None = None,
        on_session_done: Callable[[str], None] | None = None,
        proposer: Callable[[Path, str, Path], dict[str, Any]] | None = None,
    ):
        self.paths = paths.ensure()
        self.config = config
        self.registry = ExperimentRegistry(paths.experiments / "definitions")
        self.vision = vision_runner or VisionRunner(config["processing"]["vision_timeout_s"])
        self.proposer = proposer or propose_coarse_spans
        self.on_session_done = on_session_done
        self._wake = threading.Event()
        self._current: dict[str, Any] | None = None
        self._recover()

    # ----- control -------------------------------------------------------
    def control(self) -> dict[str, Any]:
        return read_json(self.paths.control, {"paused": False}) or {"paused": False}

    def pause(self) -> None:
        update_record(self.paths.control, paused=True, reason="pause", at=utc_now())
        self.write_status()

    def stop(self) -> None:
        update_record(self.paths.control, paused=True, reason="stop", at=utc_now())
        if hasattr(self.vision, "terminate"):
            self.vision.terminate()
        self.write_status()

    def resume(self) -> None:
        update_record(self.paths.control, paused=False, reason=None, at=utc_now())
        self._wake.set()
        self.write_status()

    # ----- jobs ----------------------------------------------------------
    def _job_path(self, job_id: str) -> Path:
        return self.paths.jobs / f"{job_id}.json"

    def jobs(self) -> list[dict[str, Any]]:
        jobs = [read_json(p) for p in self.paths.jobs.glob("*.json")]
        return sorted((j for j in jobs if j), key=lambda j: j["created_at"])

    def enqueue_archive(self, archive: Path, *, archive_sha256: str, device: str | None,
                        upload_id: str | None = None) -> dict[str, Any]:
        job = enqueue_job(self.paths, archive, archive_sha256=archive_sha256, device=device,
                          upload_id=upload_id)
        self._wake.set()
        self.write_status()
        return job

    def retry(self, job_id: str) -> dict[str, Any]:
        job = read_json(self._job_path(job_id))
        if not job or job["state"] not in {"needs_attention"}:
            raise ValueError("only jobs needing attention can be retried")
        job.update(state="queued", error=None, updated_at=utc_now())
        write_json_atomic(self._job_path(job_id), job)
        self._wake.set()
        return job

    def _recover(self) -> None:
        for job in self.jobs():
            if job["state"] == "running":
                job.update(state="queued", recovered_at=utc_now())
                write_json_atomic(self._job_path(job["job_id"]), job)

    def _save(self, job: dict[str, Any]) -> None:
        job["updated_at"] = utc_now()
        write_json_atomic(self._job_path(job["job_id"]), job)

    # ----- worker --------------------------------------------------------
    def run_once(self) -> bool:
        """Run one step of the oldest queued job. Returns False when idle."""
        if self.control().get("paused"):
            return False
        job = next((j for j in self.jobs() if j["state"] == "queued"), None)
        if job is None:
            return False
        step = next(s for s in SESSION_STEPS if s not in job["steps_done"])
        job.update(state="running", current_step=step)
        self._save(job)
        self._current = {"job_id": job["job_id"], "session_id": job.get("session_id"),
                         "step": step, "started_at": utc_now()}
        self.write_status()
        try:
            getattr(self, f"_step_{step}")(job)
        except StepInterrupted as exc:
            job.update(state="queued", interrupted_step=step, error=str(exc))
        except ArchiveError as exc:
            job.update(state="rejected", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - recorded for the operator
            job.update(state="needs_attention", error=f"{step}: {exc}",
                       traceback=traceback.format_exc(limit=5))
        else:
            job["steps_done"].append(step)
            job["error"] = None
            if len(job["steps_done"]) == len(SESSION_STEPS):
                job["state"] = "completed"
            elif job["state"] == "running":
                job["state"] = "queued"
        finally:
            self._current = None
        job["current_step"] = None
        self._save(job)
        if job["state"] == "completed" and self.on_session_done and job.get("session_id"):
            self.on_session_done(job["session_id"])
        self.write_status()
        return True

    def run_forever(self, stop_event: threading.Event, periodic: Callable[[], None] | None = None):
        last_periodic = 0.0
        interval = self.config["processing"]["rescan_interval_s"]
        while not stop_event.is_set():
            worked = False
            try:
                worked = self.run_once()
                if periodic and time.monotonic() - last_periodic >= interval:
                    last_periodic = time.monotonic()
                    periodic()
            except Exception:  # noqa: BLE001 - keep the service alive
                traceback.print_exc()
            if not worked:
                self._wake.wait(timeout=2.0)
                self._wake.clear()

    # ----- steps ---------------------------------------------------------
    def _record(self, session_id: str, **changes) -> dict[str, Any]:
        return update_record(self.paths.session_record(session_id), session_id=session_id,
                             updated_at=utc_now(), **changes)

    def _step_import(self, job):
        result = import_archive(Path(job["archive"]), self.paths,
                                expected_sha256=job["archive_sha256"], received_from=job["device"])
        job["session_id"] = result.session_id
        job["import_state"] = result.state
        if result.state == "conflict":
            self._record(result.session_id, attention=result.message)
            raise ArchiveError(result.message)
        record = read_json(self.paths.session_record(result.session_id), {}) or {}
        if result.state == "duplicate" and record.get("processing") == "completed":
            # Everything already exists; finish the job without redoing work.
            job["steps_done"] = list(SESSION_STEPS[1:-1])
        if result.state == "duplicate":
            # Keep the original provenance; only note that a copy arrived again.
            self._record(result.session_id, imported=True, processing="processing",
                         last_duplicate_at=utc_now(), last_duplicate_from=job["device"])
        else:
            self._record(result.session_id, imported=True, archive_sha256=job["archive_sha256"],
                         received_from=job["device"], processing="processing")
        if result.state in {"imported", "duplicate"}:
            # The verified original is retained under archives/; this is the upload copy.
            Path(job["archive"]).unlink(missing_ok=True)

    def _source(self, job) -> Path:
        return self.paths.source(job["session_id"])

    def _step_timing_audit(self, job):
        from ..eval.phase0 import timing_report

        report = timing_report(self._source(job))
        write_json_atomic(self.paths.session_reports(job["session_id"]) / "timing.json", report)
        self._record(job["session_id"], sensor_coverage=report["sensor_coverage_fraction"],
                     duration_s=report["duration_s"], motion_gaps=len(report["motion_gaps"]))

    def _step_guided_check(self, job):
        source = self._source(job)
        manifest = load_session_manifest(source)
        check = verify_guided_session(
            source, self.registry, manifest_session_id=manifest.session_id,
            video_clock_origin=manifest.clock_sync.video_clock_origin_host_time_s)
        reports = self.paths.session_reports(job["session_id"])
        write_json_atomic(reports / "guided-check.json", check)
        changes: dict[str, Any] = {"guided": check["guided"], "activity": manifest.activity,
                                   "legacy": manifest.legacy_review_required
                                   or manifest.source_app == "legacy_import"}
        if check["guided"]:
            guided = load_guided_session(source)
            write_json_atomic(reports / "intended-activity.json", intended_activity_track(guided))
            changes.update(
                experiment_id=check["experiment_id"],
                experiment_version=check["experiment_version"],
                definition_verified=check["definition_verified"],
                device_validation_complete=check["device_validation_complete"],
                failed_checks=check["failed_checks"],
                repeat_of_session_id=check["repeat_of_session_id"],
                tags=guided.definition.get("tags", {}),
                food_category=(guided.definition.get("food") or {}).get("category"),
            )
        self._record(job["session_id"], **changes)

    def _step_assign_role(self, job):
        session_id = job["session_id"]
        record = read_json(self.paths.session_record(session_id), {}) or {}
        if record.get("role"):
            return  # Never reassign.
        parent = record.get("repeat_of_session_id")
        parent_record = read_json(self.paths.session_record(parent), {}) if parent else None
        if parent_record and parent_record.get("role"):
            role, reason = parent_record["role"], f"inherits role of repeated session {parent}"
        elif record.get("legacy"):
            role, reason = "diagnostic", "legacy recordings are diagnostic only"
        else:
            guided = read_json(self.paths.session_reports(session_id) / "guided-check.json", {})
            definition = self.registry.find(guided.get("experiment_id", ""), guided.get("experiment_version", 0)) \
                if guided.get("guided") and guided.get("definition_verified") else None
            if definition and not {"training", "evaluation", "confound_negative"}.intersection(definition.evaluation_purpose):
                self._record(session_id, role="diagnostic", role_reason="Timing-only recording; excluded from model datasets",
                             role_assigned_at=utc_now())
                return
            eligible = definition is None or (
                definition.labeling.blind_evaluation_eligible
                and "evaluation" in definition.evaluation_purpose)
            roles = self.config["roles"]
            # Retired recordings no longer count toward the evaluation quota.
            retired = retired_ids(self.paths.root)
            evaluation_count = sum(
                1 for p in self.paths.session_state.glob("*.json")
                if (record := read_json(p, {}) or {}).get("role") == "evaluation"
                and record.get("session_id") not in retired)
            fraction = (roles["boosted_evaluation_fraction"]
                        if evaluation_count < roles["min_evaluation_sessions"]
                        else roles["evaluation_fraction"])
            bucket = _role_bucket(session_id)
            if eligible and bucket < fraction:
                role = "evaluation"
            else:
                role = "training"
            reason = (f"deterministic bucket {bucket:.3f} vs evaluation fraction {fraction:.2f}"
                      + ("" if eligible else "; experiment not evaluation-eligible"))
        self._record(session_id, role=role, role_reason=reason, role_assigned_at=utc_now())

    def _step_prepare_copies(self, job):
        from ..eval.phase0 import prepare_experiment

        session_id = job["session_id"]
        source = self._source(job)
        record = read_json(self.paths.session_record(session_id), {}) or {}
        assisted = self.paths.assisted / "sessions/raw" / session_id
        if not assisted.exists():
            prepare_experiment(source, self.paths.assisted)
            # Instructed-activity context helps the assisted reviewer only.
            # The blind copy never receives it.
            if (source / SIDECAR_NAME).exists():
                shutil.copy2(source / SIDECAR_NAME, assisted / SIDECAR_NAME)
                (assisted / SIDECAR_NAME).chmod(0o644)
        if record.get("role") == "evaluation":
            blind = self.paths.blind / "sessions/raw" / session_id
            if not blind.exists():
                prepare_experiment(source, self.paths.blind, blind=True)

    def _step_vision(self, job):
        session_id = job["session_id"]
        derived = self.paths.assisted / "derived/vision" / session_id
        if any(derived.glob("candidates-v*.json")):
            return
        if derived.exists():
            # Only mutable telemetry from an interrupted run can be here.
            shutil.rmtree(derived)
        self.vision(self.paths.assisted, session_id,
                    self.paths.session_reports(session_id) / "vision.log")
        if not any(derived.glob("candidates-v*.json")):
            raise RuntimeError("vision finished without an immutable candidate revision")
        self._record(session_id, vision="completed")

    def _step_sync_assessment(self, job):
        session_id = job["session_id"]
        reports = self.paths.session_reports(session_id)
        intended = read_json(reports / "intended-activity.json")
        if not intended:
            return
        source = self._source(job)
        manifest = load_session_manifest(source)
        result = assess_markers(
            intended,
            self.paths.assisted / "derived/vision" / session_id / "frame_predictions.csv",
            source / manifest.motion.path,
            manifest.clock_sync.video_clock_origin_host_time_s,
        )
        write_json_atomic(reports / "sync-assessment.json", result)
        self._record(session_id, sync_assessment=result["status"])

    def _step_coarse_proposals(self, job):
        """Video + motion chewing-span proposals for assisted review.

        Optional assistance: a failure is recorded on the session and in the
        job log but does not block review tasks.
        """
        session_id = job["session_id"]
        record = read_json(self.paths.session_record(session_id), {}) or {}
        if record.get("role") == "diagnostic":
            return
        try:
            payload = self.proposer(self.paths.assisted, session_id,
                                    self.paths.session_reports(session_id) / "assist.log")
        except Exception as exc:  # noqa: BLE001 - surfaced on the session, never hidden
            self._record(session_id, coarse_proposals="failed",
                         attention=f"chewing-span proposals failed: {exc}")
            return
        summary = payload.get("summary") or {}
        self._record(session_id, coarse_proposals="created",
                     coarse_proposal_summary={k: summary.get(k) for k in (
                         "coverage", "review_items", "batch_items", "label_conflicts")})

    def _step_review_tasks(self, job):
        self.refresh_review_tasks()
        self._record(job["session_id"], processing="completed")

    # ----- review follow-up ----------------------------------------------
    def refresh_review_tasks(self) -> list[dict[str, Any]]:
        return refresh_review_tasks(self.paths)

    def finalize_verified_drafts(self) -> list[str]:
        """Freeze drafts a human marked verified with a full pass."""
        from ..session_bundle import finalize_session

        if not self.config["processing"]["auto_finalize_verified_drafts"]:
            return []
        finalized = []
        for path in sorted(self.paths.session_state.glob("*.json")):
            record = read_json(path, {}) or {}
            session_id = record.get("session_id")
            if not session_id:
                continue
            for name, workspace in (("blind", self.paths.blind), ("assisted", self.paths.assisted)):
                if review_state(workspace, session_id) != "verified_draft":
                    continue
                try:
                    finalize_session(workspace / "sessions/raw" / session_id,
                                     workspace / "derived/vision" / session_id)
                    finalized.append(f"{name}:{session_id}")
                    self._record(session_id, **{f"{name}_review": "finalized"})
                except Exception as exc:  # noqa: BLE001 - surface, never force
                    self._record(session_id, attention=f"{name} finalization failed: {exc}")
        if finalized:
            self.refresh_review_tasks()
        return finalized

    # ----- status --------------------------------------------------------
    def write_status(self) -> dict[str, Any]:
        jobs = self.jobs()
        control = self.control()
        state = "processing" if self._current else ("paused" if control.get("paused") else "idle")
        if control.get("paused") and control.get("reason") == "stop":
            state = "stopped"
        status = {
            "updated_at": utc_now(),
            "state": state,
            "current": self._current,
            "pending": [j["session_id"] or j["job_id"][:8] for j in jobs if j["state"] == "queued"],
            "completed": [j["session_id"] for j in jobs if j["state"] == "completed"][-20:],
            "attention": [
                {"job_id": j["job_id"], "session_id": j.get("session_id"), "error": j.get("error"),
                 "state": j["state"]}
                for j in jobs if j["state"] in {"needs_attention", "rejected"}
            ],
        }
        write_json_atomic(self.paths.status, status)
        return status
