"""Versioned guided-experiment definitions, validation and the published catalog.

A definition is data, not code: Collect renders it without an app release.
Every published version is immutable. Recordings carry the exact definition
they were captured with, and the Mac verifies it against this registry.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFINITION_SCHEMA = "guided-experiment/1"
CATALOG_SCHEMA = "guided-catalog/1"
SEED_DIR = Path(__file__).resolve().parents[2] / "configs" / "experiments"

ActivityKind = Literal[
    "sync_marker",
    "rest",
    "chew",
    "stop_chewing",
    "talk",
    "drink",
    "walk",
    "head_movement",
    "other",
]
# What the wearer is *asked* to do. This is never a verified label.
IntendedLabel = Literal["chewing", "not_chewing", "unknown"]
QualityPolicy = Literal["pause", "mark", "repeat"]

MAX_TOTAL_SECONDS = 600.0
MAX_ACTIVITY_SECONDS = 180.0
MIN_ACTIVITY_SECONDS = 2.0
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_UNSAFE_TEXT = re.compile(
    r"(?i)\b(choke|choking|swallow\s+whole|while\s+driving|drive|run(ning)?\s+fast|"
    r"close\s+your\s+eyes\s+while\s+walking|stairs|traffic|alcohol)\b"
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FoodRequirement(_Strict):
    kind: Literal["none", "food", "beverage", "food_or_beverage"] = "none"
    recommended: str | None = None
    category: str | None = None  # e.g. "crunchy", "soft", "water"


class AirPodsRequirement(_Strict):
    required: bool = True
    configuration: Literal["both_in_ear", "either", "not_required"] = "both_in_ear"


class CameraRequirement(_Strict):
    position: str
    face_required: bool = True
    jaw_required: bool = True
    min_brightness: float = Field(default=0.15, ge=0.0, le=1.0)
    max_phone_motion_g: float = Field(default=0.25, gt=0.0)


class Activity(_Strict):
    id: str
    kind: ActivityKind
    instruction: str = Field(min_length=3, max_length=90)
    detail: str | None = Field(default=None, max_length=240)
    duration_s: float
    expected_behavior: str = Field(min_length=3, max_length=160)
    intended_label: IntendedLabel
    requires_face: bool = True
    requires_motion: bool = True
    on_quality_loss: QualityPolicy = "pause"
    min_valid_fraction: float = Field(default=0.8, gt=0.0, le=1.0)
    sync_marker: bool = False
    repeatable: bool = True

    @model_validator(mode="after")
    def check(self) -> "Activity":
        if not _ID.match(self.id):
            raise ValueError(f"activity id {self.id!r} must be a lowercase slug")
        if not (MIN_ACTIVITY_SECONDS <= self.duration_s <= MAX_ACTIVITY_SECONDS):
            raise ValueError(
                f"activity {self.id} duration must be within "
                f"{MIN_ACTIVITY_SECONDS:g}-{MAX_ACTIVITY_SECONDS:g} s"
            )
        if self.kind == "sync_marker" and not self.sync_marker:
            raise ValueError("sync_marker activities must set sync_marker=true")
        if self.sync_marker and self.intended_label == "chewing":
            raise ValueError("sync markers are excluded from chewing supervision")
        if self.kind == "chew" and self.intended_label != "chewing":
            raise ValueError("chew activities must intend chewing")
        if self.kind in {"rest", "talk", "walk", "head_movement", "stop_chewing"} and (
            self.intended_label == "chewing"
        ):
            raise ValueError(f"{self.kind} activities cannot intend chewing")
        return self


class Completion(_Strict):
    min_activity_valid_fraction: float = Field(default=0.8, gt=0.0, le=1.0)
    min_motion_coverage: float = Field(default=0.9, ge=0.0, le=1.0)
    min_face_visible_fraction: float = Field(default=0.8, ge=0.0, le=1.0)
    required_sync_markers: int = Field(default=2, ge=0)


class Labeling(_Strict):
    coarse_review_required: bool = True
    event_review_required: bool = True
    wearer_no_food_attestation: bool = False
    blind_evaluation_eligible: bool = True


class RequestReason(_Strict):
    source: Literal["seed", "planner", "manual"] = "seed"
    weakness_id: str | None = None
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExperimentDefinition(_Strict):
    schema_version: Literal["guided-experiment/1"] = DEFINITION_SCHEMA
    experiment_id: str
    version: int = Field(ge=1)
    title: str = Field(min_length=3, max_length=60)
    summary: str = Field(min_length=3, max_length=200)
    purpose: str = Field(min_length=3, max_length=400)
    request: RequestReason
    priority: Literal["high", "medium", "low"] = "medium"
    required: bool = False
    target_recordings: int = Field(ge=1, le=50)
    estimated_minutes: float | None = None
    preparation: list[str] = Field(min_length=1)
    equipment: list[str] = Field(default_factory=list)
    food: FoodRequirement = Field(default_factory=FoodRequirement)
    airpods: AirPodsRequirement = Field(default_factory=AirPodsRequirement)
    camera: CameraRequirement
    safety_notes: list[str] = Field(default_factory=list)
    lead_in_s: float = Field(default=3.0, ge=0.0, le=10.0)
    activities: list[Activity] = Field(min_length=1, max_length=30)
    completion: Completion = Field(default_factory=Completion)
    labeling: Labeling = Field(default_factory=Labeling)
    evaluation_purpose: list[
        Literal["training", "evaluation", "confound_negative", "synchronization"]
    ] = Field(min_length=1)
    tags: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check(self) -> "ExperimentDefinition":
        if not _ID.match(self.experiment_id):
            raise ValueError("experiment_id must be a lowercase slug")
        ids = [a.id for a in self.activities]
        if len(set(ids)) != len(ids):
            raise ValueError("activity ids must be unique")
        if self.total_seconds > MAX_TOTAL_SECONDS:
            raise ValueError(f"experiments are limited to {MAX_TOTAL_SECONDS:g} s")
        food_free = self.food.kind == "none"
        if food_free and any(a.kind in {"chew", "drink"} for a in self.activities):
            raise ValueError("chew/drink activities require food or a beverage")
        if self.labeling.wearer_no_food_attestation and not food_free:
            raise ValueError("a no-food attestation is only valid for food-free experiments")
        markers = sum(a.sync_marker for a in self.activities)
        if markers < self.completion.required_sync_markers:
            raise ValueError("fewer sync-marker activities than required_sync_markers")
        if self.airpods.required is False and any(a.requires_motion for a in self.activities):
            raise ValueError("activities cannot require motion when AirPods are optional")
        if not self.camera.face_required and any(a.requires_face for a in self.activities):
            raise ValueError("activities cannot require the face when the camera does not")
        if any(a.kind == "walk" for a in self.activities):
            if not self.safety_notes:
                raise ValueError("walking experiments require safety notes")
            if any(a.requires_face for a in self.activities if a.kind == "walk"):
                raise ValueError("walking activities cannot require face visibility")
        for text in self._texts():
            if _UNSAFE_TEXT.search(text):
                raise ValueError(f"instruction text failed safety validation: {text!r}")
        return self

    def _texts(self) -> Iterable[str]:
        yield from (self.title, self.summary, self.purpose)
        yield from self.preparation
        for activity in self.activities:
            yield activity.instruction
            if activity.detail:
                yield activity.detail

    @property
    def total_seconds(self) -> float:
        return sum(a.duration_s for a in self.activities)


def _normalize(value: Any) -> Any:
    """JSON-number normalization so Swift and Python serializations hash alike."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("definitions must contain finite numbers")
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        _normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def definition_sha256(definition: ExperimentDefinition | dict[str, Any]) -> str:
    if isinstance(definition, ExperimentDefinition):
        payload = definition.model_dump(mode="json", exclude_none=True)
    else:
        payload = ExperimentDefinition.model_validate(definition).model_dump(
            mode="json", exclude_none=True
        )
    return hashlib.sha256(canonical_json(payload)).hexdigest()


class ExperimentRegistry:
    """Immutable per-version definition files plus a small mutable state file.

    ``<root>/<experiment_id>/v0001.json`` never changes after creation.
    ``state.json`` records which version is published or retired.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, experiment_id: str) -> Path:
        if not _ID.match(experiment_id):
            raise ValueError("invalid experiment id")
        return self.root / experiment_id

    def versions(self, experiment_id: str) -> list[int]:
        return sorted(
            int(p.stem[1:]) for p in self._dir(experiment_id).glob("v[0-9][0-9][0-9][0-9].json")
        )

    def load(self, experiment_id: str, version: int) -> ExperimentDefinition:
        path = self._dir(experiment_id) / f"v{version:04d}.json"
        return ExperimentDefinition.model_validate_json(path.read_text())

    def add(self, definition: ExperimentDefinition | dict[str, Any]) -> ExperimentDefinition:
        """Store a new version. Re-adding identical content is a no-op."""
        definition = ExperimentDefinition.model_validate(
            definition.model_dump() if isinstance(definition, ExperimentDefinition) else definition
        )
        folder = self._dir(definition.experiment_id)
        path = folder / f"v{definition.version:04d}.json"
        if path.exists():
            if definition_sha256(self.load(definition.experiment_id, definition.version)) != (
                definition_sha256(definition)
            ):
                raise FileExistsError(
                    f"{definition.experiment_id} v{definition.version} already exists with "
                    "different content; publish a new version instead"
                )
            return definition
        existing = self.versions(definition.experiment_id)
        if existing and definition.version <= existing[-1]:
            raise ValueError("new versions must be greater than every existing version")
        folder.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(definition.model_dump_json(indent=2, exclude_none=True) + "\n")
        temporary.rename(path)
        return definition

    def next_version(self, experiment_id: str) -> int:
        versions = self.versions(experiment_id) if self._dir(experiment_id).exists() else []
        return (versions[-1] + 1) if versions else 1

    def _state_path(self, experiment_id: str) -> Path:
        return self._dir(experiment_id) / "state.json"

    def state(self, experiment_id: str) -> dict[str, Any]:
        path = self._state_path(experiment_id)
        return json.loads(path.read_text()) if path.exists() else {"status": "draft"}

    def _set_state(self, experiment_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(experiment_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True))
        temporary.replace(path)

    def publish(self, experiment_id: str, version: int, *, by: str) -> None:
        self.load(experiment_id, version)  # validates
        history = self.state(experiment_id).get("history", [])
        history.append({"action": "publish", "version": version, "by": by})
        self._set_state(
            experiment_id,
            {"status": "published", "published_version": version, "history": history},
        )

    def retire(self, experiment_id: str, *, by: str, reason: str) -> None:
        state = self.state(experiment_id)
        history = state.get("history", [])
        history.append({"action": "retire", "by": by, "reason": reason})
        state.update(status="retired", history=history)
        self._set_state(experiment_id, state)

    def experiment_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and _ID.match(p.name))

    def published(self) -> list[ExperimentDefinition]:
        result = []
        for experiment_id in self.experiment_ids():
            state = self.state(experiment_id)
            if state.get("status") == "published":
                result.append(self.load(experiment_id, int(state["published_version"])))
        return result

    def find(self, experiment_id: str, version: int) -> ExperimentDefinition | None:
        try:
            return self.load(experiment_id, version)
        except (FileNotFoundError, ValueError):
            return None

    def seed(self, seed_dir: Path = SEED_DIR) -> list[str]:
        """Add and publish seed definitions that are not present yet."""
        added = []
        for path in sorted(seed_dir.glob("*.json")):
            definition = ExperimentDefinition.model_validate_json(path.read_text())
            if self.versions(definition.experiment_id):
                continue  # Seeded before, or superseded by a Mac-side version.
            self.add(definition)
            self.publish(definition.experiment_id, definition.version, by="seed")
            added.append(definition.experiment_id)
        return added


_PRIORITY = {"high": 0, "medium": 1, "low": 2}


def build_catalog(
    definitions: list[ExperimentDefinition],
    progress: dict[str, int],
    *,
    catalog_version: int,
    generated_at: str,
) -> dict[str, Any]:
    """Return the payload Collect caches. ``progress`` counts valid recordings."""
    entries = []
    for definition in sorted(
        definitions,
        key=lambda d: (not d.required, _PRIORITY[d.priority], d.experiment_id),
    ):
        completed = int(progress.get(definition.experiment_id, 0))
        entries.append(
            {
                "definition": definition.model_dump(mode="json", exclude_none=True),
                "definition_sha256": definition_sha256(definition),
                "progress": {
                    "target": definition.target_recordings,
                    "completed_valid": completed,
                    "remaining": max(0, definition.target_recordings - completed),
                },
            }
        )
    return {
        "schema_version": CATALOG_SCHEMA,
        "catalog_version": catalog_version,
        "generated_at": generated_at,
        "experiments": entries,
    }


def catalog_fingerprint(catalog: dict[str, Any]) -> str:
    """Content hash excluding volatile fields, used to bump catalog_version."""
    stable = {"experiments": catalog["experiments"]}
    return hashlib.sha256(canonical_json(stable)).hexdigest()
