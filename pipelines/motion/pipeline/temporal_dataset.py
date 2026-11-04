"""Materialize causal-TCN datasets from canonical schema-v2 sessions.

The canonical session is the only source of label truth.  This module joins
its immutable finalized annotation revision back to the raw six-axis motion,
then emits the exact arrays consumed by :class:`TemporalChewNet`:

``X.npy``
    ``(N, 7, 150)`` raw IMU windows at 50 Hz plus a gap-validity channel.
``y.npy``
    ``(N, 8)`` eating/event/phase targets and their supervision masks.
``timestamps.npy`` / ``session_ids.npy``
    Stable replay keys; windows from one session are never split by row.

Schema-v2 coarse-only sessions are intentionally useful for the eating head,
but their event and phase masks are zero.  A session with missing coarse
review is rejected rather than interpreted as all-negative.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .temporal import (
    CONTEXT_SAMPLES,
    INFERENCE_STEP_SECONDS,
    MASK_EATING,
    MASK_EVENT,
    MASK_PHASE,
    TARGET_EVENT,
    TARGET_PHASE_CONFIDENCE,
    TARGET_PHASE_COS,
    TARGET_PHASE_SIN,
    TARGET_WIDTH,
    TEMPORAL_CONTRACT_VERSION,
    build_temporal_targets,
    resample_motion_window,
)


SESSION_SCHEMA_VERSION = "2.0.0"
MOTION_COLUMNS = ("ax", "ay", "az", "gx", "gy", "gz")
HARD_NEGATIVE_ACTIVITIES = frozenset(
    {
        "drinking",
        "drinking_water",
        "head_nod",
        "head_movement",
        "head_shake",
        "head_shaking",
        "nodding",
        "posture_change",
        "posture_changes",
        "talking",
        "walking",
    }
)


class TemporalDatasetError(ValueError):
    """A canonical session cannot safely contribute temporal supervision."""


@dataclass(frozen=True)
class SessionMaterialization:
    session_id: str
    created_at: str
    activity: str
    label: str
    verified: bool
    hard_negative: bool
    event_supervision: bool
    supervision: str
    rows: int
    eating_rows: int
    event_count: int
    excluded_eating_rows: int
    excluded_event_rows: int
    motion_sha256: str
    candidate_sha256: str | None = None
    review_sha256: str | None = None
    review_revision: int | None = None
    food_category: str | None = None


@dataclass(frozen=True)
class TemporalDatasetResult:
    output_dir: Path
    sessions: tuple[SessionMaterialization, ...]
    rows: int
    dataset_hash: str
    manifest_path: Path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise TemporalDatasetError(f"missing required artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TemporalDatasetError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TemporalDatasetError(f"expected a JSON object in {path}")
    return payload


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_content_sha256(payload: Mapping[str, Any]) -> str:
    content = dict(payload)
    content.pop("content_sha256", None)
    encoded = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_inside(session_dir: Path, relative: str, *, kind: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise TemporalDatasetError(f"{kind} path must be relative: {relative}")
    resolved = (session_dir / relative_path).resolve()
    try:
        resolved.relative_to(session_dir.resolve())
    except ValueError as exc:
        raise TemporalDatasetError(f"{kind} path escapes session: {relative}") from exc
    return resolved


def discover_v2_sessions(root: str | Path) -> list[Path]:
    """Return schema-v2 session directories in stable chronological order."""

    root = Path(root)
    candidates = [root] if (root / "session.json").exists() else [
        child for child in root.iterdir() if child.is_dir() and (child / "session.json").exists()
    ]

    def key(session_dir: Path) -> tuple[str, str]:
        try:
            created_at = str(_read_json(session_dir / "session.json").get("created_at", ""))
        except TemporalDatasetError:
            created_at = ""
        return created_at, session_dir.name

    return sorted(candidates, key=key)


def _normalize_activity(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "unknown").strip().lower())
    return text.strip("_") or "unknown"


def session_activity_context(session_dir: Path, manifest: Mapping[str, Any]) -> str:
    """Confound metadata only; never a source of training labels."""
    activity = _normalize_activity(manifest.get("activity"))
    if activity == "unknown" and (session_dir / "guided_session.json").exists():
        guided = _read_json(session_dir / "guided_session.json")
        kinds = {a.get("kind") for a in guided.get("definition", {}).get("activities", [])}
        kinds -= {"sync_marker", "rest"}
        confounds = {"talk": "talking", "walk": "walking", "drink": "drinking",
                     "head_movement": "head_movement"}
        if len(kinds) == 1:
            activity = confounds.get(next(iter(kinds)), activity)
    return activity


def _load_coarse_intervals(session_dir: Path, session_id: str) -> tuple[list[dict[str, Any]], bool]:
    path = session_dir / "annotations" / "coarse_labels.json"
    payload = _read_json(path)
    if payload.get("schema_version") != SESSION_SCHEMA_VERSION:
        raise TemporalDatasetError(f"{session_id}: coarse labels are not schema 2.0.0")
    if payload.get("artifact_type") != "coarse_labels":
        raise TemporalDatasetError(f"{session_id}: invalid coarse-label artifact type")
    if payload.get("session_id") != session_id:
        raise TemporalDatasetError(f"{session_id}: coarse-label session_id mismatch")
    raw_intervals = payload.get("intervals")
    if not isinstance(raw_intervals, list):
        raise TemporalDatasetError(f"{session_id}: coarse intervals must be a list")
    intervals: list[dict[str, Any]] = []
    previous_end: float | None = None
    for item in raw_intervals:
        if not isinstance(item, dict):
            raise TemporalDatasetError(f"{session_id}: malformed coarse interval")
        try:
            start = float(item["start_ts"])
            end = float(item["end_ts"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TemporalDatasetError(f"{session_id}: malformed coarse bounds") from exc
        label = str(item.get("label", ""))
        if not np.isfinite(start) or not np.isfinite(end) or start < 0 or end < start:
            raise TemporalDatasetError(f"{session_id}: invalid coarse interval {start}..{end}")
        if previous_end is not None and start < previous_end:
            raise TemporalDatasetError(f"{session_id}: overlapping coarse intervals")
        if label not in {"chewing", "not_chewing", "unknown"}:
            raise TemporalDatasetError(f"{session_id}: unknown coarse label {label!r}")
        intervals.append({"start_ts": start, "end_ts": end, "label": label})
        previous_end = end
    return intervals, len(intervals) == 0


def _load_finalized_labels(
    session_dir: Path,
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, pd.DataFrame | None]:
    """Load exact immutable materialized revision and derived motion labels."""

    pointer_path = session_dir / "annotations" / "canonical_events.json"
    if not pointer_path.exists():
        return None, None
    pointer = _read_json(pointer_path)
    session_id = str(manifest["session_id"])
    if (
        pointer.get("schema_version") != SESSION_SCHEMA_VERSION
        or pointer.get("artifact_type") != "canonical_pointer"
        or pointer.get("session_id") != session_id
    ):
        raise TemporalDatasetError(f"{session_id}: invalid canonical pointer")
    review_path = _resolve_inside(
        session_dir, str(pointer.get("review_path", "")), kind="review"
    )
    review = _read_json(review_path)
    if (
        review.get("schema_version") != SESSION_SCHEMA_VERSION
        or review.get("artifact_type") != "review_revision"
        or review.get("session_id") != session_id
    ):
        raise TemporalDatasetError(f"{session_id}: invalid immutable review revision")
    review_hash = _canonical_content_sha256(review)
    if (
        review.get("content_sha256") != review_hash
        or pointer.get("selected_review_sha256") != review_hash
        or review.get("review_revision") != pointer.get("selected_review_revision")
        or review.get("candidate_revision") != pointer.get("candidate_revision")
        or review.get("candidate_sha256") != pointer.get("candidate_sha256")
    ):
        raise TemporalDatasetError(f"{session_id}: stale or tampered review lineage")
    materialized_path = _resolve_inside(
        session_dir, str(pointer.get("materialized_path", "")), kind="materialized"
    )
    materialized = _read_json(materialized_path)
    if (
        materialized.get("schema_version") != SESSION_SCHEMA_VERSION
        or materialized.get("artifact_type") != "materialized_annotations"
        or materialized.get("session_id") != session_id
    ):
        raise TemporalDatasetError(f"{session_id}: invalid materialized annotations")
    actual_hash = _canonical_content_sha256(materialized)
    if materialized.get("content_sha256") != actual_hash:
        raise TemporalDatasetError(f"{session_id}: materialized content hash mismatch")
    if pointer.get("materialized_sha256") != actual_hash:
        raise TemporalDatasetError(f"{session_id}: stale materialized pointer")
    for pointer_key, materialized_key in (
        ("candidate_sha256", "candidate_sha256"),
        ("selected_review_sha256", "review_sha256"),
        ("selected_review_revision", "review_revision"),
    ):
        if pointer.get(pointer_key) != materialized.get(materialized_key):
            raise TemporalDatasetError(f"{session_id}: canonical lineage mismatch ({pointer_key})")
    if materialized.get("verified") is not True or materialized.get("full_video_pass_completed") is not True:
        raise TemporalDatasetError(f"{session_id}: canonical revision is not fully verified")

    training_manifest_path = session_dir / "manifests" / "motion_training.json"
    training_manifest = _read_json(training_manifest_path)
    if (
        training_manifest.get("schema_version") != SESSION_SCHEMA_VERSION
        or training_manifest.get("kind") != "motion"
        or training_manifest.get("session_id") != session_id
        or training_manifest.get("candidate_sha256") != pointer.get("candidate_sha256")
        or training_manifest.get("review_sha256") != pointer.get("selected_review_sha256")
    ):
        raise TemporalDatasetError(f"{session_id}: motion training lineage mismatch")
    targets = training_manifest.get("targets")
    if not isinstance(targets, dict) or not targets.get("motion_labels"):
        raise TemporalDatasetError(f"{session_id}: motion training manifest has no labels")
    labels_path = _resolve_inside(
        session_dir, str(targets["motion_labels"]), kind="motion labels"
    )
    try:
        labels = pd.read_csv(labels_path)
    except (OSError, pd.errors.ParserError) as exc:
        raise TemporalDatasetError(f"{session_id}: cannot read motion labels: {exc}") from exc
    required = {
        "timestamp",
        "video_ts",
        "label_valid",
        "phase_valid",
        "chew_event_valid",
        "label_source",
        "annotation_revision",
    }
    missing = sorted(required - set(labels.columns))
    if missing:
        raise TemporalDatasetError(f"{session_id}: motion labels missing {missing}")
    if not labels["label_source"].astype(str).eq("human_reviewed").all():
        raise TemporalDatasetError(f"{session_id}: motion labels are not human-reviewed")
    revision = int(pointer["selected_review_revision"])
    if not labels["annotation_revision"].astype(int).eq(revision).all():
        raise TemporalDatasetError(f"{session_id}: motion-label revision mismatch")
    return materialized, labels


def _nearest_values(
    source_t: np.ndarray,
    values: np.ndarray,
    target_t: np.ndarray,
) -> np.ndarray:
    if len(source_t) == 0:
        return np.zeros(len(target_t), dtype=values.dtype)
    right = np.searchsorted(source_t, target_t, side="left")
    left = np.clip(right - 1, 0, len(source_t) - 1)
    right = np.clip(right, 0, len(source_t) - 1)
    choose_right = np.abs(source_t[right] - target_t) < np.abs(source_t[left] - target_t)
    indices = np.where(choose_right, right, left)
    distance = np.abs(source_t[indices] - target_t)
    bracket_ok = (source_t[right] - source_t[left] <= 0.08) | (distance <= 1e-9)
    covered = (distance <= 0.04) & bracket_ok & (target_t >= source_t[0]) & (target_t <= source_t[-1])
    return np.where(covered, values[indices], 0)


def _mask_for_intervals(
    timestamps: np.ndarray,
    intervals: Sequence[Mapping[str, Any]],
    *,
    labels: frozenset[str],
) -> np.ndarray:
    mask = np.zeros(len(timestamps), dtype=bool)
    for interval in intervals:
        if str(interval["label"]) in labels:
            mask |= (
                (timestamps >= float(interval["start_ts"]))
                & (timestamps <= float(interval["end_ts"]))
            )
    return mask


def _materialize_one(
    session_dir: Path,
    *,
    inference_step_seconds: float,
    max_gap_seconds: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, SessionMaterialization]:
    manifest = _read_json(session_dir / "session.json")
    session_id = str(manifest.get("session_id") or session_dir.name)
    if manifest.get("schema_version") != SESSION_SCHEMA_VERSION:
        raise TemporalDatasetError(
            f"{session_id}: schema {manifest.get('schema_version')!r} is read-only; migrate explicitly"
        )
    if not isinstance(manifest.get("motion"), dict) or not manifest["motion"].get("path"):
        raise TemporalDatasetError(f"{session_id}: manifest has no motion media reference")
    clock = manifest.get("clock_sync")
    if not isinstance(clock, dict) or clock.get("video_clock_origin_host_time_s") is None:
        raise TemporalDatasetError(f"{session_id}: manifest has no video-clock origin")
    try:
        clock_origin = float(clock["video_clock_origin_host_time_s"])
    except (TypeError, ValueError) as exc:
        raise TemporalDatasetError(f"{session_id}: invalid video-clock origin") from exc
    if not np.isfinite(clock_origin):
        raise TemporalDatasetError(f"{session_id}: invalid video-clock origin")

    motion_path = _resolve_inside(session_dir, str(manifest["motion"]["path"]), kind="motion")
    if not motion_path.exists():
        raise TemporalDatasetError(f"{session_id}: missing motion CSV {motion_path}")
    recorded_motion_hash = manifest["motion"].get("sha256")
    motion_hash = _file_sha256(motion_path)
    if recorded_motion_hash and recorded_motion_hash != motion_hash:
        raise TemporalDatasetError(f"{session_id}: motion SHA-256 mismatch")
    try:
        motion = pd.read_csv(motion_path)
    except (OSError, pd.errors.ParserError) as exc:
        raise TemporalDatasetError(f"{session_id}: cannot read motion CSV: {exc}") from exc
    required_motion = {"timestamp", *MOTION_COLUMNS}
    missing_motion = sorted(required_motion - set(motion.columns))
    if missing_motion:
        raise TemporalDatasetError(f"{session_id}: motion CSV missing {missing_motion}")
    motion_t = motion["timestamp"].to_numpy(dtype=np.float64)
    imu = motion[list(MOTION_COLUMNS)].to_numpy(dtype=np.float32)
    if (
        len(motion_t) < 2
        or not np.isfinite(motion_t).all()
        or not np.isfinite(imu).all()
        or np.any(np.diff(motion_t) <= 0)
    ):
        raise TemporalDatasetError(f"{session_id}: motion samples must be finite and strictly monotonic")
    video_motion_t = motion_t - clock_origin

    capture = manifest.get("capture") if isinstance(manifest.get("capture"), dict) else {}
    duration_value = capture.get("duration_s")
    duration = float(duration_value) if duration_value is not None else float(video_motion_t[-1])
    if not np.isfinite(duration) or duration < 0:
        raise TemporalDatasetError(f"{session_id}: invalid capture duration")
    if duration + 1e-6 < float(video_motion_t[-1]):
        raise TemporalDatasetError(f"{session_id}: motion extends beyond capture duration")

    intervals, explicit_all_negative = _load_coarse_intervals(session_dir, session_id)
    for interval in intervals:
        if float(interval["end_ts"]) > duration + 1e-6:
            raise TemporalDatasetError(f"{session_id}: coarse interval exceeds capture duration")
    eating_intervals = [
        (float(item["start_ts"]), float(item["end_ts"]))
        for item in intervals
        if item["label"] == "chewing"
    ]

    materialized, derived_labels = _load_finalized_labels(session_dir, manifest)
    supervision = "reviewed" if materialized is not None else "coarse"
    cycles = list(materialized.get("chew_cycles", [])) if materialized else []
    if materialized is not None and bool(manifest.get("legacy_review_required", False)):
        raise TemporalDatasetError(f"{session_id}: finalized session still requires legacy review")
    if explicit_all_negative and materialized is not None and (
        cycles or materialized.get("food_entries")
    ):
        raise TemporalDatasetError(
            f"{session_id}: explicit all-negative coarse review contains finalized events"
        )
    previous_end: float | None = None
    for cycle in cycles:
        try:
            start = float(cycle["start_closed_ts"])
            peak = float(cycle["open_peak_ts"])
            end = float(cycle["end_closed_ts"])
            event = float(cycle["chew_event_ts"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TemporalDatasetError(f"{session_id}: malformed materialized chew") from exc
        if not (0 <= start < peak < end <= duration + 1e-6) or not np.isclose(
            event, end, atol=1e-9, rtol=0
        ):
            raise TemporalDatasetError(f"{session_id}: invalid closed/open/closed chew anchors")
        if previous_end is not None and start < previous_end - 1e-9:
            raise TemporalDatasetError(f"{session_id}: materialized chews overlap")
        if eating_intervals and not any(
            interval_start - 1e-6 <= start and end <= interval_end + 1e-6
            for interval_start, interval_end in eating_intervals
        ):
            raise TemporalDatasetError(
                f"{session_id}: materialized chew falls outside reviewed chewing intervals"
            )
        previous_end = end
    if derived_labels is not None:
        if len(derived_labels) != len(motion):
            raise TemporalDatasetError(f"{session_id}: motion-label row count mismatch")
        label_t = derived_labels["timestamp"].to_numpy(dtype=np.float64)
        label_video_t = derived_labels["video_ts"].to_numpy(dtype=np.float64)
        if not np.allclose(label_t, motion_t, atol=1e-7, rtol=0):
            raise TemporalDatasetError(f"{session_id}: motion-label timestamps do not match raw motion")
        if not np.allclose(label_video_t, video_motion_t, atol=1e-7, rtol=0):
            raise TemporalDatasetError(f"{session_id}: motion labels use a different clock origin")

    if inference_step_seconds <= 0:
        raise TemporalDatasetError("inference step must be positive")
    inference_t = np.arange(
        0.0,
        duration + inference_step_seconds * 0.25,
        inference_step_seconds,
        dtype=np.float64,
    )
    if not len(inference_t):
        inference_t = np.array([0.0], dtype=np.float64)
    windows = np.empty((len(inference_t), 7, CONTEXT_SAMPLES), dtype=np.float32)
    for index, end_timestamp in enumerate(inference_t):
        windows[index], _ = resample_motion_window(
            video_motion_t,
            imu,
            end_timestamp=float(end_timestamp),
            max_gap_seconds=max_gap_seconds,
        )

    targets = build_temporal_targets(
        inference_t,
        eating_intervals=eating_intervals,
        cycles=cycles,
        supervision=supervision,
    )
    if explicit_all_negative:
        eating_valid = np.ones(len(inference_t), dtype=bool)
    else:
        eating_valid = _mask_for_intervals(
            inference_t,
            intervals,
            labels=frozenset({"chewing", "not_chewing"}),
        )
    targets[:, MASK_EATING] = eating_valid.astype(np.float32)

    if derived_labels is not None:
        source_t = derived_labels["video_ts"].to_numpy(dtype=np.float64)
        label_valid = _nearest_values(
            source_t,
            derived_labels["label_valid"].to_numpy(dtype=np.float32),
            inference_t,
        )
        event_valid = _nearest_values(
            source_t,
            derived_labels["chew_event_valid"].to_numpy(dtype=np.float32),
            inference_t,
        )
        phase_valid = _nearest_values(
            source_t,
            derived_labels["phase_valid"].to_numpy(dtype=np.float32),
            inference_t,
        )
        targets[:, MASK_EATING] *= label_valid >= 0.5
        targets[:, MASK_EVENT] *= event_valid >= 0.5
        targets[:, MASK_PHASE] *= phase_valid >= 0.5
    else:
        targets[:, MASK_EVENT] = 0.0
        targets[:, MASK_PHASE] = 0.0

    # The endpoint itself must have sensor coverage; a mostly missing context
    # or nearest surviving label cannot turn an outage into a negative target.
    endpoint_valid = windows[:, 6, -1] >= 0.5
    for column in (MASK_EATING, MASK_EVENT, MASK_PHASE):
        targets[:, column] *= endpoint_valid
    targets[targets[:, MASK_EVENT] < 0.5, TARGET_EVENT] = 0.0
    invalid_phase = targets[:, MASK_PHASE] < 0.5
    targets[invalid_phase, TARGET_PHASE_SIN] = 0.0
    targets[invalid_phase, TARGET_PHASE_COS] = 0.0
    targets[invalid_phase, TARGET_PHASE_CONFIDENCE] = 0.0

    activity = session_activity_context(session_dir, manifest)
    guided_path = session_dir / "guided_session.json"
    food_category = None
    if guided_path.exists():
        food_category = (_read_json(guided_path).get("definition", {}).get("food") or {}).get("category")
    hard_negative_session = activity in HARD_NEGATIVE_ACTIVITIES
    hard_negative_mask = (
        hard_negative_session
        & (targets[:, MASK_EATING] >= 0.5)
        & (targets[:, 0] < 0.5)
    )
    verified = bool(materialized and materialized.get("verified")) or str(
        manifest.get("status", "")
    ) in {"coarse_reviewed", "verified", "materialized", "included_in_training"}
    eating_rows = int(((targets[:, 0] >= 0.5) & (targets[:, MASK_EATING] >= 0.5)).sum())
    label = "eating" if eating_rows else "hard-negative" if hard_negative_session else "not-eating"
    record = SessionMaterialization(
        session_id=session_id,
        created_at=str(manifest.get("created_at", "")),
        activity=activity,
        label=label,
        verified=verified,
        hard_negative=hard_negative_session,
        event_supervision=bool(np.any(targets[:, MASK_EVENT] >= 0.5)),
        supervision=supervision,
        rows=len(inference_t),
        eating_rows=eating_rows,
        event_count=len(cycles),
        excluded_eating_rows=int((targets[:, MASK_EATING] < 0.5).sum()),
        excluded_event_rows=int((targets[:, MASK_EVENT] < 0.5).sum()),
        motion_sha256=motion_hash,
        candidate_sha256=(str(materialized["candidate_sha256"]) if materialized else None),
        review_sha256=(str(materialized["review_sha256"]) if materialized else None),
        review_revision=(int(materialized["review_revision"]) if materialized else None),
        food_category=food_category,
    )
    return (
        windows,
        targets.astype(np.float32),
        inference_t,
        hard_negative_mask.astype(bool),
        record,
    )


def materialize_temporal_dataset(
    sessions: Iterable[str | Path],
    output_dir: str | Path,
    *,
    inference_step_seconds: float = INFERENCE_STEP_SECONDS,
    max_gap_seconds: float = 0.08,
    skip_invalid: bool = False,
) -> TemporalDatasetResult:
    """Write one deterministic temporal dataset from canonical sessions.

    By default any invalid session aborts the dataset, which is what the
    guided loop wants for its pre-screened training set.  ``skip_invalid``
    instead excludes each invalid session, records why in the manifest's
    ``rejected_sessions`` and fails only if nothing usable remains.
    """

    session_dirs = [Path(path) for path in sessions]
    if not session_dirs:
        raise TemporalDatasetError("no schema-v2 sessions were supplied")
    rejected: list[dict[str, str]] = []
    all_windows: list[np.ndarray] = []
    all_targets: list[np.ndarray] = []
    all_timestamps: list[np.ndarray] = []
    all_session_ids: list[np.ndarray] = []
    all_hard_negative: list[np.ndarray] = []
    records: list[SessionMaterialization] = []
    seen_ids: set[str] = set()
    for session_dir in session_dirs:
        try:
            windows, targets, timestamps, hard_negative, record = _materialize_one(
                session_dir,
                inference_step_seconds=inference_step_seconds,
                max_gap_seconds=max_gap_seconds,
            )
        except TemporalDatasetError as exc:
            if not skip_invalid:
                raise TemporalDatasetError(
                    f"{exc} (in {session_dir}; use skip_invalid / --skip-invalid to exclude "
                    "invalid sessions and list them in the manifest)"
                ) from exc
            rejected.append({"session_dir": str(session_dir), "reason": str(exc)})
            continue
        if record.session_id in seen_ids:
            raise TemporalDatasetError(f"duplicate session_id: {record.session_id}")
        seen_ids.add(record.session_id)
        all_windows.append(windows)
        all_targets.append(targets)
        all_timestamps.append(timestamps)
        all_session_ids.append(np.full(len(timestamps), record.session_id, dtype=object))
        all_hard_negative.append(hard_negative)
        records.append(record)

    if not all_windows:
        raise TemporalDatasetError(
            "no valid schema-v2 sessions remain: "
            + "; ".join(f"{item['session_dir']}: {item['reason']}" for item in rejected)
        )
    X = np.concatenate(all_windows).astype(np.float32, copy=False)
    y = np.concatenate(all_targets).astype(np.float32, copy=False)
    timestamps = np.concatenate(all_timestamps).astype(np.float64, copy=False)
    session_ids = np.concatenate(all_session_ids)
    hard_negative_mask = np.concatenate(all_hard_negative).astype(bool, copy=False)
    if X.shape != (len(y), 7, CONTEXT_SAMPLES) or y.shape[1:] != (TARGET_WIDTH,):
        raise AssertionError("internal temporal dataset shape mismatch")

    digest = hashlib.sha256()
    digest.update(TEMPORAL_CONTRACT_VERSION.encode())
    digest.update(X.tobytes())
    digest.update(y.tobytes())
    digest.update(timestamps.tobytes())
    digest.update("\0".join(map(str, session_ids)).encode())
    digest.update(hard_negative_mask.tobytes())
    digest.update(
        json.dumps(
            [asdict(record) for record in records],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    dataset_hash = digest.hexdigest()

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    np.save(output / "X.npy", X, allow_pickle=False)
    np.save(output / "y.npy", y, allow_pickle=False)
    np.save(output / "timestamps.npy", timestamps, allow_pickle=False)
    np.save(output / "session_ids.npy", session_ids)
    np.save(output / "hard_negative_mask.npy", hard_negative_mask, allow_pickle=False)
    array_files = (
        "X.npy",
        "y.npy",
        "timestamps.npy",
        "session_ids.npy",
        "hard_negative_mask.npy",
    )
    manifest = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "artifact_type": "temporal_motion_dataset",
        "temporal_contract_version": TEMPORAL_CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_hash": dataset_hash,
        "input_shape": [7, CONTEXT_SAMPLES],
        "target_width": TARGET_WIDTH,
        "sample_rate_hz": 50,
        "context_seconds": 3.0,
        "inference_step_seconds": inference_step_seconds,
        "max_gap_seconds": max_gap_seconds,
        "rows": len(X),
        "arrays": {
            name: {"sha256": _file_sha256(output / name)} for name in array_files
        },
        "sessions": [asdict(record) for record in records],
        "rejected_sessions": rejected,
    }
    manifest_path = output / "dataset_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return TemporalDatasetResult(
        output_dir=output,
        sessions=tuple(records),
        rows=len(X),
        dataset_hash=dataset_hash,
        manifest_path=manifest_path,
    )


def create_locked_temporal_splits(
    dataset_manifest: str | Path | Mapping[str, Any],
    output_path: str | Path,
    *,
    validation_ratio: float = 0.20,
    test_ratio: float = 0.10,
) -> dict[str, Any]:
    """Create chronological, session-level, class-covered locked splits.

    Positive and hard-negative sessions are assigned chronologically within
    each class so validation and test both contain both classes.  The function
    refuses undersized datasets instead of manufacturing an invalid holdout.
    """

    manifest = (
        dict(dataset_manifest)
        if isinstance(dataset_manifest, Mapping)
        else _read_json(Path(dataset_manifest))
    )
    sessions = manifest.get("sessions")
    if not isinstance(sessions, list):
        raise TemporalDatasetError("temporal dataset manifest has no sessions")
    positive = sorted(
        [
            item
            for item in sessions
            if item.get("label") == "eating"
            and item.get("verified") is True
            and item.get("event_supervision") is True
        ],
        key=lambda item: (str(item.get("created_at", "")), str(item.get("session_id", ""))),
    )
    hard_negative = sorted(
        [
            item
            for item in sessions
            if item.get("hard_negative") is True
            and item.get("verified") is True
            and item.get("event_supervision") is True
        ],
        key=lambda item: (str(item.get("created_at", "")), str(item.get("session_id", ""))),
    )
    if len(positive) < 3 or len(hard_negative) < 3:
        raise TemporalDatasetError(
            "locked temporal splits require at least three fully reviewed eating and "
            "three fully reviewed hard-negative sessions"
        )

    def partition(items: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
        count = len(items)
        n_test = max(1, int(round(count * test_ratio)))
        n_validation = max(1, int(round(count * validation_ratio)))
        if n_test + n_validation >= count:
            n_validation = 1
            n_test = 1
        ids = [str(item["session_id"]) for item in items]
        return ids[: count - n_validation - n_test], ids[count - n_validation - n_test : -n_test], ids[-n_test:]

    output = Path(output_path)
    current_ids = {str(item.get("session_id")) for item in sessions}
    if "None" in current_ids or len(current_ids) != len(sessions):
        raise TemporalDatasetError("temporal dataset sessions require unique session_id values")
    now = datetime.now(timezone.utc).isoformat()
    if output.exists():
        previous = _read_json(output)
        if previous.get("version") != 2:
            raise TemporalDatasetError(
                f"refusing to reinterpret non-v2 split manifest: {output}"
            )
        previous_splits = previous.get("splits", {})
        validation = set(map(str, previous_splits.get("validation_locked", [])))
        test = set(map(str, previous_splits.get("test_locked", [])))
        locked = validation | test
        missing_locked = locked - current_ids
        if missing_locked:
            raise TemporalDatasetError(
                "locked sessions disappeared from the dataset: "
                + ", ".join(sorted(missing_locked))
            )
        eligible_positive = {str(item["session_id"]) for item in positive}
        eligible_negative = {str(item["session_id"]) for item in hard_negative}
        for name, values in (("validation_locked", validation), ("test_locked", test)):
            if not (values & eligible_positive) or not (values & eligible_negative):
                raise TemporalDatasetError(
                    f"existing {name} no longer has fully reviewed eating and hard-negative coverage"
                )
        train = current_ids - locked
        field_regression = set(map(str, previous_splits.get("field_regression", []))) & current_ids
        gold = set(map(str, previous_splits.get("gold_locked", []))) & current_ids
        created_at = str(previous.get("created_at", now))
    else:
        pos_train, pos_validation, pos_test = partition(positive)
        neg_train, neg_validation, neg_test = partition(hard_negative)
        validation = set(pos_validation + neg_validation)
        test = set(pos_test + neg_test)
        train = current_ids - validation - test
        field_regression = set()
        gold = set()
        created_at = now

    result = {
        "version": 2,
        "created_at": created_at,
        "updated_at": now,
        "dataset_hash": manifest.get("dataset_hash"),
        "policy": {
            "unit": "session",
            "ordering": "chronological_within_class",
            "class_coverage": "eating_and_hard_negative_in_every_locked_split",
            "validation_ratio": validation_ratio,
            "test_ratio": test_ratio,
            "new_sessions_default": "train",
            "locked_assignments_immutable": True,
        },
        "splits": {
            "train": sorted(train),
            "validation_locked": sorted(validation),
            "test_locked": sorted(test),
            "field_regression": sorted(field_regression),
            "gold_locked": sorted(gold),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


__all__ = [
    "HARD_NEGATIVE_ACTIVITIES",
    "SessionMaterialization",
    "TemporalDatasetError",
    "TemporalDatasetResult",
    "create_locked_temporal_splits",
    "discover_v2_sessions",
    "materialize_temporal_dataset",
]
