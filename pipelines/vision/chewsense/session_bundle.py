"""Canonical ChewSense schema-v2 session bundle and finalization workflow."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from .canonical import (
    SCHEMA_VERSION,
    AuditIssue,
    AuditReport,
    CandidateArtifact,
    CanonicalPointer,
    MaterializedAnnotations,
    ReviewDraft,
    ReviewRevision,
    audit_artifacts,
    file_sha256,
    load_candidate_artifact,
    load_review_draft,
    materialize_annotations,
    materialize_motion_rows,
    prepare_review_revision,
    review_complete,
    utc_now,
    write_candidate_artifact,
)
from .events.schema import VideoEvents


BUNDLE_SCHEMA_VERSION = SCHEMA_VERSION
ANNOTATION_SCHEMA_VERSION = SCHEMA_VERSION

SessionStatus = Literal[
    "collected",
    "coarse_labeled",
    "coarse_reviewed",
    "auto_labeled",
    "in_review",
    "verified",
    "materialized",
    "included_in_training",
]
AnnotationSource = Literal[
    "automatic",
    "human",
    "human_coarse",
    "human_edited",
    "legacy",
]


class LegacySchemaError(ValueError):
    """Raised when a v1 artifact is presented as v2 reviewed truth."""


class MediaReference(BaseModel):
    path: str
    media_type: Literal["video", "motion_csv", "audio", "audio_m4a"]
    sha256: str | None = None


class AudioCapture(BaseModel):
    enabled: bool = False
    permission: str | None = None
    path: str | None = None
    media_type: Literal["audio", "audio_m4a"] | None = None
    sha256: str | None = None
    purpose: Literal["vad_and_human_review_only"] | None = None
    shipping_model_eligible: bool = False
    safe_to_delete_after_finalization: bool = True
    purged_at: str | None = None


class ClockSync(BaseModel):
    """Mapping from motion timestamps to video-relative seconds."""

    time_domain: Literal["host_monotonic"] = "host_monotonic"
    video_start_host_time_s: float | None = None
    first_video_frame_host_time_s: float | None = None
    first_motion_host_time_s: float | None = None
    video_clock_origin_host_time_s: float
    motion_to_video_offset_s: float | None = None
    estimated_drift_ppm: float = 0.0
    drift_status: str | None = None
    method: Literal[
        "shared_host_clock",
        "first_video_frame",
        "manual_calibration",
        "legacy_first_motion_fallback",
        "capture_clock_fallback",
    ]
    confidence: Literal["high", "medium", "low"] = "high"

    @model_validator(mode="after")
    def validate_clock(self) -> "ClockSync":
        optional_times = (
            self.video_start_host_time_s,
            self.first_video_frame_host_time_s,
            self.first_motion_host_time_s,
            self.motion_to_video_offset_s,
        )
        values = [
            self.video_clock_origin_host_time_s,
            self.estimated_drift_ppm,
            *(value for value in optional_times if value is not None),
        ]
        if not all(np.isfinite(value) for value in values):
            raise ValueError("clock synchronization values must be finite")
        if (
            self.first_motion_host_time_s is not None
            and self.motion_to_video_offset_s is not None
            and not np.isclose(
                self.motion_to_video_offset_s,
                self.first_motion_host_time_s - self.video_clock_origin_host_time_s,
                rtol=0.0,
                atol=1e-6,
            )
        ):
            raise ValueError(
                "motion_to_video_offset_s must equal first_motion_host_time_s "
                "minus video_clock_origin_host_time_s"
            )
        return self


class CaptureMetadata(BaseModel):
    fps: float | None = None
    sensor_rate_hz: float | None = None
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    interrupted: bool = False
    interruption_count: int = 0
    motion_sample_gap_count: int = 0
    largest_motion_sample_gap_s: float = 0.0


class RecordingQuality(BaseModel):
    video_present: bool
    motion_present: bool
    review_audio_present: bool = False
    motion_samples: int = 0
    notes: list[str] = Field(default_factory=list)


class CoarseInterval(BaseModel):
    start_ts: float
    end_ts: float
    label: Literal["chewing", "not_chewing", "unknown"]
    source: AnnotationSource = "human"

    @model_validator(mode="after")
    def validate_bounds(self) -> "CoarseInterval":
        if not np.isfinite(self.start_ts) or not np.isfinite(self.end_ts):
            raise ValueError("coarse interval timestamps must be finite")
        if self.end_ts < self.start_ts:
            raise ValueError("coarse interval end_ts must be >= start_ts")
        return self


class SessionManifest(BaseModel):
    schema_version: Literal["2.0.0"] = BUNDLE_SCHEMA_VERSION
    session_id: str
    wearer_id: str
    source_app: Literal["collect", "review", "live", "legacy_import"]
    source_app_version: str = "unknown"
    activity: str = "unknown"
    status: SessionStatus = "collected"
    created_at: str
    updated_at: str
    video: MediaReference | None = None
    audio: AudioCapture | None = None
    motion: MediaReference
    clock_sync: ClockSync
    capture: CaptureMetadata = Field(default_factory=CaptureMetadata)
    quality: RecordingQuality
    active_model_ids: dict[str, str] = Field(default_factory=dict)
    annotation_revision: int = 0
    legacy_review_required: bool = False


class TrainingManifest(BaseModel):
    schema_version: Literal["2.0.0"] = BUNDLE_SCHEMA_VERSION
    kind: Literal["computer_vision", "motion"]
    session_id: str
    wearer_id: str
    candidate_revision: int
    candidate_sha256: str
    review_revision: int
    review_sha256: str
    generated_at: str
    verified: bool
    model_ids: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, str]
    targets: dict[str, str]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write_json(path: Path, value: BaseModel | dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_exclusive(path: Path, value: BaseModel | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise FileExistsError(f"immutable artifact already exists: {path}") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _find_media(session_dir: Path, extensions: tuple[str, ...]) -> Path | None:
    for extension in extensions:
        matches = sorted(session_dir.glob(extension))
        if matches:
            return matches[0]
    return None


def _find_motion_csv(session_dir: Path) -> Path | None:
    preferred = (
        session_dir / f"{session_dir.name}.csv",
        session_dir / "motion.csv",
        session_dir / "labeled_motion.csv",
    )
    candidates = [*preferred, *sorted(session_dir.glob("*.csv"))]
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            columns = set(pd.read_csv(path, nrows=1).columns)
        except (OSError, pd.errors.ParserError, UnicodeDecodeError):
            continue
        if {"ax", "ay", "az"} <= columns and any(
            name in columns for name in ("timestamp", "time", "ts")
        ):
            return path
    return None


def _timestamp_column(df: pd.DataFrame) -> str:
    for name in ("timestamp", "time", "ts"):
        if name in df.columns:
            return name
    raise ValueError("motion CSV has no timestamp column")


def _bool_labels(series: pd.Series) -> np.ndarray:
    normalized = series.astype(str).str.strip().str.lower()
    return normalized.isin({"1", "true", "yes", "chewing", "chew"}).to_numpy()


def coarse_intervals_from_motion(motion_csv: Path, offset_s: float) -> list[CoarseInterval]:
    """Materialize legacy row labels using the manifest's video-clock origin."""
    df = pd.read_csv(motion_csv)
    if "label" not in df.columns or df.empty:
        return []
    ts_col = _timestamp_column(df)
    video_ts = df[ts_col].astype(float).to_numpy() - float(offset_s)
    labels = _bool_labels(df["label"])
    intervals: list[CoarseInterval] = []
    start = 0
    for index in range(1, len(labels) + 1):
        if index < len(labels) and labels[index] == labels[start]:
            continue
        intervals.append(
            CoarseInterval(
                start_ts=max(0.0, float(video_ts[start])),
                end_ts=max(0.0, float(video_ts[index - 1])),
                label="chewing" if labels[start] else "not_chewing",
                source="legacy",
            )
        )
        start = index
    return intervals


def migrate_legacy_session(
    session_dir: str | Path,
    *,
    wearer_id: str = "local-user",
    source_app: Literal["collect", "live", "legacy_import"] = "legacy_import",
    source_app_version: str = "unknown",
    activity: str | None = None,
    force: bool = False,
) -> SessionManifest:
    """Explicitly wrap a legacy recording in a v2 bundle.

    Legacy event/phase/food-entry labels are not migrated.  The generated
    manifest remains ``legacy_review_required`` until a fresh v2 review is
    finalized.
    """
    session_dir = Path(session_dir)
    manifest_path = session_dir / "session.json"
    if manifest_path.exists() and not force:
        payload = _read_json(manifest_path)
        if payload.get("schema_version") != BUNDLE_SCHEMA_VERSION:
            raise LegacySchemaError(
                "legacy session.json is read-only; rerun migrate-session --force "
                "to create a v2 wrapper, then review from fresh candidates"
            )
        return SessionManifest.model_validate(payload)

    motion = _find_motion_csv(session_dir)
    if motion is None:
        raise FileNotFoundError(f"no motion CSV in {session_dir}")
    video = _find_media(session_dir, ("*.mov", "*.MOV", "*.mp4", "*.MP4"))
    motion_df = pd.read_csv(motion)
    ts_col = _timestamp_column(motion_df)
    timestamps = motion_df[ts_col].astype(float).to_numpy()
    if timestamps.size == 0:
        raise ValueError(f"motion CSV is empty: {motion}")
    if not np.all(np.isfinite(timestamps)):
        raise ValueError("legacy motion timestamps must be finite")
    source_motion = motion
    removed_nonmonotonic = 0
    if timestamps.size > 1 and np.any(np.diff(timestamps) <= 0):
        differences = np.diff(timestamps)
        bad = differences[differences <= 0]
        # Old AirPods exports occasionally repeated or stepped backward by one
        # 20 ms sample. Recover that narrow, auditable case into a new v2 CSV;
        # never mutate the original and never repair a clock reset.
        max_recoverable_rows = max(3, int(np.ceil(len(differences) * 0.01)))
        if len(bad) > max_recoverable_rows or float(np.min(bad)) < -0.1:
            raise ValueError(
                "legacy motion has a clock reset or excessive non-monotonic rows; "
                "preserve it for manual recovery"
            )
        keep = np.zeros(len(timestamps), dtype=bool)
        last = float("-inf")
        for index, timestamp in enumerate(timestamps):
            if float(timestamp) > last:
                keep[index] = True
                last = float(timestamp)
        removed_nonmonotonic = int((~keep).sum())
        motion_df = motion_df.loc[keep].reset_index(drop=True)
        motion = session_dir / "motion-v2.csv"
        motion_df.to_csv(motion, index=False)
        timestamps = motion_df[ts_col].astype(float).to_numpy()
        if timestamps.size < 2 or np.any(np.diff(timestamps) <= 0):
            raise ValueError("legacy motion sanitation did not produce a monotonic clock")
    offset = float(timestamps[0])
    dt = np.diff(timestamps)
    dt = dt[(dt > 0) & np.isfinite(dt)]
    sensor_rate = float(1.0 / np.median(dt)) if dt.size else None
    median_dt = float(np.median(dt)) if dt.size else 0.0
    gaps = dt[dt > median_dt * 1.5] if median_dt > 0 else np.zeros(0)
    duration = float(timestamps[-1] - timestamps[0]) if timestamps.size > 1 else 0.0
    created = datetime.fromtimestamp(session_dir.stat().st_mtime, timezone.utc).isoformat()
    lower_name = session_dir.name.lower()
    if lower_name.startswith("eating"):
        named_activity = "eating"
    elif lower_name.startswith(("not-eating", "noteating")):
        named_activity = "not_eating"
    else:
        # An unlabeled legacy folder must never become a negative merely
        # because its name does not start with "Eating".
        named_activity = "unknown"
    inferred_activity = activity or named_activity

    manifest = SessionManifest(
        session_id=session_dir.name,
        wearer_id=wearer_id,
        source_app=source_app,
        source_app_version=source_app_version,
        activity=inferred_activity,
        status="coarse_labeled" if "label" in motion_df.columns else "collected",
        created_at=created,
        updated_at=utc_now(),
        video=(
            MediaReference(path=video.name, media_type="video", sha256=file_sha256(video))
            if video is not None
            else None
        ),
        motion=MediaReference(
            path=motion.name,
            media_type="motion_csv",
            sha256=file_sha256(motion),
        ),
        clock_sync=ClockSync(
            first_motion_host_time_s=offset,
            video_clock_origin_host_time_s=offset,
            # The fallback declares the first motion sample to be video zero,
            # so its *relative* latency is zero.  The absolute host timestamp
            # remains available in both fields above.
            motion_to_video_offset_s=0.0,
            method="legacy_first_motion_fallback",
            confidence="low",
        ),
        capture=CaptureMetadata(
            sensor_rate_hz=sensor_rate,
            duration_s=duration,
            motion_sample_gap_count=int(len(gaps)),
            largest_motion_sample_gap_s=float(np.max(gaps)) if len(gaps) else 0.0,
        ),
        quality=RecordingQuality(
            video_present=video is not None,
            motion_present=True,
            motion_samples=int(len(motion_df)),
            notes=[
                "Legacy timing uses the first motion row as video zero; verify alignment.",
                "Legacy event labels are historical only; fresh v2 review is required.",
                *(
                    [
                        f"Removed {removed_nonmonotonic} repeated/backward motion rows into "
                        f"{motion.name}; original preserved as {source_motion.name}."
                    ]
                    if removed_nonmonotonic
                    else []
                ),
            ],
        ),
        legacy_review_required=True,
    )
    _write_json(manifest_path, manifest)
    if "label" in motion_df.columns:
        intervals = coarse_intervals_from_motion(
            motion, manifest.clock_sync.video_clock_origin_host_time_s
        )
        _write_json(
            session_dir / "annotations" / "coarse_labels.json",
            {
                "schema_version": ANNOTATION_SCHEMA_VERSION,
                "artifact_type": "coarse_labels",
                "session_id": manifest.session_id,
                "intervals": [item.model_dump(mode="json") for item in intervals],
            },
        )
    return manifest


def load_session_manifest(session_dir: str | Path) -> SessionManifest:
    path = Path(session_dir) / "session.json"
    if not path.exists():
        raise FileNotFoundError(
            f"missing {path}; run chewsense-vision migrate-session explicitly"
        )
    payload = _read_json(path)
    if payload.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise LegacySchemaError(
            f"session schema {payload.get('schema_version')!r} is read-only; "
            "run explicit migration and fresh review"
        )
    return SessionManifest.model_validate(payload)


def save_session_manifest(session_dir: str | Path, manifest: SessionManifest) -> Path:
    path = Path(session_dir) / "session.json"
    manifest.updated_at = utc_now()
    _write_json(path, manifest)
    return path


def load_coarse_intervals(session_dir: str | Path) -> list[CoarseInterval] | None:
    """Return ``None`` for missing review, distinct from explicit all-negative."""
    path = Path(session_dir) / "annotations" / "coarse_labels.json"
    if not path.exists():
        return None
    payload = _read_json(path)
    if payload.get("schema_version") != ANNOTATION_SCHEMA_VERSION:
        raise LegacySchemaError("coarse labels must be explicitly migrated to schema 2.0.0")
    artifact_type = payload.get("artifact_type")
    if artifact_type != "coarse_labels":
        raise ValueError(f"unexpected coarse-label artifact_type: {artifact_type!r}")
    manifest = load_session_manifest(session_dir)
    if payload.get("session_id") != manifest.session_id:
        raise ValueError("coarse-label session_id does not match session manifest")
    raw_intervals = payload.get("intervals")
    if not isinstance(raw_intervals, list):
        raise ValueError("coarse-label intervals must be a list")
    intervals = [CoarseInterval.model_validate(item) for item in raw_intervals]
    previous_end: float | None = None
    for interval in intervals:
        if previous_end is not None and interval.start_ts < previous_end:
            raise ValueError("coarse-label intervals must be ordered and non-overlapping")
        previous_end = interval.end_ts
    return intervals


def save_auto_predictions(
    session_dir: str | Path,
    events: VideoEvents,
    *,
    model_ids: dict[str, str] | None = None,
) -> Path:
    """Persist mutable detector telemetry, never canonical human truth."""
    session_dir = Path(session_dir)
    path = session_dir / "annotations" / "automatic_telemetry.json"
    _write_json(
        path,
        {
            "artifact_type": "automatic_telemetry",
            "source_schema_version": events.schema_version,
            "provenance": "automatic",
            "events": events.model_dump(mode="json"),
        },
    )
    manifest = load_session_manifest(session_dir)
    if model_ids:
        manifest.active_model_ids.update(model_ids)
    manifest.status = "auto_labeled"
    save_session_manifest(session_dir, manifest)
    return path


def create_candidates_from_events(
    session_dir: str | Path,
    derived_dir: str | Path,
    events: VideoEvents,
    *,
    generated_at: str | None = None,
) -> tuple[Path, CandidateArtifact]:
    """Create one immutable candidate revision from the fused mouth-cycle path."""
    session_dir = Path(session_dir)
    manifest = load_session_manifest(session_dir)
    coarse = load_coarse_intervals(session_dir)
    food_entries = list(events.food_entry_proposals)
    model_versions = dict(manifest.active_model_ids)
    if not model_versions:
        from . import __version__

        model_versions = {
            "vision_pipeline": f"chewsense-{__version__}",
            "automatic_events_schema": events.schema_version,
        }
    return write_candidate_artifact(
        derived_dir,
        session_id=manifest.session_id,
        chew_hypotheses=events.mouth_cycles,
        food_entry_hypotheses=food_entries,
        coarse_intervals=coarse,
        model_versions=model_versions,
        generated_at=generated_at,
        debug_telemetry={
            "source_events_schema": events.schema_version,
            "source_chew_stream": "fused_mouth_cycles",
            "automatic_mouth_states_are_telemetry": True,
        },
    )


def _candidate_path(derived_dir: Path, revision: int) -> Path:
    return derived_dir / f"candidates-v{revision:04d}.json"


def _load_review_for_pointer(
    session_dir: Path, pointer: CanonicalPointer
) -> ReviewRevision:
    payload = _read_json(session_dir / pointer.review_path)
    return ReviewRevision.model_validate(payload)


def finalize_session(
    session_dir: str | Path,
    derived_dir: str | Path,
    *,
    review_draft_path: str | Path | None = None,
) -> MaterializedAnnotations:
    """Freeze a v2 review revision and materialize derived training targets."""
    session_dir = Path(session_dir)
    derived_dir = Path(derived_dir)
    manifest = load_session_manifest(session_dir)
    annotations_dir = session_dir / "annotations"
    draft_path = Path(review_draft_path) if review_draft_path else annotations_dir / "review-draft.json"
    if not draft_path.exists():
        if (session_dir / "review_overrides.json").exists():
            raise LegacySchemaError(
                "review_overrides.json is a v1 historical artifact; create a fresh v2 "
                "annotations/review-draft.json from immutable candidates"
            )
        raise FileNotFoundError(f"missing review draft: {draft_path}")
    review = load_review_draft(draft_path)
    candidates_path = _candidate_path(derived_dir, review.candidate_revision)
    if not candidates_path.exists():
        raise FileNotFoundError(f"missing exact candidate revision: {candidates_path}")
    candidates = load_candidate_artifact(candidates_path)

    complete = review_complete(candidates, review)
    if candidates.requires_full_coarse_review:
        raise ValueError(
            "candidate revision was generated without complete coarse labels; "
            "finish the full coarse review, generate a new immutable candidate "
            "revision, and reopen that exact revision before finalizing"
        )
    if not complete:
        raise ValueError(
            "review is incomplete: decide every candidate, resolve coarse-label coverage, "
            "complete the full-video pass, and mark the draft verified"
        )

    # Build the would-be immutable revision in memory first.  Overlaps, bounds,
    # stale lineage, and motion-clock failures must not leave behind a frozen
    # review that was never successfully materialized.
    review_path, frozen_review = prepare_review_revision(annotations_dir, review)
    materialized = materialize_annotations(candidates, frozen_review)
    materialized_path = annotations_dir / f"materialized-v{frozen_review.review_revision:04d}.json"

    coarse = load_coarse_intervals(session_dir) or []
    motion_path = session_dir / manifest.motion.path
    duration = manifest.capture.duration_s
    if duration is None:
        motion = pd.read_csv(motion_path)
        ts_col = _timestamp_column(motion)
        duration = float(
            motion[ts_col].astype(float).iloc[-1]
            - manifest.clock_sync.video_clock_origin_host_time_s
        )
    aligned = materialize_motion_rows(
        motion_path,
        materialized,
        candidates=candidates,
        time_offset=manifest.clock_sync.video_clock_origin_host_time_s,
        duration_s=float(duration),
        coarse_intervals=coarse,
    )
    motion_labels_path = annotations_dir / f"motion-labels-v{frozen_review.review_revision:04d}.csv"

    preflight = audit_artifacts(
        session_id=manifest.session_id,
        candidates=candidates,
        review=frozen_review,
        materialized=materialized,
        duration_s=float(duration),
    )
    if not preflight.ok:
        details = "; ".join(f"{item.code}: {item.message}" for item in preflight.errors)
        raise ValueError(f"session cannot be finalized: {details}")
    for immutable_path in (review_path, materialized_path):
        if immutable_path.exists():
            raise FileExistsError(f"immutable artifact already exists: {immutable_path}")

    _write_json_exclusive(review_path, frozen_review)
    _write_json_exclusive(materialized_path, materialized)
    aligned.to_csv(motion_labels_path, index=False)

    pointer = CanonicalPointer(
        session_id=manifest.session_id,
        selected_review_revision=frozen_review.review_revision,
        selected_review_sha256=frozen_review.content_sha256,
        candidate_revision=candidates.candidate_revision,
        candidate_sha256=candidates.content_sha256,
        review_path=str(review_path.relative_to(session_dir)),
        materialized_path=str(materialized_path.relative_to(session_dir)),
        materialized_sha256=materialized.content_sha256,
    )
    # This is intentionally the only mutable canonical file, and it is only a pointer.
    _write_json(annotations_dir / "canonical_events.json", pointer)

    manifests_dir = session_dir / "manifests"
    common = {
        "session_id": manifest.session_id,
        "wearer_id": manifest.wearer_id,
        "candidate_revision": candidates.candidate_revision,
        "candidate_sha256": candidates.content_sha256,
        "review_revision": frozen_review.review_revision,
        "review_sha256": frozen_review.content_sha256,
        "generated_at": utc_now(),
        "verified": complete,
        "model_ids": candidates.model_versions,
    }
    cv_manifest = TrainingManifest(
        kind="computer_vision",
        **common,
        inputs={
            "video": manifest.video.path if manifest.video else "",
            "session": "session.json",
            "candidates": str(candidates_path),
        },
        targets={
            "review": str(review_path.relative_to(session_dir)),
            "materialized": str(materialized_path.relative_to(session_dir)),
            "canonical_pointer": "annotations/canonical_events.json",
        },
    )
    motion_manifest = TrainingManifest(
        kind="motion",
        **common,
        inputs={"motion": manifest.motion.path, "session": "session.json"},
        targets={
            "motion_labels": str(motion_labels_path.relative_to(session_dir)),
            "materialized": str(materialized_path.relative_to(session_dir)),
            "canonical_pointer": "annotations/canonical_events.json",
        },
    )
    _write_json(manifests_dir / "cv_training.json", cv_manifest)
    _write_json(manifests_dir / "motion_training.json", motion_manifest)

    manifest.annotation_revision = frozen_review.review_revision
    manifest.status = "materialized" if complete else "in_review"
    if complete:
        manifest.legacy_review_required = False
    save_session_manifest(session_dir, manifest)
    return materialized


def purge_review_audio(
    session_dir: str | Path,
    derived_dir: str | Path,
) -> Path:
    """Remove a review-only audio sidecar after verified finalization."""

    session_dir = Path(session_dir)
    derived_dir = Path(derived_dir)
    manifest = load_session_manifest(session_dir)
    audio = manifest.audio
    if audio is None or not audio.enabled or not audio.path:
        raise ValueError("session has no enabled review-audio sidecar to purge")
    if not audio.safe_to_delete_after_finalization:
        raise ValueError("session manifest does not permit review-audio deletion")
    pointer = session_dir / "annotations" / "canonical_events.json"
    if (
        manifest.status != "materialized"
        or manifest.annotation_revision < 1
        or not pointer.exists()
    ):
        raise ValueError("review audio may be purged only after verified finalization")

    report = audit_session(session_dir, derived_dir)
    if not report.ok:
        details = "; ".join(f"{item.code}: {item.message}" for item in report.errors)
        raise ValueError(f"session must pass audit before audio purge: {details}")

    audio_path = (session_dir / audio.path).resolve()
    try:
        audio_path.relative_to(session_dir.resolve())
    except ValueError as exc:
        raise ValueError("review-audio path escapes the canonical session") from exc
    if not audio_path.is_file():
        raise FileNotFoundError(f"review-audio sidecar is missing: {audio_path}")

    audio.enabled = False
    audio.path = None
    audio.media_type = None
    audio.sha256 = None
    audio.purged_at = utc_now()
    manifest.quality.review_audio_present = False
    manifest.quality.notes.append("Review-only audio purged after verified finalization.")
    save_session_manifest(session_dir, manifest)
    audio_path.unlink()
    return audio_path


def audit_session(
    session_dir: str | Path,
    derived_dir: str | Path | None = None,
) -> AuditReport:
    """Read-only validation of clocks, hashes, lineage, IDs, and bounds."""
    session_dir = Path(session_dir)
    derived = Path(derived_dir) if derived_dir is not None else None
    session_id = session_dir.name
    errors: list[AuditIssue] = []
    warnings: list[AuditIssue] = []

    def error(code: str, message: str, path: Path | None = None) -> None:
        errors.append(
            AuditIssue(code=code, message=message, path=str(path) if path else None)
        )

    try:
        manifest = load_session_manifest(session_dir)
        session_id = manifest.session_id
    except Exception as exc:  # noqa: BLE001
        return AuditReport(
            session_id=session_id,
            ok=False,
            errors=(AuditIssue(code="manifest", message=str(exc)),),
        )
    if manifest.session_id != session_dir.name:
        warnings.append(
            AuditIssue(
                code="session_folder",
                message=(
                    f"manifest session_id {manifest.session_id!r} does not match folder "
                    f"{session_dir.name!r}; artifact lineage still uses the manifest ID"
                ),
            )
        )

    references: list[MediaReference] = [manifest.motion]
    if manifest.video is not None:
        references.append(manifest.video)
    if manifest.audio is not None and manifest.audio.enabled and manifest.audio.path:
        references.append(
            MediaReference(
                path=manifest.audio.path,
                media_type=manifest.audio.media_type or "audio",
                sha256=manifest.audio.sha256,
            )
        )
    for reference in references:
        path = session_dir / reference.path
        if not path.exists():
            error("missing_media", f"missing {reference.media_type}: {reference.path}", path)
        elif reference.sha256 and file_sha256(path) != reference.sha256:
            error("media_hash", f"SHA-256 mismatch for {reference.path}", path)
        elif not reference.sha256:
            warnings.append(
                AuditIssue(
                    code="media_hash_missing",
                    message=f"no recorded SHA-256 for {reference.path}",
                    path=str(path),
                )
            )

    motion_path = session_dir / manifest.motion.path
    if motion_path.exists():
        try:
            motion = pd.read_csv(motion_path)
            ts_col = _timestamp_column(motion)
            ts = motion[ts_col].astype(float).to_numpy()
            if not np.all(np.isfinite(ts)):
                error("motion_clock", "motion timestamps must be finite", motion_path)
            elif ts.size > 1 and np.any(np.diff(ts) <= 0):
                error("motion_clock", "motion timestamps are not strictly monotonic", motion_path)
            elif ts.size:
                video_ts = ts - manifest.clock_sync.video_clock_origin_host_time_s
                if video_ts[0] < -1e-6:
                    warnings.append(
                        AuditIssue(
                            code="motion_preroll",
                            message="motion begins before the declared video-clock origin; pre-roll rows will be masked",
                            path=str(motion_path),
                        )
                    )
                if (
                    manifest.capture.duration_s is not None
                    and video_ts[-1] > manifest.capture.duration_s + 1e-6
                ):
                    warnings.append(
                        AuditIssue(
                            code="capture_duration",
                            message=(
                                "motion extends beyond capture.duration_s; trailing rows will be masked "
                                "unless the manifest duration is corrected"
                            ),
                            path=str(motion_path),
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            error("motion_read", str(exc), motion_path)

    pointer_path = session_dir / "annotations" / "canonical_events.json"
    draft_path = session_dir / "annotations" / "review-draft.json"
    pointer: CanonicalPointer | None = None
    review: ReviewDraft | ReviewRevision | None = None
    materialized: MaterializedAnnotations | None = None
    candidates: CandidateArtifact | None = None

    try:
        coarse = load_coarse_intervals(session_dir)
        if coarse is not None:
            duration = manifest.capture.duration_s
            previous_end: float | None = None
            for interval in coarse:
                if interval.start_ts < -1e-6 or (
                    duration is not None and interval.end_ts > duration + 1e-6
                ):
                    error("coarse_bounds", "coarse interval is outside video bounds")
                if previous_end is not None and interval.start_ts < previous_end:
                    error("coarse_overlap", "coarse intervals overlap")
                previous_end = interval.end_ts
    except Exception as exc:  # noqa: BLE001
        error("coarse_labels", str(exc))

    try:
        if pointer_path.exists():
            pointer = CanonicalPointer.model_validate(_read_json(pointer_path))
            review = _load_review_for_pointer(session_dir, pointer)
            materialized = MaterializedAnnotations.model_validate(
                _read_json(session_dir / pointer.materialized_path)
            )
            revision = pointer.candidate_revision
        elif draft_path.exists():
            review = load_review_draft(draft_path)
            revision = review.candidate_revision
        else:
            review = None
            if derived is None:
                revision = 0
            else:
                paths = sorted(derived.glob("candidates-v*.json"))
                revision = int(paths[-1].stem.removeprefix("candidates-v")) if paths else 0
        if revision:
            if derived is None:
                error("derived_required", "--derived is required to audit candidate lineage")
            else:
                candidates = load_candidate_artifact(_candidate_path(derived, revision))
    except Exception as exc:  # noqa: BLE001
        error("artifact_load", str(exc))

    if pointer is not None and review is not None and materialized is not None:
        if pointer.session_id != manifest.session_id:
            error("pointer_lineage", "canonical pointer session_id mismatch")
        if not isinstance(review, ReviewRevision):
            error("pointer_lineage", "canonical pointer does not reference frozen review")
        else:
            if pointer.selected_review_revision != review.review_revision:
                error("pointer_lineage", "canonical pointer review revision mismatch")
            if pointer.selected_review_sha256 != review.content_sha256:
                error("pointer_lineage", "canonical pointer review hash mismatch")
            if candidates is not None:
                try:
                    if not review_complete(candidates, review):
                        error("pointer_lineage", "canonical pointer selects an incomplete review")
                except ValueError as exc:
                    error("pointer_lineage", f"canonical pointer review is invalid: {exc}")
        if pointer.candidate_revision != materialized.candidate_revision:
            error("pointer_lineage", "canonical pointer candidate revision mismatch")
        if pointer.materialized_sha256 != materialized.content_sha256:
            error("pointer_lineage", "canonical pointer materialized hash mismatch")
        if pointer.candidate_sha256 != materialized.candidate_sha256:
            error("pointer_lineage", "canonical pointer candidate hash mismatch")
        if materialized.review_revision != pointer.selected_review_revision:
            error("pointer_lineage", "materialized review revision differs from pointer")
        if materialized.review_sha256 != pointer.selected_review_sha256:
            error("pointer_lineage", "materialized review hash differs from pointer")
        if candidates is not None:
            if pointer.candidate_revision != candidates.candidate_revision:
                error("pointer_lineage", "canonical pointer candidate revision differs from artifact")
            if pointer.candidate_sha256 != candidates.content_sha256:
                error("pointer_lineage", "canonical pointer candidate hash differs from artifact")

    if candidates is not None:
        report = audit_artifacts(
            session_id=manifest.session_id,
            candidates=candidates,
            review=review,
            materialized=materialized,
            duration_s=manifest.capture.duration_s,
        )
        errors.extend(report.errors)
        warnings.extend(report.warnings)
    elif derived is not None:
        error("missing_candidates", "no candidate artifact found")

    return AuditReport(
        session_id=manifest.session_id,
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def migrate_tree(
    input_root: str | Path,
    *,
    wearer_id: str = "local-user",
    force: bool = False,
) -> dict[str, Any]:
    input_root = Path(input_root)
    sessions = [path for path in sorted(input_root.iterdir()) if path.is_dir()]
    migrated: list[str] = []
    failed: list[dict[str, str]] = []
    for session in sessions:
        try:
            migrate_legacy_session(session, wearer_id=wearer_id, force=force)
            migrated.append(session.name)
        except Exception as exc:  # noqa: BLE001
            failed.append({"session_id": session.name, "error": str(exc)})
    return {"migrated": migrated, "failed": failed}


def copy_annotation_artifacts(source: Path, destination: Path) -> list[str]:
    """Copy portable telemetry only; never promote legacy truth implicitly."""
    copied: list[str] = []
    for name in ("review_feedback.csv", "face_presence.json"):
        src = source / name
        if src.exists():
            shutil.copy2(src, destination / name)
            copied.append(name)
    return copied


# Compatibility name for callers that only need the finalized return type.
CanonicalAnnotations = MaterializedAnnotations


__all__ = [
    "ANNOTATION_SCHEMA_VERSION",
    "BUNDLE_SCHEMA_VERSION",
    "CanonicalAnnotations",
    "ClockSync",
    "CoarseInterval",
    "LegacySchemaError",
    "MediaReference",
    "SessionManifest",
    "TrainingManifest",
    "audit_session",
    "coarse_intervals_from_motion",
    "copy_annotation_artifacts",
    "create_candidates_from_events",
    "finalize_session",
    "load_coarse_intervals",
    "load_session_manifest",
    "migrate_legacy_session",
    "migrate_tree",
    "purge_review_audio",
    "save_auto_predictions",
    "save_session_manifest",
]
