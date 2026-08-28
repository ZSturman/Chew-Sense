"""Schema-v2 candidate, review, audit, and materialization contract.

The immutable candidate artifact is the only automatic input to review.  A
review draft names the exact candidate revision and SHA-256 it was created
from.  Finalization freezes that draft, materializes derived motion targets,
and updates a small pointer; it never mutates candidate or review revisions.

Version-1 event JSON remains useful as detector telemetry, but is deliberately
not accepted here as reviewed truth.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "2.0.0"
TIMESTAMP_TOLERANCE_S = 1e-6

CoarseLabelState = Literal["reviewed_intervals", "all_negative", "missing"]
ReviewDisposition = Literal[
    "accepted",
    "edited",
    "rejected",
    "unobservable",
    "uncertain",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    payload.pop("content_sha256", None)
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_sha256(value: BaseModel | dict[str, Any]) -> str:
    """Hash an artifact envelope, excluding its ``content_sha256`` field."""
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _write_json_exclusive(path: Path, value: BaseModel | dict[str, Any]) -> None:
    """Create an immutable JSON artifact and refuse accidental replacement."""
    if path.exists():
        raise FileExistsError(f"immutable artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_pointer(path: Path, value: BaseModel | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChewCandidate(_StrictModel):
    id: str
    start_closed_ts: float
    open_peak_ts: float
    end_closed_ts: float
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_anchors(self) -> "ChewCandidate":
        anchors = (self.start_closed_ts, self.open_peak_ts, self.end_closed_ts)
        if not all(math.isfinite(value) for value in anchors):
            raise ValueError("chew anchors must be finite")
        if not self.start_closed_ts < self.open_peak_ts < self.end_closed_ts:
            raise ValueError(
                "chew must satisfy start_closed_ts < open_peak_ts < end_closed_ts"
            )
        return self

    @property
    def chew_event_ts(self) -> float:
        return self.end_closed_ts


class FoodEntryCandidate(_StrictModel):
    id: str
    food_entry_ts: float
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timestamp(self) -> "FoodEntryCandidate":
        if not math.isfinite(self.food_entry_ts):
            raise ValueError("food_entry_ts must be finite")
        return self


class CandidateArtifact(_StrictModel):
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION
    artifact_type: Literal["vision_candidates"] = "vision_candidates"
    session_id: str
    candidate_revision: int = Field(ge=1)
    generated_at: str
    model_versions: dict[str, str] = Field(default_factory=dict)
    coarse_label_state: CoarseLabelState
    requires_full_coarse_review: bool
    chews: tuple[ChewCandidate, ...] = ()
    food_entries: tuple[FoodEntryCandidate, ...] = ()
    debug_telemetry: dict[str, Any] = Field(default_factory=dict)
    content_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "CandidateArtifact":
        _validate_unique_ids(self.chews, "chew candidate")
        _validate_unique_ids(self.food_entries, "food-entry candidate")
        _validate_cycle_order(self.chews)
        ordered_food = sorted(self.food_entries, key=lambda item: item.food_entry_ts)
        if list(self.food_entries) != ordered_food:
            raise ValueError("food-entry candidates must be timestamp ordered")
        if any(
            current.food_entry_ts <= previous.food_entry_ts
            for previous, current in zip(self.food_entries, self.food_entries[1:])
        ):
            raise ValueError("food-entry candidate timestamps must be unique")
        if self.coarse_label_state == "all_negative" and (self.chews or self.food_entries):
            raise ValueError("an explicitly all-negative session must have zero candidates")
        if self.coarse_label_state == "missing" and not self.requires_full_coarse_review:
            raise ValueError("missing coarse labels require a full coarse review")
        if self.content_sha256 != content_sha256(self):
            raise ValueError("candidate content_sha256 does not match artifact content")
        return self


class ChewReviewDecision(_StrictModel):
    candidate_id: str
    disposition: ReviewDisposition
    start_closed_ts: float | None = None
    open_peak_ts: float | None = None
    end_closed_ts: float | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_edit(self) -> "ChewReviewDecision":
        anchors = (self.start_closed_ts, self.open_peak_ts, self.end_closed_ts)
        if self.disposition == "edited":
            if any(value is None for value in anchors):
                raise ValueError("edited chew decisions require all three anchors")
            assert all(value is not None for value in anchors)
            if not all(math.isfinite(value) for value in anchors):
                raise ValueError("edited chew anchors must be finite")
            if not self.start_closed_ts < self.open_peak_ts < self.end_closed_ts:  # type: ignore[operator]
                raise ValueError("edited chew anchors are not ordered")
        elif any(value is not None for value in anchors):
            raise ValueError("only edited chew decisions may replace anchors")
        return self


class FoodEntryReviewDecision(_StrictModel):
    candidate_id: str
    disposition: ReviewDisposition
    food_entry_ts: float | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_edit(self) -> "FoodEntryReviewDecision":
        if self.disposition == "edited" and self.food_entry_ts is None:
            raise ValueError("edited food-entry decisions require food_entry_ts")
        if self.disposition != "edited" and self.food_entry_ts is not None:
            raise ValueError("only edited food-entry decisions may replace food_entry_ts")
        if self.food_entry_ts is not None and not math.isfinite(self.food_entry_ts):
            raise ValueError("edited food-entry timestamp must be finite")
        return self


class AddedChew(_StrictModel):
    id: str
    disposition: Literal["added"] = "added"
    start_closed_ts: float
    open_peak_ts: float
    end_closed_ts: float
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    note: str | None = None

    @model_validator(mode="after")
    def validate_anchors(self) -> "AddedChew":
        ChewCandidate(
            id=self.id,
            start_closed_ts=self.start_closed_ts,
            open_peak_ts=self.open_peak_ts,
            end_closed_ts=self.end_closed_ts,
            confidence=self.confidence,
        )
        return self


class AddedFoodEntry(_StrictModel):
    id: str
    disposition: Literal["added"] = "added"
    food_entry_ts: float
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    note: str | None = None

    @model_validator(mode="after")
    def validate_timestamp(self) -> "AddedFoodEntry":
        if not math.isfinite(self.food_entry_ts):
            raise ValueError("added food-entry timestamp must be finite")
        return self


class ReviewDraft(_StrictModel):
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION
    artifact_type: Literal["review_draft"] = "review_draft"
    session_id: str
    candidate_revision: int = Field(ge=1)
    candidate_sha256: str
    reviewer: str | None = None
    updated_at: str
    verified: bool = False
    full_video_pass_completed: bool = False
    chew_decisions: tuple[ChewReviewDecision, ...] = ()
    food_entry_decisions: tuple[FoodEntryReviewDecision, ...] = ()
    added_chews: tuple[AddedChew, ...] = ()
    added_food_entries: tuple[AddedFoodEntry, ...] = ()
    notes: str | None = None

    @model_validator(mode="after")
    def validate_ids(self) -> "ReviewDraft":
        _validate_unique_ids(self.chew_decisions, "chew decision", key="candidate_id")
        _validate_unique_ids(
            self.food_entry_decisions, "food-entry decision", key="candidate_id"
        )
        _validate_unique_ids(self.added_chews, "added chew")
        _validate_unique_ids(self.added_food_entries, "added food entry")
        return self


class ReviewRevision(ReviewDraft):
    artifact_type: Literal["review_revision"] = "review_revision"
    review_revision: int = Field(ge=1)
    finalized_at: str
    content_sha256: str

    @model_validator(mode="after")
    def validate_hash(self) -> "ReviewRevision":
        if self.content_sha256 != content_sha256(self):
            raise ValueError("review content_sha256 does not match artifact content")
        return self


class MaterializedChew(_StrictModel):
    id: str
    start_closed_ts: float
    open_peak_ts: float
    end_closed_ts: float
    chew_event_ts: float
    confidence: float = Field(ge=0.0, le=1.0)
    review_disposition: Literal["accepted", "edited", "added"]
    food_entry_id: str | None = None
    chewing_bout_id: str

    @model_validator(mode="after")
    def validate_contract(self) -> "MaterializedChew":
        if not self.start_closed_ts < self.open_peak_ts < self.end_closed_ts:
            raise ValueError("materialized chew anchors are not ordered")
        if self.chew_event_ts != self.end_closed_ts:
            raise ValueError("chew_event_ts must equal end_closed_ts")
        return self


class MaterializedFoodEntry(_StrictModel):
    id: str
    food_entry_ts: float
    confidence: float = Field(ge=0.0, le=1.0)
    review_disposition: Literal["accepted", "edited", "added"]


class MaterializedBout(_StrictModel):
    id: str
    food_entry_id: str | None = None
    chew_ids: tuple[str, ...]
    start_ts: float
    end_ts: float

    @model_validator(mode="after")
    def validate_contract(self) -> "MaterializedBout":
        if not self.chew_ids:
            raise ValueError("a chewing bout must contain at least one chew")
        if not math.isfinite(self.start_ts) or not math.isfinite(self.end_ts):
            raise ValueError("chewing-bout bounds must be finite")
        if self.end_ts < self.start_ts:
            raise ValueError("chewing-bout end must not precede its start")
        if len(set(self.chew_ids)) != len(self.chew_ids):
            raise ValueError("chewing-bout chew IDs must be unique")
        return self


class MaterializedAnnotations(_StrictModel):
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION
    artifact_type: Literal["materialized_annotations"] = "materialized_annotations"
    session_id: str
    generated_at: str
    candidate_revision: int
    candidate_sha256: str
    review_revision: int
    review_sha256: str
    verified: bool
    full_video_pass_completed: bool
    chew_decisions: tuple[ChewReviewDecision, ...]
    food_entry_decisions: tuple[FoodEntryReviewDecision, ...]
    chew_cycles: tuple[MaterializedChew, ...]
    food_entries: tuple[MaterializedFoodEntry, ...]
    chewing_bouts: tuple[MaterializedBout, ...]
    excluded_chew_ids: tuple[str, ...] = ()
    excluded_food_entry_ids: tuple[str, ...] = ()
    content_sha256: str

    @model_validator(mode="after")
    def validate_hash(self) -> "MaterializedAnnotations":
        _validate_unique_ids(self.chew_cycles, "materialized chew")
        _validate_unique_ids(self.food_entries, "materialized food entry")
        _validate_unique_ids(self.chewing_bouts, "materialized bout")
        _validate_cycle_order(self.chew_cycles)
        if self.verified and not self.full_video_pass_completed:
            raise ValueError("verified materialization requires a full-video pass")
        if self.content_sha256 != content_sha256(self):
            raise ValueError("materialized content_sha256 does not match artifact content")
        return self


class CanonicalPointer(_StrictModel):
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION
    artifact_type: Literal["canonical_pointer"] = "canonical_pointer"
    session_id: str
    selected_review_revision: int
    selected_review_sha256: str
    candidate_revision: int
    candidate_sha256: str
    review_path: str
    materialized_path: str
    materialized_sha256: str


class AuditIssue(_StrictModel):
    code: str
    message: str
    path: str | None = None


class AuditReport(_StrictModel):
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION
    session_id: str
    ok: bool
    errors: tuple[AuditIssue, ...] = ()
    warnings: tuple[AuditIssue, ...] = ()


def _validate_unique_ids(
    values: Iterable[Any], label: str, *, key: str = "id"
) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        identifier = str(getattr(value, key))
        if identifier in seen:
            duplicates.add(identifier)
        seen.add(identifier)
    if duplicates:
        raise ValueError(f"duplicate {label} ids: {sorted(duplicates)}")


def _validate_cycle_order(cycles: Iterable[Any]) -> None:
    ordered = list(cycles)
    expected = sorted(ordered, key=lambda item: (item.start_closed_ts, item.end_closed_ts))
    if ordered != expected:
        raise ValueError("chew cycles must be ordered")
    previous_end: float | None = None
    for cycle in ordered:
        if previous_end is not None and cycle.start_closed_ts < previous_end:
            raise ValueError(
                "chew cycles overlap; adjacent cycles may share a closed boundary "
                "but may not cross it"
            )
        previous_end = cycle.end_closed_ts


def _next_number(directory: Path, pattern: str, prefix: str) -> int:
    numbers: list[int] = []
    for path in directory.glob(pattern):
        stem = path.stem
        try:
            numbers.append(int(stem.removeprefix(prefix)))
        except ValueError:
            continue
    return max(numbers, default=0) + 1


def _coarse_state(coarse_intervals: Sequence[Any] | None) -> tuple[CoarseLabelState, bool]:
    if coarse_intervals is None:
        return "missing", True
    if not coarse_intervals:
        return "all_negative", False
    labels = [getattr(item, "label", None) for item in coarse_intervals]
    if "chewing" not in labels and "unknown" not in labels:
        return "all_negative", False
    return "reviewed_intervals", "unknown" in labels


def _inside_chewing(ts: float, coarse_intervals: Sequence[Any] | None) -> bool:
    if coarse_intervals is None:
        return True
    return any(
        getattr(item, "label", None) == "chewing"
        and float(item.start_ts) - TIMESTAMP_TOLERANCE_S
        <= ts
        <= float(item.end_ts) + TIMESTAMP_TOLERANCE_S
        for item in coarse_intervals
    )


def build_candidate_artifact(
    *,
    session_id: str,
    candidate_revision: int,
    chew_hypotheses: Iterable[Any],
    food_entry_hypotheses: Iterable[Any],
    coarse_intervals: Sequence[Any] | None,
    model_versions: dict[str, str] | None = None,
    generated_at: str | None = None,
    debug_telemetry: dict[str, Any] | None = None,
) -> CandidateArtifact:
    """Normalize raw hypotheses exactly once and create a hashed envelope.

    Invalid or overlapping raw hypotheses are retained in
    ``debug_telemetry.discarded_raw_hypotheses`` with an explicit reason.  No
    consumer is allowed to trim or drop candidates after this point.
    """
    coarse_state, requires_full_review = _coarse_state(coarse_intervals)
    discarded: list[dict[str, Any]] = []
    chews: list[ChewCandidate] = []

    def raw_value(raw: Any, canonical_name: str, legacy_name: str) -> Any:
        if isinstance(raw, dict):
            if canonical_name in raw:
                return raw[canonical_name]
            return raw[legacy_name]
        if hasattr(raw, canonical_name):
            return getattr(raw, canonical_name)
        return getattr(raw, legacy_name)

    def anchors(raw: Any) -> tuple[float, float, float]:
        if isinstance(raw, dict):
            return (
                float(raw.get("start_closed_ts", raw.get("start_ts"))),
                float(raw.get("open_peak_ts", raw.get("peak_ts"))),
                float(raw.get("end_closed_ts", raw.get("end_ts"))),
            )
        return tuple(
            float(raw_value(raw, canonical_name, legacy_name))
            for canonical_name, legacy_name in (
                ("start_closed_ts", "start_ts"),
                ("open_peak_ts", "peak_ts"),
                ("end_closed_ts", "end_ts"),
            )
        )  # type: ignore[return-value]

    raw_chews: list[tuple[float, float, float, Any]] = []
    for raw in chew_hypotheses:
        try:
            start, peak, end = anchors(raw)
        except (AttributeError, TypeError, ValueError) as exc:
            discarded.append({"kind": "chew", "reason": f"invalid anchors: {exc}"})
            continue
        raw_chews.append((start, peak, end, raw))

    for start, peak, end, raw in sorted(raw_chews, key=lambda item: item[:3]):
        if coarse_state == "all_negative" or not _inside_chewing(peak, coarse_intervals):
            discarded.append(
                {
                    "kind": "chew",
                    "reason": "outside reviewed chewing intervals",
                    "anchors": [start, peak, end],
                }
            )
            continue
        try:
            confidence = float(
                raw.get("confidence", 0.0)
                if isinstance(raw, dict)
                else getattr(raw, "confidence", 0.0)
            )
            evidence = dict(
                raw.get("evidence", {})
                if isinstance(raw, dict)
                else getattr(raw, "evidence", {})
            )
            candidate = ChewCandidate(
                id=f"ch_{len(chews):04d}",
                start_closed_ts=start,
                open_peak_ts=peak,
                end_closed_ts=end,
                confidence=confidence,
                evidence=evidence,
            )
        except (TypeError, ValueError) as exc:
            discarded.append(
                {
                    "kind": "chew",
                    "reason": str(exc),
                    "anchors": [start, peak, end],
                }
            )
            continue
        if chews and candidate.start_closed_ts < chews[-1].end_closed_ts:
            discarded.append(
                {
                    "kind": "chew",
                    "reason": f"overlaps normalized candidate {chews[-1].id}",
                    "anchors": [start, peak, end],
                }
            )
            continue
        chews.append(candidate)

    raw_food: list[tuple[float, Any]] = []
    for raw in food_entry_hypotheses:
        try:
            ts = float(raw_value(raw, "food_entry_ts", "ts"))
            raw_food.append((ts, raw))
        except (AttributeError, TypeError, ValueError) as exc:
            discarded.append({"kind": "food_entry", "reason": f"invalid timestamp: {exc}"})
    food_entries: list[FoodEntryCandidate] = []
    for ts, raw in sorted(raw_food, key=lambda item: item[0]):
        if coarse_state == "all_negative" or not _inside_chewing(ts, coarse_intervals):
            discarded.append(
                {
                    "kind": "food_entry",
                    "reason": "outside reviewed chewing intervals",
                    "food_entry_ts": ts,
                }
            )
            continue
        if food_entries and ts <= food_entries[-1].food_entry_ts:
            discarded.append(
                {
                    "kind": "food_entry",
                    "reason": f"duplicates normalized candidate {food_entries[-1].id}",
                    "food_entry_ts": ts,
                }
            )
            continue
        try:
            confidence = float(
                raw.get("confidence", 0.0)
                if isinstance(raw, dict)
                else getattr(raw, "confidence", 0.0)
            )
            evidence = dict(
                raw.get("evidence", {})
                if isinstance(raw, dict)
                else getattr(raw, "evidence", {})
            )
            food_entries.append(
                FoodEntryCandidate(
                    id=f"fe_{len(food_entries):04d}",
                    food_entry_ts=ts,
                    confidence=confidence,
                    evidence=evidence,
                )
            )
        except (TypeError, ValueError) as exc:
            discarded.append(
                {
                    "kind": "food_entry",
                    "reason": str(exc),
                    "food_entry_ts": ts,
                }
            )

    telemetry = dict(debug_telemetry or {})
    telemetry["discarded_raw_hypotheses"] = discarded
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "vision_candidates",
        "session_id": session_id,
        "candidate_revision": candidate_revision,
        "generated_at": generated_at or utc_now(),
        "model_versions": dict(model_versions or {}),
        "coarse_label_state": coarse_state,
        "requires_full_coarse_review": requires_full_review,
        "chews": [item.model_dump(mode="json") for item in chews],
        "food_entries": [item.model_dump(mode="json") for item in food_entries],
        "debug_telemetry": telemetry,
    }
    data["content_sha256"] = content_sha256(data)
    return CandidateArtifact.model_validate(data)


def write_candidate_artifact(
    derived_dir: str | Path,
    *,
    session_id: str,
    chew_hypotheses: Iterable[Any],
    food_entry_hypotheses: Iterable[Any],
    coarse_intervals: Sequence[Any] | None,
    model_versions: dict[str, str] | None = None,
    generated_at: str | None = None,
    debug_telemetry: dict[str, Any] | None = None,
) -> tuple[Path, CandidateArtifact]:
    derived = Path(derived_dir)
    revision = _next_number(derived, "candidates-v*.json", "candidates-v")
    artifact = build_candidate_artifact(
        session_id=session_id,
        candidate_revision=revision,
        chew_hypotheses=chew_hypotheses,
        food_entry_hypotheses=food_entry_hypotheses,
        coarse_intervals=coarse_intervals,
        model_versions=model_versions,
        generated_at=generated_at,
        debug_telemetry=debug_telemetry,
    )
    path = derived / f"candidates-v{revision:04d}.json"
    _write_json_exclusive(path, artifact)
    return path, artifact


def load_candidate_artifact(path: str | Path) -> CandidateArtifact:
    payload = _read_json(Path(path))
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"candidate schema {version!r} is not supported as v2 truth; rerun vision"
        )
    return CandidateArtifact.model_validate(payload)


def load_review_draft(path: str | Path) -> ReviewDraft:
    payload = _read_json(Path(path))
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"review schema {version!r} is not supported; explicitly migrate and review afresh"
        )
    return ReviewDraft.model_validate(payload)


def validate_review_lineage(candidates: CandidateArtifact, review: ReviewDraft) -> None:
    if candidates.session_id != review.session_id:
        raise ValueError("review session_id does not match candidate artifact")
    if candidates.candidate_revision != review.candidate_revision:
        raise ValueError("stale review: candidate revision does not match")
    if candidates.content_sha256 != review.candidate_sha256:
        raise ValueError(
            "stale review: candidate SHA-256 does not match; reload the exact candidate revision"
        )
    candidate_chews = {item.id for item in candidates.chews}
    reviewed_chews = {item.candidate_id for item in review.chew_decisions}
    unknown_chews = reviewed_chews - candidate_chews
    if unknown_chews:
        raise ValueError(f"review references unknown chew candidates: {sorted(unknown_chews)}")
    candidate_food = {item.id for item in candidates.food_entries}
    reviewed_food = {item.candidate_id for item in review.food_entry_decisions}
    unknown_food = reviewed_food - candidate_food
    if unknown_food:
        raise ValueError(
            f"review references unknown food-entry candidates: {sorted(unknown_food)}"
        )
    added_chew_ids = {item.id for item in review.added_chews}
    if added_chew_ids & candidate_chews:
        raise ValueError("added chew IDs must not reuse automatic candidate IDs")
    added_food_ids = {item.id for item in review.added_food_entries}
    if added_food_ids & candidate_food:
        raise ValueError("added food-entry IDs must not reuse automatic candidate IDs")


def review_complete(candidates: CandidateArtifact, review: ReviewDraft) -> bool:
    validate_review_lineage(candidates, review)
    if not review.verified or not review.full_video_pass_completed:
        return False
    return (
        {item.id for item in candidates.chews}
        == {item.candidate_id for item in review.chew_decisions}
        and {item.id for item in candidates.food_entries}
        == {item.candidate_id for item in review.food_entry_decisions}
        and not candidates.requires_full_coarse_review
    )


def freeze_review(
    annotations_dir: Path,
    review: ReviewDraft,
    *,
    finalized_at: str | None = None,
) -> tuple[Path, ReviewRevision]:
    path, frozen = prepare_review_revision(
        annotations_dir,
        review,
        finalized_at=finalized_at,
    )
    _write_json_exclusive(path, frozen)
    return path, frozen


def prepare_review_revision(
    annotations_dir: Path,
    review: ReviewDraft,
    *,
    finalized_at: str | None = None,
) -> tuple[Path, ReviewRevision]:
    """Build the next immutable revision without writing it.

    Finalization uses this to validate materialization and motion targets before
    any immutable artifact is created.  ``freeze_review`` remains the small
    direct writer for callers that have already completed those checks.
    """
    revision = _next_number(annotations_dir, "review-v*.json", "review-v")
    data = review.model_dump(mode="json")
    data.update(
        {
            "artifact_type": "review_revision",
            "review_revision": revision,
            "finalized_at": finalized_at or utc_now(),
        }
    )
    data["content_sha256"] = content_sha256(data)
    frozen = ReviewRevision.model_validate(data)
    path = annotations_dir / f"review-v{revision:04d}.json"
    return path, frozen


def _selected_events(
    candidates: CandidateArtifact,
    review: ReviewDraft,
) -> tuple[list[tuple[ChewCandidate, str]], list[tuple[FoodEntryCandidate, str]], list[str], list[str]]:
    chew_map = {item.id: item for item in candidates.chews}
    food_map = {item.id: item for item in candidates.food_entries}
    chews: list[tuple[ChewCandidate, str]] = []
    food: list[tuple[FoodEntryCandidate, str]] = []
    excluded_chews: list[str] = []
    excluded_food: list[str] = []
    for decision in review.chew_decisions:
        candidate = chew_map[decision.candidate_id]
        if decision.disposition == "accepted":
            chews.append((candidate, "accepted"))
        elif decision.disposition == "edited":
            chews.append(
                (
                    ChewCandidate(
                        id=candidate.id,
                        start_closed_ts=float(decision.start_closed_ts),
                        open_peak_ts=float(decision.open_peak_ts),
                        end_closed_ts=float(decision.end_closed_ts),
                        confidence=1.0,
                        evidence={"edited_from_candidate": candidate.id},
                    ),
                    "edited",
                )
            )
        else:
            excluded_chews.append(candidate.id)
    for item in review.added_chews:
        chews.append(
            (
                ChewCandidate(
                    id=item.id,
                    start_closed_ts=item.start_closed_ts,
                    open_peak_ts=item.open_peak_ts,
                    end_closed_ts=item.end_closed_ts,
                    confidence=item.confidence,
                ),
                "added",
            )
        )
    for decision in review.food_entry_decisions:
        candidate = food_map[decision.candidate_id]
        if decision.disposition == "accepted":
            food.append((candidate, "accepted"))
        elif decision.disposition == "edited":
            food.append(
                (
                    FoodEntryCandidate(
                        id=candidate.id,
                        food_entry_ts=float(decision.food_entry_ts),
                        confidence=1.0,
                    ),
                    "edited",
                )
            )
        else:
            excluded_food.append(candidate.id)
    for item in review.added_food_entries:
        food.append(
            (
                FoodEntryCandidate(
                    id=item.id,
                    food_entry_ts=item.food_entry_ts,
                    confidence=item.confidence,
                ),
                "added",
            )
        )
    chews.sort(key=lambda item: (item[0].start_closed_ts, item[0].end_closed_ts))
    food.sort(key=lambda item: item[0].food_entry_ts)
    _validate_cycle_order(item[0] for item in chews)
    _validate_unique_ids((item[0] for item in chews), "selected chew")
    _validate_unique_ids((item[0] for item in food), "selected food entry")
    return chews, food, sorted(excluded_chews), sorted(excluded_food)


def materialize_annotations(
    candidates: CandidateArtifact,
    review: ReviewRevision,
    *,
    generated_at: str | None = None,
) -> MaterializedAnnotations:
    validate_review_lineage(candidates, review)
    chews, food_entries, excluded_chews, excluded_food = _selected_events(candidates, review)
    materialized_food = [
        MaterializedFoodEntry(
            id=item.id,
            food_entry_ts=item.food_entry_ts,
            confidence=item.confidence,
            review_disposition=disposition,  # type: ignore[arg-type]
        )
        for item, disposition in food_entries
    ]

    materialized_chews: list[MaterializedChew] = []
    bout_order: list[tuple[str, str | None]] = []
    for item, disposition in chews:
        preceding = [
            entry
            for entry, _ in food_entries
            if entry.food_entry_ts <= item.end_closed_ts
        ]
        food_entry_id = preceding[-1].id if preceding else None
        bout_id = f"bout-{food_entry_id}" if food_entry_id else "bout-unlinked"
        if (bout_id, food_entry_id) not in bout_order:
            bout_order.append((bout_id, food_entry_id))
        materialized_chews.append(
            MaterializedChew(
                id=item.id,
                start_closed_ts=item.start_closed_ts,
                open_peak_ts=item.open_peak_ts,
                end_closed_ts=item.end_closed_ts,
                chew_event_ts=item.end_closed_ts,
                confidence=item.confidence,
                review_disposition=disposition,  # type: ignore[arg-type]
                food_entry_id=food_entry_id,
                chewing_bout_id=bout_id,
            )
        )

    bouts: list[MaterializedBout] = []
    for bout_id, food_entry_id in bout_order:
        grouped = [item for item in materialized_chews if item.chewing_bout_id == bout_id]
        if not grouped:
            continue
        food_ts = next(
            (
                item.food_entry_ts
                for item in materialized_food
                if item.id == food_entry_id
            ),
            grouped[0].start_closed_ts,
        )
        bouts.append(
            MaterializedBout(
                id=bout_id,
                food_entry_id=food_entry_id,
                chew_ids=tuple(item.id for item in grouped),
                start_ts=(
                    food_ts if food_entry_id is not None else grouped[0].start_closed_ts
                ),
                end_ts=grouped[-1].end_closed_ts,
            )
        )

    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "materialized_annotations",
        "session_id": candidates.session_id,
        "generated_at": generated_at or utc_now(),
        "candidate_revision": candidates.candidate_revision,
        "candidate_sha256": candidates.content_sha256,
        "review_revision": review.review_revision,
        "review_sha256": review.content_sha256,
        "verified": review.verified,
        "full_video_pass_completed": review.full_video_pass_completed,
        "chew_decisions": [item.model_dump(mode="json") for item in review.chew_decisions],
        "food_entry_decisions": [
            item.model_dump(mode="json") for item in review.food_entry_decisions
        ],
        "chew_cycles": [item.model_dump(mode="json") for item in materialized_chews],
        "food_entries": [item.model_dump(mode="json") for item in materialized_food],
        "chewing_bouts": [item.model_dump(mode="json") for item in bouts],
        "excluded_chew_ids": excluded_chews,
        "excluded_food_entry_ids": excluded_food,
    }
    data["content_sha256"] = content_sha256(data)
    return MaterializedAnnotations.model_validate(data)


def materialize_motion_rows(
    motion_csv_path: str | Path,
    annotations: MaterializedAnnotations,
    *,
    candidates: CandidateArtifact,
    time_offset: float,
    duration_s: float,
    coarse_intervals: Sequence[Any],
    timestamp_col: str = "timestamp",
) -> pd.DataFrame:
    """Derive phase and event targets; these rows are never edited truth."""
    motion = pd.read_csv(motion_csv_path)
    if timestamp_col not in motion:
        raise ValueError(f"motion CSV is missing {timestamp_col!r}")
    motion_ts = motion[timestamp_col].astype(float).to_numpy()
    if motion_ts.size > 1 and np.any(np.diff(motion_ts) <= 0):
        raise ValueError("motion timestamps must be strictly increasing")
    video_ts = motion_ts - float(time_offset)
    n = video_ts.size
    eating = np.zeros(n, dtype=np.int8)
    for interval in coarse_intervals:
        if getattr(interval, "label", None) != "chewing":
            continue
        mask = (
            (video_ts >= float(interval.start_ts) - TIMESTAMP_TOLERANCE_S)
            & (video_ts <= float(interval.end_ts) + TIMESTAMP_TOLERANCE_S)
        )
        eating[mask] = 1

    phase = np.full(n, "idle", dtype=object)
    phase_angle = np.full(n, np.nan, dtype=np.float64)
    chew_id = np.full(n, "", dtype=object)
    confidence = np.zeros(n, dtype=np.float64)
    chew_event = np.zeros(n, dtype=np.int8)
    event_id = np.full(n, "", dtype=object)
    phase_valid = np.zeros(n, dtype=np.int8)
    event_valid = (
        (video_ts >= -TIMESTAMP_TOLERANCE_S)
        & (video_ts <= duration_s + TIMESTAMP_TOLERANCE_S)
    ).astype(np.int8)

    for chew in annotations.chew_cycles:
        opening = (
            (video_ts >= chew.start_closed_ts - TIMESTAMP_TOLERANCE_S)
            & (video_ts <= chew.open_peak_ts + TIMESTAMP_TOLERANCE_S)
        )
        closing = (
            (video_ts > chew.open_peak_ts + TIMESTAMP_TOLERANCE_S)
            & (video_ts <= chew.end_closed_ts + TIMESTAMP_TOLERANCE_S)
        )
        phase[opening] = "opening"
        phase[closing] = "closing"
        chew_id[opening | closing] = chew.id
        confidence[opening | closing] = chew.confidence
        phase_valid[opening | closing] = 1
        phase_angle[opening] = (
            math.pi
            * (video_ts[opening] - chew.start_closed_ts)
            / (chew.open_peak_ts - chew.start_closed_ts)
        )
        phase_angle[closing] = math.pi + (
            math.pi
            * (video_ts[closing] - chew.open_peak_ts)
            / (chew.end_closed_ts - chew.open_peak_ts)
        )
        if n:
            index = int(np.argmin(np.abs(video_ts - chew.chew_event_ts)))
            chew_event[index] = 1
            event_id[index] = chew.id

    # Apply exclusions *after* selected-cycle phases.  An edited or added
    # cycle may overlap a candidate that the reviewer marked unobservable;
    # that overlap must remain masked rather than being re-enabled by the
    # phase assignment loop above.
    candidate_map = {item.id: item for item in candidates.chews}
    for decision in annotations.chew_decisions:
        if decision.disposition not in {"unobservable", "uncertain"}:
            continue
        candidate = candidate_map[decision.candidate_id]
        uncertain_span = (
            (video_ts >= candidate.start_closed_ts - TIMESTAMP_TOLERANCE_S)
            & (video_ts <= candidate.end_closed_ts + TIMESTAMP_TOLERANCE_S)
        )
        event_valid[uncertain_span] = 0
        phase_valid[uncertain_span] = 0

    food_event = np.zeros(n, dtype=np.int8)
    food_entry_id = np.full(n, "", dtype=object)
    food_entry_valid = (
        (video_ts >= -TIMESTAMP_TOLERANCE_S)
        & (video_ts <= duration_s + TIMESTAMP_TOLERANCE_S)
    ).astype(np.int8)
    food_candidate_map = {item.id: item for item in candidates.food_entries}
    for decision in annotations.food_entry_decisions:
        if decision.disposition not in {"unobservable", "uncertain"} or not n:
            continue
        candidate = food_candidate_map[decision.candidate_id]
        index = int(np.argmin(np.abs(video_ts - candidate.food_entry_ts)))
        food_entry_valid[index] = 0
    for item in annotations.food_entries:
        if n:
            index = int(np.argmin(np.abs(video_ts - item.food_entry_ts)))
            food_event[index] = 1
            food_entry_id[index] = item.id

    phase_sin = np.sin(phase_angle)
    phase_cos = np.cos(phase_angle)
    within_video = (
        (video_ts >= -TIMESTAMP_TOLERANCE_S)
        & (video_ts <= duration_s + TIMESTAMP_TOLERANCE_S)
    )
    if coarse_intervals:
        coarse_valid = np.zeros(n, dtype=bool)
        for interval in coarse_intervals:
            if getattr(interval, "label", None) == "unknown":
                continue
            coarse_valid |= (
                (video_ts >= float(interval.start_ts) - TIMESTAMP_TOLERANCE_S)
                & (video_ts <= float(interval.end_ts) + TIMESTAMP_TOLERANCE_S)
            )
    else:
        # An explicitly empty coarse artifact means the full session was
        # reviewed all-negative. Missing coarse review never reaches verified
        # materialization.
        coarse_valid = np.ones(n, dtype=bool)
    return pd.DataFrame(
        {
            "timestamp": motion_ts,
            "video_ts": video_ts,
            "eating": eating,
            "chew_phase": phase.astype(str),
            "phase_sin": phase_sin.astype(np.float32),
            "phase_cos": phase_cos.astype(np.float32),
            "phase_valid": phase_valid,
            "chew_id": chew_id.astype(str),
            "chew_event": chew_event,
            "chew_event_id": event_id.astype(str),
            "chew_event_valid": event_valid,
            "food_entry_event": food_event,
            "food_entry_id": food_entry_id.astype(str),
            "food_entry_valid": food_entry_valid,
            "event_valid": event_valid,
            "label_valid": (within_video & coarse_valid).astype(np.int8),
            "label_source": (
                "human_reviewed" if annotations.verified else "human_review_draft"
            ),
            "annotation_revision": annotations.review_revision,
            "video_confidence": confidence.astype(np.float32),
        }
    )


def audit_artifacts(
    *,
    session_id: str,
    candidates: CandidateArtifact,
    review: ReviewDraft | ReviewRevision | None = None,
    materialized: MaterializedAnnotations | None = None,
    duration_s: float | None = None,
) -> AuditReport:
    """Read-only structural audit for in-memory or already-loaded artifacts."""
    errors: list[AuditIssue] = []
    warnings: list[AuditIssue] = []

    def error(code: str, message: str) -> None:
        errors.append(AuditIssue(code=code, message=message))

    if candidates.session_id != session_id:
        error("session_mismatch", "candidate session_id does not match session")
    if duration_s is not None:
        for chew in candidates.chews:
            if (
                chew.start_closed_ts < -TIMESTAMP_TOLERANCE_S
                or chew.end_closed_ts > duration_s + TIMESTAMP_TOLERANCE_S
            ):
                error("candidate_bounds", f"chew {chew.id} is outside video bounds")
        for entry in candidates.food_entries:
            if (
                entry.food_entry_ts < -TIMESTAMP_TOLERANCE_S
                or entry.food_entry_ts > duration_s + TIMESTAMP_TOLERANCE_S
            ):
                error("candidate_bounds", f"food entry {entry.id} is outside video bounds")
    if review is not None:
        try:
            validate_review_lineage(candidates, review)
        except ValueError as exc:
            error("review_lineage", str(exc))
        candidate_ids = {item.id for item in candidates.chews}
        reviewed_ids = {item.candidate_id for item in review.chew_decisions}
        if review.verified and candidate_ids != reviewed_ids:
            error("incomplete_chew_review", "verified review does not decide every chew candidate")
        food_ids = {item.id for item in candidates.food_entries}
        reviewed_food = {item.candidate_id for item in review.food_entry_decisions}
        if review.verified and food_ids != reviewed_food:
            error(
                "incomplete_food_review",
                "verified review does not decide every food-entry candidate",
            )
        if review.verified and not review.full_video_pass_completed:
            error("incomplete_full_video_review", "verified review lacks a full-video pass")
    if materialized is not None:
        if review is None:
            error("missing_review", "materialized annotations require a review revision")
        else:
            if materialized.session_id != session_id:
                error("materialized_lineage", "materialized session_id does not match")
            if materialized.candidate_revision != candidates.candidate_revision:
                error("materialized_lineage", "materialized candidate revision does not match")
            if materialized.candidate_sha256 != candidates.content_sha256:
                error("materialized_lineage", "materialized candidate hash does not match")
            if isinstance(review, ReviewRevision):
                if materialized.review_revision != review.review_revision:
                    error("materialized_lineage", "materialized review revision does not match")
                if materialized.review_sha256 != review.content_sha256:
                    error("materialized_lineage", "materialized review hash does not match")
            else:
                error("materialized_lineage", "materialized annotations require a frozen review")
            if materialized.verified != review.verified:
                error("materialized_lineage", "materialized verified state differs from review")
            if materialized.full_video_pass_completed != review.full_video_pass_completed:
                error("materialized_lineage", "materialized full-video state differs from review")
            if materialized.chew_decisions != review.chew_decisions:
                error("id_parity", "materialized chew decisions differ from frozen review")
            if materialized.food_entry_decisions != review.food_entry_decisions:
                error("id_parity", "materialized food-entry decisions differ from frozen review")
            decision_ids = {item.candidate_id for item in materialized.chew_decisions}
            if decision_ids != {item.id for item in candidates.chews}:
                error("id_parity", "candidate/review/materialized chew decision IDs differ")
            selected_expected = {
                item.candidate_id
                for item in review.chew_decisions
                if item.disposition in {"accepted", "edited"}
            }
            selected_expected |= {
                item.id
                for item in getattr(review, "added_chews", ())
            }
            if selected_expected != {item.id for item in materialized.chew_cycles}:
                error("id_parity", "review/materialized selected chew IDs differ")
            excluded_expected = {
                item.candidate_id
                for item in review.chew_decisions
                if item.disposition in {"rejected", "unobservable", "uncertain"}
            }
            if excluded_expected != set(materialized.excluded_chew_ids):
                error("id_parity", "excluded chew decision IDs are not preserved exactly")
            if (
                selected_expected | excluded_expected
            ) - {item.id for item in candidates.chews} != {
                item.id for item in getattr(review, "added_chews", ())
            }:
                error("id_parity", "candidate/selected/excluded/added chew accounting differs")
            selected_food_expected = {
                item.candidate_id
                for item in review.food_entry_decisions
                if item.disposition in {"accepted", "edited"}
            }
            selected_food_expected |= {
                item.id for item in getattr(review, "added_food_entries", ())
            }
            if selected_food_expected != {item.id for item in materialized.food_entries}:
                error("id_parity", "review/materialized selected food-entry IDs differ")
            excluded_food_expected = {
                item.candidate_id
                for item in review.food_entry_decisions
                if item.disposition in {"rejected", "unobservable", "uncertain"}
            }
            if excluded_food_expected != set(materialized.excluded_food_entry_ids):
                error("id_parity", "excluded food-entry decision IDs are not preserved exactly")
            candidate_food_ids = {item.id for item in candidates.food_entries}
            added_food_ids = {item.id for item in getattr(review, "added_food_entries", ())}
            if (
                selected_food_expected | excluded_food_expected
            ) - candidate_food_ids != added_food_ids:
                error("id_parity", "candidate/selected/excluded/added food-entry accounting differs")
            food_ids = {item.id for item in materialized.food_entries}
            ordered_food = sorted(
                materialized.food_entries,
                key=lambda item: item.food_entry_ts,
            )
            for chew in materialized.chew_cycles:
                if chew.food_entry_id is not None and chew.food_entry_id not in food_ids:
                    error("food_link", f"chew {chew.id} links unknown food entry")
                preceding = [
                    item.id
                    for item in ordered_food
                    if item.food_entry_ts <= chew.chew_event_ts
                ]
                expected_food_id = preceding[-1] if preceding else None
                if chew.food_entry_id != expected_food_id:
                    error(
                        "food_link",
                        f"chew {chew.id} is not linked to the most recent food entry",
                    )
            bout_ids = {item.id for item in materialized.chewing_bouts}
            bouted_chews: list[str] = []
            for bout in materialized.chewing_bouts:
                if bout.food_entry_id is not None and bout.food_entry_id not in food_ids:
                    error("food_link", f"bout {bout.id} links unknown food entry")
                bouted_chews.extend(bout.chew_ids)
                for chew_id in bout.chew_ids:
                    chew = next(
                        (item for item in materialized.chew_cycles if item.id == chew_id),
                        None,
                    )
                    if chew is None:
                        error("bout_link", f"bout {bout.id} links unknown chew {chew_id}")
                    elif chew.chewing_bout_id != bout.id:
                        error("bout_link", f"chew {chew_id} and bout {bout.id} disagree")
            if len(bouted_chews) != len(set(bouted_chews)):
                error("bout_link", "a chew appears in more than one chewing bout")
            if set(bouted_chews) != {item.id for item in materialized.chew_cycles}:
                error("bout_link", "chewing bouts do not cover every materialized chew")
            if any(item.chewing_bout_id not in bout_ids for item in materialized.chew_cycles):
                error("bout_link", "a materialized chew links an unknown chewing bout")
            if duration_s is not None:
                for chew in materialized.chew_cycles:
                    if (
                        chew.start_closed_ts < -TIMESTAMP_TOLERANCE_S
                        or chew.end_closed_ts > duration_s + TIMESTAMP_TOLERANCE_S
                    ):
                        error(
                            "materialized_bounds",
                            f"materialized chew {chew.id} is outside video bounds",
                        )
                for entry in materialized.food_entries:
                    if (
                        entry.food_entry_ts < -TIMESTAMP_TOLERANCE_S
                        or entry.food_entry_ts > duration_s + TIMESTAMP_TOLERANCE_S
                    ):
                        error(
                            "materialized_bounds",
                            f"materialized food entry {entry.id} is outside video bounds",
                        )
    if candidates.requires_full_coarse_review:
        warnings.append(
            AuditIssue(
                code="coarse_review_required",
                message="coarse labels are missing or incomplete; finalization is blocked",
            )
        )
    return AuditReport(
        session_id=session_id,
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


__all__ = [
    "SCHEMA_VERSION",
    "AddedChew",
    "AddedFoodEntry",
    "AuditReport",
    "CandidateArtifact",
    "CanonicalPointer",
    "ChewCandidate",
    "ChewReviewDecision",
    "FoodEntryCandidate",
    "FoodEntryReviewDecision",
    "MaterializedAnnotations",
    "ReviewDraft",
    "ReviewRevision",
    "audit_artifacts",
    "build_candidate_artifact",
    "content_sha256",
    "file_sha256",
    "freeze_review",
    "load_candidate_artifact",
    "load_review_draft",
    "materialize_annotations",
    "materialize_motion_rows",
    "prepare_review_revision",
    "review_complete",
    "utc_now",
    "validate_review_lineage",
    "write_candidate_artifact",
]
