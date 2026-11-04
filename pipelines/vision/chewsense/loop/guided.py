"""Guided-recording sidecar written by Collect next to ``session.json``.

The sidecar records *what was instructed and when it actually happened* on the
same host-monotonic clock as the media. Instructions become, at most, an
``intended_activity`` track. They are never coarse labels, never review
decisions and never evaluation truth.
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .experiments import ExperimentDefinition, ExperimentRegistry, definition_sha256

SIDECAR_NAME = "guided_session.json"
SIDECAR_SCHEMA = "guided-session/1"

EventType = Literal[
    "lead_in_started",
    "instruction_presented",
    "activity_started",
    "activity_paused",
    "activity_resumed",
    "activity_completed",
    "activity_restarted",
    "activity_marked_uncertain",
    "quality_failed",
    "quality_restored",
    "motion_lost",
    "motion_restored",
    "capture_interrupted",
    "capture_resumed",
    "user_acknowledged",
    "experiment_completed",
    "experiment_abandoned",
]
AttemptOutcome = Literal["completed", "completed_with_uncertainty", "interrupted", "abandoned"]


class _Model(BaseModel):
    # Forward compatible: newer Collect builds may add fields.
    model_config = ConfigDict(extra="allow")


class GuidedEvent(_Model):
    type: EventType
    host_time_s: float
    activity_index: int | None = None
    attempt: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ActivityAttempt(_Model):
    activity_id: str
    activity_index: int
    attempt: int = Field(ge=1)
    presented_host_s: float
    started_host_s: float | None = None
    ended_host_s: float | None = None
    outcome: AttemptOutcome
    paused_seconds: float = Field(default=0, ge=0)
    valid_seconds: float = Field(default=0, ge=0)
    quality_flags: list[str] = Field(default_factory=list)


class InvalidInterval(_Model):
    start_host_s: float
    end_host_s: float
    reasons: list[str]


class CheckResult(_Model):
    id: str
    passed: bool
    message: str
    activity_ids: list[str] = Field(default_factory=list)


class OnDeviceValidation(_Model):
    complete: bool
    checks: list[CheckResult]
    repeat_activity_ids: list[str] = Field(default_factory=list)


class QualitySummary(_Model):
    samples: int = 0
    face_visible_fraction: float | None = None
    low_light_fraction: float | None = None
    phone_motion_fraction: float | None = None
    motion_coverage_fraction: float | None = None


class GuidedSession(_Model):
    schema_version: Literal["guided-session/1"]
    session_id: str
    experiment_id: str
    experiment_version: int
    definition_sha256: str
    definition: dict[str, Any]
    catalog_version: int | None = None
    time_domain: Literal["host_monotonic"] = "host_monotonic"
    video_clock_origin_host_time_s: float
    events: list[GuidedEvent]
    attempts: list[ActivityAttempt]
    invalid_intervals: list[InvalidInterval] = Field(default_factory=list)
    quality: QualitySummary = Field(default_factory=QualitySummary)
    validation: OnDeviceValidation
    repeat_of_session_id: str | None = None
    repeated_activity_ids: list[str] = Field(default_factory=list)
    labels_status: Literal["intended_only_not_verified"] = "intended_only_not_verified"

    @model_validator(mode="after")
    def check(self) -> "GuidedSession":
        times = [e.host_time_s for e in self.events]
        if not all(math.isfinite(t) for t in times):
            raise ValueError("guided event times must be finite")
        if any(b < a for a, b in zip(times, times[1:])):
            raise ValueError("guided events must be in non-decreasing host-time order")
        for attempt in self.attempts:
            if attempt.started_host_s is not None and attempt.started_host_s < attempt.presented_host_s:
                raise ValueError("an activity cannot start before it was presented")
            if (
                attempt.started_host_s is not None
                and attempt.ended_host_s is not None
                and attempt.ended_host_s < attempt.started_host_s
            ):
                raise ValueError("an activity cannot end before it started")
        for interval in self.invalid_intervals:
            if interval.end_host_s < interval.start_host_s:
                raise ValueError("invalid intervals must be ordered")
        return self


def load_guided_session(session_dir: Path) -> GuidedSession | None:
    path = Path(session_dir) / SIDECAR_NAME
    if not path.exists():
        return None
    return GuidedSession.model_validate_json(path.read_text())


def verify_guided_session(
    session_dir: Path,
    registry: ExperimentRegistry,
    *,
    manifest_session_id: str,
    video_clock_origin: float,
) -> dict[str, Any]:
    """Return a deterministic Mac-side check of the sidecar.

    Mismatched definitions or clocks are reported, never repaired.
    """
    guided = load_guided_session(session_dir)
    if guided is None:
        return {"guided": False}
    problems: list[str] = []
    if guided.session_id != manifest_session_id:
        problems.append("sidecar session_id differs from session.json")
    if abs(guided.video_clock_origin_host_time_s - video_clock_origin) > 1e-6:
        problems.append("sidecar video clock origin differs from session.json")
    try:
        carried = ExperimentDefinition.model_validate(guided.definition)
        carried_sha = definition_sha256(carried)
    except ValueError as exc:
        carried, carried_sha = None, None
        problems.append(f"carried definition is invalid: {exc}")
    if carried_sha and carried_sha != guided.definition_sha256:
        problems.append("carried definition does not match its recorded sha256")
    registered = registry.find(guided.experiment_id, guided.experiment_version)
    if registered is None:
        problems.append("experiment version is not in the Mac registry")
    elif carried_sha and definition_sha256(registered) != carried_sha:
        problems.append("carried definition differs from the registered version")
    return {
        "guided": True,
        "experiment_id": guided.experiment_id,
        "experiment_version": guided.experiment_version,
        "definition_verified": not problems,
        "problems": problems,
        "device_validation_complete": guided.validation.complete,
        "failed_checks": [c.id for c in guided.validation.checks if not c.passed],
        "repeat_activity_ids": guided.validation.repeat_activity_ids,
        "repeat_of_session_id": guided.repeat_of_session_id,
    }


def intended_activity_track(guided: GuidedSession) -> dict[str, Any]:
    """Video-relative instructed intervals for completed attempts only.

    Interrupted attempts and invalid spans remain explicit. The result is
    context for review prioritization and per-activity reporting.
    """
    definition = ExperimentDefinition.model_validate(guided.definition)
    origin = guided.video_clock_origin_host_time_s
    intervals, interrupted = [], []
    for attempt in guided.attempts:
        if attempt.started_host_s is None or attempt.ended_host_s is None:
            continue
        activity = definition.activities[attempt.activity_index]
        if activity.id != attempt.activity_id:
            raise ValueError("attempt activity id does not match the definition order")
        if not attempt.outcome.startswith("completed"):
            interrupted.append({"activity_id": activity.id, "attempt": attempt.attempt,
                                "outcome": attempt.outcome,
                                "start_ts": max(0.0, attempt.started_host_s - origin),
                                "end_ts": max(0.0, attempt.ended_host_s - origin)})
            continue
        intervals.append(
            {
                "activity_id": activity.id,
                "kind": activity.kind,
                "intended_label": activity.intended_label,
                "sync_marker": activity.sync_marker,
                "attempt": attempt.attempt,
                "outcome": attempt.outcome,
                "start_ts": max(0.0, attempt.started_host_s - origin),
                "end_ts": max(0.0, attempt.ended_host_s - origin),
                "paused_seconds": attempt.paused_seconds,
            }
        )
    invalid = [
        {
            "start_ts": max(0.0, i.start_host_s - origin),
            "end_ts": max(0.0, i.end_host_s - origin),
            "reasons": i.reasons,
        }
        for i in guided.invalid_intervals
    ]
    return {
        "artifact_type": "intended_activity",
        "provenance": "guided_instruction",
        "verified": False,
        "independent_truth": False,
        "note": "What the wearer was instructed to do, with actual start/end times. Not a label.",
        "session_id": guided.session_id,
        "experiment_id": guided.experiment_id,
        "experiment_version": guided.experiment_version,
        "definition_sha256": guided.definition_sha256,
        "intervals": intervals,
        "interrupted_attempts": interrupted,
        "invalid_intervals": invalid,
    }


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    # Admin requests and the worker can publish the same status concurrently.
    # Each writer needs its own temporary file before the atomic replacement.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent,
                                     prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(payload)
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
