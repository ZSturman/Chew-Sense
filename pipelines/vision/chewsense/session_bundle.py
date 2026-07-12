"""Canonical ChewSense session bundle contract and legacy migration helpers."""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from .events.schema import VideoEvents
from .integrations.airpods_bridge import align_events_to_motion


BUNDLE_SCHEMA_VERSION = "1.0.0"
ANNOTATION_SCHEMA_VERSION = "1.0.0"

SessionStatus = Literal[
    "collected",
    "coarse_labeled",
    "auto_labeled",
    "in_review",
    "verified",
    "materialized",
    "included_in_training",
]
MouthState = Literal["closed", "opening", "open", "closing", "unknown"]
ReviewDecision = Literal["accepted", "rejected", "edited", "added"]
AnnotationSource = Literal["automatic", "human", "human_edited", "legacy"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MediaReference(BaseModel):
    path: str
    media_type: Literal["video", "motion_csv"]
    sha256: str | None = None


class ClockSync(BaseModel):
    """Mapping from motion timestamps to video-relative seconds."""

    time_domain: Literal["host_monotonic"] = "host_monotonic"
    video_start_host_time_s: float | None = None
    first_video_frame_host_time_s: float | None = None
    first_motion_host_time_s: float | None = None
    motion_to_video_offset_s: float
    estimated_drift_ppm: float = 0.0
    method: Literal[
        "shared_host_clock",
        "first_video_frame",
        "manual_calibration",
        "legacy_first_motion_fallback",
    ]
    confidence: Literal["high", "medium", "low"] = "high"


class CaptureMetadata(BaseModel):
    fps: float | None = None
    sensor_rate_hz: float | None = None
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    interrupted: bool = False


class RecordingQuality(BaseModel):
    video_present: bool
    motion_present: bool
    motion_samples: int = 0
    notes: list[str] = Field(default_factory=list)


class CoarseInterval(BaseModel):
    start_ts: float
    end_ts: float
    label: Literal["chewing", "not_chewing", "unknown"]
    source: AnnotationSource = "human"

    @model_validator(mode="after")
    def validate_bounds(self) -> "CoarseInterval":
        if self.end_ts < self.start_ts:
            raise ValueError("coarse interval end_ts must be >= start_ts")
        return self


class SessionManifest(BaseModel):
    schema_version: str = BUNDLE_SCHEMA_VERSION
    session_id: str
    wearer_id: str
    source_app: Literal["collect", "review", "live", "legacy_import"]
    source_app_version: str = "unknown"
    activity: str = "unknown"
    status: SessionStatus = "collected"
    created_at: str
    updated_at: str
    video: MediaReference | None = None
    motion: MediaReference
    clock_sync: ClockSync
    capture: CaptureMetadata = Field(default_factory=CaptureMetadata)
    quality: RecordingQuality
    active_model_ids: dict[str, str] = Field(default_factory=dict)
    annotation_revision: int = 0


class MouthStateInterval(BaseModel):
    start_ts: float
    end_ts: float
    state: MouthState
    confidence: float = 1.0


class ChewCycleAnnotation(BaseModel):
    id: str
    start_ts: float
    peak_ts: float
    end_ts: float
    confidence: float = 1.0
    decision: ReviewDecision
    source: AnnotationSource
    bout_id: str | None = None

    @model_validator(mode="after")
    def validate_cycle(self) -> "ChewCycleAnnotation":
        if not self.start_ts < self.peak_ts < self.end_ts:
            raise ValueError("chew cycle must satisfy start_ts < peak_ts < end_ts")
        return self


class BiteEventAnnotation(BaseModel):
    id: str
    ts: float
    confidence: float = 1.0
    decision: ReviewDecision
    source: AnnotationSource
    bout_id: str | None = None


class ChewingBoutAnnotation(BaseModel):
    id: str
    start_ts: float
    end_ts: float
    confidence: float = 1.0
    bite_event_ids: list[str] = Field(default_factory=list)
    chew_cycle_ids: list[str] = Field(default_factory=list)


class CanonicalAnnotations(BaseModel):
    schema_version: str = ANNOTATION_SCHEMA_VERSION
    session_id: str
    revision: int
    parent_revision: int | None = None
    generated_at: str
    reviewer: str | None = None
    verified: bool
    full_video_pass_completed: bool
    model_ids: dict[str, str] = Field(default_factory=dict)
    coarse_intervals: list[CoarseInterval] = Field(default_factory=list)
    mouth_states: list[MouthStateInterval] = Field(default_factory=list)
    chew_cycles: list[ChewCycleAnnotation] = Field(default_factory=list)
    bite_events: list[BiteEventAnnotation] = Field(default_factory=list)
    chewing_bouts: list[ChewingBoutAnnotation] = Field(default_factory=list)
    rejected_chew_ids: list[str] = Field(default_factory=list)
    rejected_bite_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


class TrainingManifest(BaseModel):
    schema_version: str = BUNDLE_SCHEMA_VERSION
    kind: Literal["computer_vision", "motion"]
    session_id: str
    wearer_id: str
    annotation_revision: int
    generated_at: str
    verified: bool
    model_ids: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, str]
    targets: dict[str, str]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write_json(path: Path, value: BaseModel | dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _find_media(session_dir: Path, extensions: tuple[str, ...]) -> Path | None:
    for extension in extensions:
        matches = sorted(session_dir.glob(extension))
        if matches:
            return matches[0]
    return None


def _find_motion_csv(session_dir: Path) -> Path | None:
    """Find the sensor table in raw or field-test bundles with many CSVs."""
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
    df = pd.read_csv(motion_csv)
    if "label" not in df.columns or df.empty:
        return []
    ts_col = _timestamp_column(df)
    video_ts = df[ts_col].astype(float).to_numpy() - offset_s
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
                source="human",
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
    """Create ``session.json`` and coarse labels without moving media."""
    session_dir = Path(session_dir)
    manifest_path = session_dir / "session.json"
    if manifest_path.exists() and not force:
        return SessionManifest.model_validate(_read_json(manifest_path))

    motion = _find_motion_csv(session_dir)
    if motion is None:
        raise FileNotFoundError(f"no motion CSV in {session_dir}")
    video = _find_media(session_dir, ("*.mov", "*.MOV", "*.mp4", "*.MP4"))
    motion_df = pd.read_csv(motion)
    ts_col = _timestamp_column(motion_df)
    timestamps = motion_df[ts_col].astype(float).to_numpy()
    if timestamps.size == 0:
        raise ValueError(f"motion CSV is empty: {motion}")
    offset = float(timestamps[0])
    dt = np.diff(timestamps)
    dt = dt[(dt > 0) & np.isfinite(dt)]
    sensor_rate = float(1.0 / np.median(dt)) if dt.size else None
    duration = float(timestamps[-1] - timestamps[0]) if timestamps.size > 1 else 0.0
    created = datetime.fromtimestamp(session_dir.stat().st_mtime, timezone.utc).isoformat()
    inferred_activity = activity or ("eating" if session_dir.name.lower().startswith("eating") else "not_eating")

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
            MediaReference(
                path=video.name,
                media_type="video",
                sha256=_sha256(video),
            )
            if video is not None
            else None
        ),
        motion=MediaReference(
            path=motion.name,
            media_type="motion_csv",
            sha256=_sha256(motion),
        ),
        clock_sync=ClockSync(
            first_motion_host_time_s=offset,
            motion_to_video_offset_s=offset,
            method="legacy_first_motion_fallback",
            confidence="low",
        ),
        capture=CaptureMetadata(
            sensor_rate_hz=sensor_rate,
            duration_s=duration,
        ),
        quality=RecordingQuality(
            video_present=video is not None,
            motion_present=True,
            motion_samples=int(len(motion_df)),
            notes=["Legacy timing uses the first motion row as video zero; verify alignment."],
        ),
    )
    _write_json(manifest_path, manifest)
    _write_json(
        session_dir / "annotations" / "coarse_labels.json",
        {
            "schema_version": ANNOTATION_SCHEMA_VERSION,
            "session_id": manifest.session_id,
            "intervals": [
                item.model_dump(mode="json")
                for item in coarse_intervals_from_motion(motion, offset)
            ],
        },
    )
    return manifest


def load_session_manifest(session_dir: str | Path) -> SessionManifest:
    session_dir = Path(session_dir)
    path = session_dir / "session.json"
    if not path.exists():
        return migrate_legacy_session(session_dir)
    return SessionManifest.model_validate(_read_json(path))


def save_session_manifest(session_dir: str | Path, manifest: SessionManifest) -> Path:
    path = Path(session_dir) / "session.json"
    manifest.updated_at = utc_now()
    _write_json(path, manifest)
    return path


def load_coarse_intervals(session_dir: str | Path) -> list[CoarseInterval]:
    payload = _read_json(Path(session_dir) / "annotations" / "coarse_labels.json")
    return [CoarseInterval.model_validate(item) for item in payload.get("intervals", [])]


def save_auto_predictions(
    session_dir: str | Path,
    events: VideoEvents,
    *,
    model_ids: dict[str, str] | None = None,
) -> Path:
    session_dir = Path(session_dir)
    path = session_dir / "annotations" / "auto_predictions.json"
    _write_json(path, events.model_dump(mode="json"))
    manifest = load_session_manifest(session_dir)
    if model_ids:
        manifest.active_model_ids.update(model_ids)
    manifest.status = "auto_labeled"
    save_session_manifest(session_dir, manifest)
    return path


def _load_events(derived_dir: Path) -> VideoEvents:
    path = derived_dir / "events.json"
    if not path.exists():
        raise FileNotFoundError(f"missing automatic predictions: {path}")
    return VideoEvents.model_validate(_read_json(path))


def _next_revision(annotations_dir: Path) -> int:
    revisions: list[int] = []
    for path in annotations_dir.glob("review-v*.json"):
        try:
            revisions.append(int(path.stem.removeprefix("review-v")))
        except ValueError:
            continue
    return max(revisions, default=0) + 1


def _chew_annotations(events: VideoEvents, review: dict) -> tuple[list[ChewCycleAnnotation], list[str]]:
    base = {chew.id: (chew, episode.id) for episode in events.episodes for chew in episode.chews}
    deleted = set(review.get("deleted_chew_ids", []))
    verified = set(review.get("verified_chew_ids", []))
    legacy_accept_all = bool(review.get("verified")) and not verified
    edited = {item["id"]: item for item in review.get("edited_chews", [])}
    added = {item["id"]: item for item in review.get("added_chews", [])}
    accepted: list[ChewCycleAnnotation] = []
    for chew_id, (chew, bout_id) in base.items():
        if chew_id in deleted:
            continue
        if chew_id in edited:
            item = edited[chew_id]
            accepted.append(
                ChewCycleAnnotation(
                    **{key: item[key] for key in ("id", "start_ts", "peak_ts", "end_ts")},
                    confidence=float(item.get("confidence", 1.0)),
                    decision="edited",
                    source="human_edited",
                    bout_id=bout_id,
                )
            )
        else:
            accepted.append(
                ChewCycleAnnotation(
                    id=chew.id,
                    start_ts=chew.start_ts,
                    peak_ts=chew.peak_ts,
                    end_ts=chew.end_ts,
                    confidence=chew.confidence,
                    decision="accepted",
                    source="human" if chew_id in verified or legacy_accept_all else "automatic",
                    bout_id=bout_id,
                )
            )
    for item in added.values():
        accepted.append(
            ChewCycleAnnotation(
                **{key: item[key] for key in ("id", "start_ts", "peak_ts", "end_ts")},
                confidence=float(item.get("confidence", 1.0)),
                decision="added",
                source="human",
            )
        )
    return sorted(accepted, key=lambda item: item.start_ts), sorted(deleted)


def _bite_annotations(events: VideoEvents, review: dict) -> tuple[list[BiteEventAnnotation], list[str]]:
    base = {bite.id: (bite, episode.id) for episode in events.episodes for bite in episode.bites}
    deleted = set(review.get("deleted_bite_ids", []))
    verified = set(review.get("verified_bite_ids", []))
    legacy_accept_all = bool(review.get("verified")) and not verified
    edited = {item["id"]: item for item in review.get("edited_bites", [])}
    added = {item["id"]: item for item in review.get("added_bites", [])}
    accepted: list[BiteEventAnnotation] = []
    for bite_id, (bite, bout_id) in base.items():
        if bite_id in deleted:
            continue
        if bite_id in edited:
            item = edited[bite_id]
            accepted.append(
                BiteEventAnnotation(
                    id=bite_id,
                    ts=float(item["ts"]),
                    confidence=float(item.get("confidence", 1.0)),
                    decision="edited",
                    source="human_edited",
                    bout_id=bout_id,
                )
            )
        else:
            accepted.append(
                BiteEventAnnotation(
                    id=bite.id,
                    ts=bite.ts,
                    confidence=bite.confidence,
                    decision="accepted",
                    source="human" if bite_id in verified or legacy_accept_all else "automatic",
                    bout_id=bout_id,
                )
            )
    for item in added.values():
        accepted.append(
            BiteEventAnnotation(
                id=item["id"],
                ts=float(item["ts"]),
                confidence=float(item.get("confidence", 1.0)),
                decision="added",
                source="human",
            )
        )
    return sorted(accepted, key=lambda item: item.ts), sorted(deleted)


def _review_complete(events: VideoEvents, review: dict) -> bool:
    if not bool(review.get("verified", False)):
        return False
    if not bool(review.get("full_video_pass_completed", review.get("verified", False))):
        return False
    base_chews = {chew.id for episode in events.episodes for chew in episode.chews}
    assessed_chews = (
        set(review.get("verified_chew_ids", []))
        | set(review.get("deleted_chew_ids", []))
        | {item["id"] for item in review.get("edited_chews", [])}
    )
    base_bites = {bite.id for episode in events.episodes for bite in episode.bites}
    assessed_bites = (
        set(review.get("verified_bite_ids", []))
        | set(review.get("deleted_bite_ids", []))
        | {item["id"] for item in review.get("edited_bites", [])}
    )
    # Existing verified reviews predate per-event decision lists.
    legacy_review = not any(
        key in review for key in ("full_video_pass_completed", "verified_bite_ids", "deleted_bite_ids")
    )
    return legacy_review or (base_chews <= assessed_chews and base_bites <= assessed_bites)


def finalize_session(
    session_dir: str | Path,
    derived_dir: str | Path,
    *,
    require_verified: bool = True,
) -> CanonicalAnnotations:
    """Create a reviewed annotation revision and both training manifests."""
    session_dir = Path(session_dir)
    derived_dir = Path(derived_dir)
    manifest = load_session_manifest(session_dir)
    events = _load_events(derived_dir)
    review_path = session_dir / "review_overrides.json"
    review = _read_json(review_path)
    complete = _review_complete(events, review)
    if require_verified and not complete:
        raise ValueError(
            "review is incomplete: assess every chew and bite, complete the full-video pass, "
            "then mark the session verified"
        )

    annotations_dir = session_dir / "annotations"
    revision = _next_revision(annotations_dir)
    coarse_payload = _read_json(annotations_dir / "coarse_labels.json")
    coarse = [
        CoarseInterval.model_validate(item)
        for item in coarse_payload.get("intervals", [])
    ]
    chews, rejected_chews = _chew_annotations(events, review)
    bites, rejected_bites = _bite_annotations(events, review)
    mouth_states = [
        MouthStateInterval(
            start_ts=item.start_ts,
            end_ts=item.end_ts,
            state=item.state,
            confidence=1.0,
        )
        for item in events.mouth_state_segments
    ]
    bouts: list[ChewingBoutAnnotation] = []
    for episode in events.episodes:
        bout_chews = [item.id for item in chews if item.bout_id == episode.id]
        bout_bites = [item.id for item in bites if item.bout_id == episode.id]
        bouts.append(
            ChewingBoutAnnotation(
                id=episode.id,
                start_ts=episode.start_ts,
                end_ts=episode.end_ts,
                confidence=episode.confidence,
                chew_cycle_ids=bout_chews,
                bite_event_ids=bout_bites,
            )
        )

    canonical = CanonicalAnnotations(
        session_id=manifest.session_id,
        revision=revision,
        parent_revision=manifest.annotation_revision or None,
        generated_at=utc_now(),
        reviewer=review.get("reviewer"),
        verified=complete,
        full_video_pass_completed=bool(
            review.get("full_video_pass_completed", review.get("verified", False))
        ),
        model_ids=manifest.active_model_ids,
        coarse_intervals=coarse,
        mouth_states=mouth_states,
        chew_cycles=chews,
        bite_events=bites,
        chewing_bouts=bouts,
        rejected_chew_ids=rejected_chews,
        rejected_bite_ids=rejected_bites,
        notes=review.get("notes"),
    )

    _write_json(annotations_dir / "auto_predictions.json", events.model_dump(mode="json"))
    revision_path = annotations_dir / f"review-v{revision:04d}.json"
    _write_json(revision_path, canonical)
    _write_json(annotations_dir / "canonical_events.json", canonical)

    motion_path = session_dir / manifest.motion.path
    aligned = align_events_to_motion(
        motion_path,
        events,
        time_offset=manifest.clock_sync.motion_to_video_offset_s,
        canonical_annotations=canonical,
    )
    motion_labels_path = annotations_dir / "motion_labels.csv"
    aligned.to_csv(motion_labels_path, index=False)

    manifests_dir = session_dir / "manifests"
    cv_manifest = TrainingManifest(
        kind="computer_vision",
        session_id=manifest.session_id,
        wearer_id=manifest.wearer_id,
        annotation_revision=revision,
        generated_at=utc_now(),
        verified=complete,
        model_ids=manifest.active_model_ids,
        inputs={
            "video": manifest.video.path if manifest.video else "",
            "session": "session.json",
        },
        targets={
            "annotations": str(revision_path.relative_to(session_dir)),
            "canonical_events": "annotations/canonical_events.json",
        },
    )
    motion_training_manifest = TrainingManifest(
        kind="motion",
        session_id=manifest.session_id,
        wearer_id=manifest.wearer_id,
        annotation_revision=revision,
        generated_at=utc_now(),
        verified=complete,
        model_ids=manifest.active_model_ids,
        inputs={"motion": manifest.motion.path, "session": "session.json"},
        targets={
            "motion_labels": str(motion_labels_path.relative_to(session_dir)),
            "canonical_events": "annotations/canonical_events.json",
        },
    )
    _write_json(manifests_dir / "cv_training.json", cv_manifest)
    _write_json(manifests_dir / "motion_training.json", motion_training_manifest)

    manifest.annotation_revision = revision
    manifest.status = "materialized" if complete else "in_review"
    manifest.updated_at = utc_now()
    _write_json(session_dir / "session.json", manifest)
    return canonical


def migrate_tree(
    input_root: str | Path,
    *,
    wearer_id: str = "local-user",
    force: bool = False,
) -> dict:
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
    """Copy only portable metadata/annotations when importing legacy bundles."""
    copied: list[str] = []
    for name in (
        "session.json",
        "review_overrides.json",
        "bite_review.json",
        "review_feedback.csv",
        "face_presence.json",
    ):
        src = source / name
        if src.exists():
            shutil.copy2(src, destination / name)
            copied.append(name)
    src_annotations = source / "annotations"
    if src_annotations.exists():
        dst_annotations = destination / "annotations"
        if dst_annotations.exists():
            shutil.rmtree(dst_annotations)
        shutil.copytree(src_annotations, dst_annotations)
        copied.append("annotations/")
    return copied
