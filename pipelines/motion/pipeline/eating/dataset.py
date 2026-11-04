"""Discover recordings, attach provenance-tiered coarse labels, cut windows.

Label tiers (strongest first).  Every tier is a *human* statement; nothing a
model, detector, or guided schedule produced is ever a label here.

``human_coarse``
    The wearer watched the video and marked chewing / not-chewing spans
    (Collect schema-v2, shared host clock).
``wearer_no_food_attestation``
    The wearer attested that nothing was eaten or chewed during a food-free
    guided take.
``legacy_human_coarse``
    2025 in-app chewing spans.  Human, but the clock origin is the first
    motion row, so alignment is unverified.
``human_marked_segment``
    A span the wearer explicitly marked while reviewing a 2026 field test.
    Unmarked field-test time was *accepted model output* and is unknown here.
``session_declared_negative``
    The whole recording was captured as a declared non-eating scenario
    (legacy "Not-eating" captures, negative field-test activities).
``human_assisted``
    The wearer decided a span in Review after seeing video + motion
    proposals (``coarse-review-vNNNN.json``).  Training only.
``human_batch_accepted``
    The wearer accepted a video + motion agreement span together with others
    without inspecting it individually.  Training only, and its automatic
    boundaries get an extra 0.5 s margin.

Assisted tiers never enter evaluation: a label made while looking at the
detectors cannot independently score them.  Where an assisted decision
repeats an existing phone label, the phone label (and its tier) stands.

Excluded, never labels: guided instructions, detector proposals,
``automatic`` sources, field-test time the reviewer did not mark, and any span
marked ``unknown``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from ..temporal import resample_motion_window


ASSISTED_TIERS = ("human_assisted", "human_batch_accepted")
LABEL_TIERS = (
    "human_coarse",
    "wearer_no_food_attestation",
    "legacy_human_coarse",
    "human_marked_segment",
    "session_declared_negative",
    *ASSISTED_TIERS,
)
# Tiers whose labels may serve as evaluation reference: independent human
# statements only.  Assisted decisions train but never score.
EVALUATION_TIERS = tuple(tier for tier in LABEL_TIERS if tier not in ASSISTED_TIERS)
BATCH_BOUNDARY_MARGIN_S = 0.5

MOTION_COLUMNS = ("ax", "ay", "az", "gx", "gy", "gz")
CHEWING = "chewing"
NOT_CHEWING = "not_chewing"


class SessionRejected(ValueError):
    """A recording cannot contribute labelled windows."""


@dataclass(frozen=True)
class LabeledInterval:
    start: float
    end: float
    label: str
    tier: str


@dataclass
class EatingSession:
    session_id: str
    source_kind: str
    source_path: str
    created_at: str
    motion_t: np.ndarray
    imu: np.ndarray
    motion_sha256: str
    intervals: list[LabeledInterval]
    clock: str
    activity: str
    duration_s: float
    loop_role: str | None = None
    food_category: str | None = None
    context_spans: list[tuple[float, float, str]] = field(default_factory=list)
    dropped_motion_rows: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def labeled_seconds(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for item in self.intervals:
            key = f"{item.label}:{item.tier}"
            totals[key] = totals.get(key, 0.0) + item.end - item.start
        return totals

    @property
    def has_chewing(self) -> bool:
        return any(item.label == CHEWING for item in self.intervals)

    def summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_kind": self.source_kind,
            "source_path": self.source_path,
            "created_at": self.created_at,
            "motion_sha256": self.motion_sha256,
            "clock": self.clock,
            "activity": self.activity,
            "food_category": self.food_category,
            "loop_role": self.loop_role,
            "duration_s": round(self.duration_s, 3),
            "motion_samples": int(len(self.motion_t)),
            "dropped_motion_rows": self.dropped_motion_rows,
            "labeled_seconds": {k: round(v, 3) for k, v in sorted(self.labeled_seconds.items())},
            "intervals": [
                {"start": round(i.start, 4), "end": round(i.end, 4), "label": i.label, "tier": i.tier}
                for i in self.intervals
            ],
            "notes": list(self.notes),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SessionRejected(f"missing {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise SessionRejected(f"invalid JSON in {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SessionRejected(f"{path.name} is not a JSON object")
    return payload


def _read_motion(path: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """Return strictly increasing timestamps, (n, 6) IMU and dropped-row count."""

    if not path.exists():
        raise SessionRejected(f"missing motion file {path.name}")
    with path.open() as handle:
        first = handle.readline()
    has_header = "timestamp" in first
    try:
        frame = pd.read_csv(path) if has_header else pd.read_csv(
            path, header=None, names=["timestamp", *MOTION_COLUMNS]
        )
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise SessionRejected(f"unreadable motion file: {exc}") from exc
    missing = [c for c in ("timestamp", *MOTION_COLUMNS) if c not in frame.columns]
    if missing:
        raise SessionRejected(f"motion file missing columns {missing}")
    t = frame["timestamp"].to_numpy(dtype=np.float64)
    imu = frame[list(MOTION_COLUMNS)].to_numpy(dtype=np.float32)
    finite = np.isfinite(t) & np.isfinite(imu).all(axis=1)
    t, imu = t[finite], imu[finite]
    if len(t) < 2:
        raise SessionRejected("fewer than two finite motion samples")
    # Keep only rows that advance the clock; repeated/backward rows are dropped,
    # never reordered or interpolated.
    running = np.maximum.accumulate(t)
    keep = np.ones(len(t), dtype=bool)
    keep[1:] = t[1:] > running[:-1]
    dropped = int((~keep).sum()) + int((~finite).sum())
    return t[keep], imu[keep], dropped


def _guided_context(session_dir: Path, origin: float) -> tuple[list[tuple[float, float, str]], str | None]:
    path = session_dir / "guided_session.json"
    if not path.exists():
        return [], None
    guided = _read_json(path)
    definition = guided.get("definition") or {}
    activities = definition.get("activities") or []
    spans: list[tuple[float, float, str]] = []
    for attempt in guided.get("attempts") or []:
        index = attempt.get("activity_index")
        start, end = attempt.get("started_host_s"), attempt.get("ended_host_s")
        if not isinstance(index, int) or start is None or end is None or index >= len(activities):
            continue
        kind = str(activities[index].get("kind") or "unknown")
        spans.append((float(start) - origin, float(end) - origin, f"guided:{kind}"))
    food = (definition.get("food") or {}).get("category")
    return sorted(spans), (str(food) if food else None)


def _sorted_intervals(intervals: list[LabeledInterval]) -> list[LabeledInterval]:
    return sorted(intervals, key=lambda item: (item.start, item.end))


_ID_TIME = re.compile(r"(20\d{6})-(\d{6})")


def recorded_at(session_id: str, fallback: str) -> str:
    """Capture time for chronology.

    Migrated legacy manifests carry the 2026 migration time in ``created_at``;
    the capture time survives only in the recording name.
    """

    match = _ID_TIME.search(session_id)
    if match:
        day, clock = match.groups()
        return f"{day[:4]}-{day[4:6]}-{day[6:]}T{clock[:2]}:{clock[2:4]}:{clock[4:]}"
    return fallback


@dataclass
class CanonicalMotion:
    manifest: dict[str, Any]
    session_id: str
    motion_path: Path
    video_t: np.ndarray
    imu: np.ndarray
    dropped: int
    origin: float
    method: str
    duration: float


def load_canonical_motion(session_dir: Path) -> CanonicalMotion:
    """Motion on the video timeline (``t - video_clock_origin``) and duration."""

    manifest = _read_json(session_dir / "session.json")
    session_id = str(manifest.get("session_id") or session_dir.name)
    motion_ref = manifest.get("motion") or {}
    if not motion_ref.get("path"):
        raise SessionRejected("manifest has no motion reference")
    motion_path = session_dir / str(motion_ref["path"])
    t, imu, dropped = _read_motion(motion_path)
    clock = manifest.get("clock_sync") or {}
    origin = clock.get("video_clock_origin_host_time_s")
    if origin is None:
        raise SessionRejected("manifest has no video-clock origin")
    origin = float(origin)
    video_t = t - origin
    capture = manifest.get("capture") or {}
    duration = float(capture.get("duration_s") or video_t[-1])
    return CanonicalMotion(
        manifest=manifest, session_id=session_id, motion_path=motion_path, video_t=video_t, imu=imu,
        dropped=dropped, origin=origin, method=str(clock.get("method") or "unknown"), duration=duration,
    )


def latest_coarse_review(session_dir: Path) -> tuple[Path, dict[str, Any]] | None:
    revisions = sorted((Path(session_dir) / "annotations").glob("coarse-review-v*.json"))
    if not revisions:
        return None
    payload = _read_json(revisions[-1])
    if payload.get("artifact_type") != "coarse_label_review":
        raise SessionRejected(f"{revisions[-1].name} is not a coarse-label review")
    return revisions[-1], payload


def _review_intervals(review: Mapping[str, Any], duration: float) -> list[tuple[float, float, str, str]]:
    """``(start, end, label, tier)`` from a saved assisted review; label may be 'unknown'."""

    result = []
    for item in review.get("intervals") or []:
        try:
            start, end = float(item["start_ts"]), float(item["end_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        label = str(item.get("label", ""))
        if label not in {CHEWING, NOT_CHEWING, "unknown"} or not end > start:
            continue
        batch = item.get("decision") == "batch_accepted"
        if batch and not item.get("boundary_adjusted"):
            start, end = start + BATCH_BOUNDARY_MARGIN_S, end - BATCH_BOUNDARY_MARGIN_S
            if not end > start:
                continue
        tier = "human_batch_accepted" if batch else "human_assisted"
        result.append((max(0.0, start), min(end, duration), label, tier))
    return result


def overlay_review(
    base: Sequence[LabeledInterval], review: Sequence[tuple[float, float, str, str]]
) -> list[LabeledInterval]:
    """Apply assisted decisions on top of independent labels.

    Inside a reviewed span: 'unknown' removes any label; the same label as
    the base keeps the base interval and tier; a different label (or no base)
    takes the review's assisted tier.  Outside reviewed spans the base stands.
    """

    cuts = sorted({p for i in base for p in (i.start, i.end)} | {p for r in review for p in r[:2]})
    pieces: list[LabeledInterval] = []
    for lo, hi in zip(cuts, cuts[1:]):
        if hi - lo <= 1e-9:
            continue
        mid = (lo + hi) / 2
        base_item = next((i for i in base if i.start <= mid < i.end), None)
        review_item = next((r for r in review if r[0] <= mid < r[1]), None)
        if review_item is None:
            chosen = base_item and LabeledInterval(lo, hi, base_item.label, base_item.tier)
        elif review_item[2] == "unknown":
            chosen = None
        elif base_item is not None and base_item.label == review_item[2]:
            chosen = LabeledInterval(lo, hi, base_item.label, base_item.tier)
        else:
            chosen = LabeledInterval(lo, hi, review_item[2], review_item[3])
        if chosen is None:
            continue
        if pieces and pieces[-1].label == chosen.label and pieces[-1].tier == chosen.tier \
                and abs(pieces[-1].end - chosen.start) <= 1e-9:
            pieces[-1] = LabeledInterval(pieces[-1].start, chosen.end, chosen.label, chosen.tier)
        else:
            pieces.append(chosen)
    return pieces


def load_canonical_session(
    session_dir: Path,
    *,
    loop_roles: Mapping[str, str] | None = None,
) -> EatingSession:
    motion = load_canonical_motion(session_dir)
    manifest, session_id, motion_path = motion.manifest, motion.session_id, motion.motion_path
    video_t, imu, dropped = motion.video_t, motion.imu, motion.dropped
    origin, method, duration = motion.origin, motion.method, motion.duration

    coarse_path = session_dir / "annotations" / "coarse_labels.json"
    review = latest_coarse_review(session_dir)
    if not coarse_path.exists() and review is None:
        raise SessionRejected("no human coarse labels (coarse review missing)")
    raw_intervals: list[Any] = []
    if coarse_path.exists():
        coarse = _read_json(coarse_path)
        raw_intervals = coarse.get("intervals")
        if not isinstance(raw_intervals, list):
            raise SessionRejected("coarse labels have no interval list")
    activity = str(manifest.get("activity") or "unknown")
    legacy = manifest.get("source_app") == "legacy_import"
    single_negative = (
        len(raw_intervals) == 1 and str(raw_intervals[0].get("label")) == NOT_CHEWING
    )
    intervals: list[LabeledInterval] = []
    notes: list[str] = []
    for item in raw_intervals:
        label = str(item.get("label", ""))
        source = str(item.get("source", ""))
        try:
            start, end = float(item["start_ts"]), float(item["end_ts"])
        except (KeyError, TypeError, ValueError):
            notes.append("skipped malformed coarse interval")
            continue
        if label not in {CHEWING, NOT_CHEWING} or not end > start:
            continue
        if source in {"human_coarse", "human", "human_edited"}:
            tier = (
                "wearer_no_food_attestation"
                if item.get("basis") == "wearer_no_food_attestation"
                else "human_coarse"
            )
        elif source == "legacy":
            tier = (
                "session_declared_negative"
                if legacy and activity == "not_eating" and single_negative
                else "legacy_human_coarse"
            )
        else:
            notes.append(f"ignored coarse interval with non-human source {source!r}")
            continue
        intervals.append(LabeledInterval(max(0.0, start), min(end, duration), label, tier))
    if review is not None:
        review_path, review_payload = review
        intervals = overlay_review(_sorted_intervals(intervals), _review_intervals(review_payload, duration))
        notes.append(f"assisted coarse review {review_path.name} applied")
    if not intervals:
        raise SessionRejected("no human-labelled time")
    if method != "shared_host_clock":
        notes.append(f"clock origin method {method}: label alignment unverified")

    spans, food = _guided_context(session_dir, origin)
    role = (loop_roles or {}).get(session_id)
    if role == "diagnostic":
        raise SessionRejected("loop diagnostic role (timing check); never training or evaluation data")
    if spans:
        session_activity = "guided:" + str(
            (_read_json(session_dir / "guided_session.json").get("experiment_id") or "unknown")
        )
    elif activity == "eating":
        session_activity = "meal_legacy"
    elif activity == "not_eating":
        session_activity = "not_eating_unspecified"
    else:
        session_activity = activity
    return EatingSession(
        session_id=session_id,
        source_kind="canonical",
        source_path=str(session_dir),
        created_at=recorded_at(session_id, str(manifest.get("created_at") or "")),
        motion_t=video_t,
        imu=imu,
        motion_sha256=_sha256(motion_path),
        intervals=_sorted_intervals(intervals),
        clock="shared_host_clock" if method == "shared_host_clock" else "first_motion_fallback",
        activity=session_activity,
        duration_s=duration,
        loop_role=role,
        food_category=food,
        context_spans=spans,
        dropped_motion_rows=dropped,
        notes=notes,
    )


def load_field_test(test_dir: Path) -> EatingSession:
    metadata = _read_json(test_dir / "session_metadata.json")
    session_id = str(metadata.get("session_id") or test_dir.name)
    motion_path = test_dir / "motion.csv"
    t, imu, dropped = _read_motion(motion_path)
    video_t = t - t[0]
    duration = float(video_t[-1])
    segments_path = test_dir / "review_segments.json"
    segments = json.loads(segments_path.read_text()) if segments_path.exists() else []
    activity = str(metadata.get("activity") or "unspecified")
    expected = str(metadata.get("expected_label_class") or "")
    scenario = str(metadata.get("test_scenario") or "")
    notes = [
        "field-test review accepted unmarked time as model output; only marked spans are labels",
        "clock origin is the first motion row; label alignment unverified",
    ]
    marked: list[LabeledInterval] = []
    for segment in segments if isinstance(segments, list) else []:
        kind = str(segment.get("kind", ""))
        try:
            start, end = float(segment["startTime"]), float(segment["endTime"])
        except (KeyError, TypeError, ValueError):
            continue
        if kind in {CHEWING, NOT_CHEWING} and end > start:
            marked.append(LabeledInterval(max(0.0, start), min(end, duration), kind, "human_marked_segment"))
    conflicting = activity.startswith("eating") and scenario.lower().startswith("not eating")
    if conflicting:
        notes.append(f"scenario {scenario!r} conflicts with activity {activity!r}")
    intervals = marked
    if expected == "negative" and not any(i.label == CHEWING for i in marked) and not conflicting:
        intervals = [LabeledInterval(0.0, duration, NOT_CHEWING, "session_declared_negative")]
    if not intervals:
        raise SessionRejected(
            "no human-marked spans (unmarked field-test time is accepted model output)"
            + ("; scenario metadata conflicts" if conflicting else "")
        )
    return EatingSession(
        session_id=session_id,
        source_kind="field_test",
        source_path=str(test_dir),
        created_at=recorded_at(session_id, str(metadata.get("started_at") or "")),
        motion_t=video_t,
        imu=imu,
        motion_sha256=_sha256(motion_path),
        intervals=_sorted_intervals(intervals),
        clock="first_motion_fallback",
        activity=f"field:{activity}",
        duration_s=duration,
        dropped_motion_rows=dropped,
        notes=notes,
    )


def read_loop_roles(loop_root: Path | None) -> dict[str, str]:
    roles: dict[str, str] = {}
    if loop_root is None:
        return roles
    state = Path(loop_root) / "state" / "sessions"
    if not state.is_dir():
        return roles
    for path in sorted(state.glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(record, dict) and record.get("role"):
            roles[str(record.get("session_id") or path.stem)] = str(record["role"])
    return roles


def discover_sessions(
    *,
    canonical_roots: Iterable[Path] = (),
    field_test_roots: Iterable[Path] = (),
    loop_root: Path | None = None,
) -> tuple[list[EatingSession], list[dict[str, str]]]:
    """Load every usable recording; return sessions and per-recording rejections.

    One bad recording never aborts discovery.  Recordings with identical motion
    bytes are loaded once: field-test archives (explicit review provenance)
    are read first, so a migrated copy of the same capture is a duplicate.
    """

    roles = read_loop_roles(loop_root)
    sessions: list[EatingSession] = []
    rejected: list[dict[str, str]] = []
    seen_hash: dict[str, str] = {}
    seen_id: set[str] = set()

    def accept(session: EatingSession) -> None:
        if session.motion_sha256 in seen_hash:
            rejected.append({
                "session_id": session.session_id,
                "path": session.source_path,
                "reason": f"duplicate motion recording of {seen_hash[session.motion_sha256]}",
            })
            return
        if session.session_id in seen_id:
            rejected.append({
                "session_id": session.session_id,
                "path": session.source_path,
                "reason": "duplicate session_id with different motion bytes",
            })
            return
        seen_hash[session.motion_sha256] = session.session_id
        seen_id.add(session.session_id)
        sessions.append(session)

    for root in field_test_roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for test_dir in sorted(p.parent for p in root.rglob("session_metadata.json")):
            try:
                accept(load_field_test(test_dir))
            except SessionRejected as exc:
                rejected.append({"session_id": test_dir.name, "path": str(test_dir), "reason": str(exc)})
    for root in canonical_roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for session_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            if not (session_dir / "session.json").exists():
                rejected.append({
                    "session_id": session_dir.name,
                    "path": str(session_dir),
                    "reason": "no schema-v2 session.json (unmigrated or corrupt capture)",
                })
                continue
            try:
                accept(load_canonical_session(session_dir, loop_roles=roles))
            except SessionRejected as exc:
                rejected.append({"session_id": session_dir.name, "path": str(session_dir), "reason": str(exc)})
    sessions.sort(key=lambda s: (s.created_at, s.session_id))
    return sessions, rejected


@dataclass
class WindowSet:
    """All causal windows of all sessions; ``label == -1`` means unlabelled."""

    X: np.ndarray            # (N, 7, samples) resampled IMU + validity
    label: np.ndarray        # (N,) int8: 1 chewing, 0 not chewing, -1 excluded
    t_end: np.ndarray        # (N,) window end on the session video timeline
    session_id: np.ndarray   # (N,) str
    tier: np.ndarray         # (N,) str, "" when unlabelled
    context: np.ndarray      # (N,) str activity context, reporting only
    exclusion: np.ndarray    # (N,) str reason when label == -1
    valid_fraction: np.ndarray

    def __len__(self) -> int:
        return len(self.label)

    def subset(self, mask: np.ndarray) -> "WindowSet":
        return WindowSet(*(getattr(self, name)[mask] for name in (
            "X", "label", "t_end", "session_id", "tier", "context", "exclusion", "valid_fraction"
        )))


def _label_runs(intervals: Sequence[LabeledInterval]) -> list[tuple[float, float, str, str]]:
    """Merge touching same-label intervals into runs (start, end, label, tier-of-first)."""

    runs: list[list[Any]] = []
    for item in sorted(intervals, key=lambda i: i.start):
        if runs and runs[-1][2] == item.label and item.start <= runs[-1][1] + 0.05:
            runs[-1][1] = max(runs[-1][1], item.end)
            continue
        runs.append([item.start, item.end, item.label, item.tier])
    return [tuple(run) for run in runs]  # type: ignore[misc]


def _tier_at(intervals: Sequence[LabeledInterval], t: float) -> str:
    for item in intervals:
        if item.start <= t <= item.end:
            return item.tier
    return ""


def _context_at(session: EatingSession, t: float) -> str:
    for start, end, kind in session.context_spans:
        if start <= t <= end:
            return kind
    if session.context_spans:
        return "guided:between_activities"
    return session.activity


def build_windows(
    sessions: Sequence[EatingSession],
    *,
    window_s: float = 3.0,
    stride_s: float = 0.5,
    guard_s: float = 1.0,
    min_valid_fraction: float = 0.9,
    sample_rate_hz: int = 50,
) -> WindowSet:
    """Cut causal windows ``[t - window_s, t]`` every ``stride_s`` seconds.

    A window is labelled only when ``[t - window_s - guard_s, t + guard_s]``
    lies inside one run of a single label.  The guard absorbs clock and
    marking imprecision at transitions, so boundary windows are excluded
    rather than guessed.  Windows with insufficient measured motion are
    excluded too.  Excluded windows are kept for timeline replay.
    """

    if window_s <= 0 or stride_s <= 0 or guard_s < 0:
        raise ValueError("window and stride must be positive; guard non-negative")
    samples = int(round(window_s * sample_rate_hz))
    parts: dict[str, list[np.ndarray]] = {k: [] for k in (
        "X", "label", "t_end", "session_id", "tier", "context", "exclusion", "valid_fraction"
    )}
    for session in sessions:
        runs = _label_runs(session.intervals)
        ends = np.arange(window_s, session.duration_s + 1e-9, stride_s)
        if not len(ends):
            continue
        X = np.zeros((len(ends), 7, samples), dtype=np.float32)
        labels = np.full(len(ends), -1, dtype=np.int8)
        tiers = np.empty(len(ends), dtype=object)
        contexts = np.empty(len(ends), dtype=object)
        reasons = np.empty(len(ends), dtype=object)
        valid = np.zeros(len(ends), dtype=np.float32)
        for index, end in enumerate(ends):
            window, _ = resample_motion_window(
                session.motion_t,
                session.imu,
                end_timestamp=float(end),
                sample_rate_hz=sample_rate_hz,
                context_seconds=window_s,
            )
            X[index] = window
            valid[index] = float(window[6].mean())
            contexts[index] = _context_at(session, float(end))
            tiers[index] = ""
            reasons[index] = ""
            lo, hi = end - window_s - guard_s, end + guard_s
            run = next((r for r in runs if r[0] <= lo and hi <= r[1]), None)
            if run is None:
                reasons[index] = (
                    "label_boundary" if any(r[0] < hi and lo < r[1] for r in runs) else "unlabelled"
                )
            elif valid[index] < min_valid_fraction:
                reasons[index] = "sensor_gap"
            else:
                labels[index] = 1 if run[2] == CHEWING else 0
                tiers[index] = _tier_at(session.intervals, float(end) - window_s / 2)
        parts["X"].append(X)
        parts["label"].append(labels)
        parts["t_end"].append(ends.astype(np.float64))
        parts["session_id"].append(np.full(len(ends), session.session_id, dtype=object))
        parts["tier"].append(tiers)
        parts["context"].append(contexts)
        parts["exclusion"].append(reasons)
        parts["valid_fraction"].append(valid)
    if not parts["X"]:
        raise ValueError("no windows could be cut from the supplied sessions")
    return WindowSet(
        X=np.concatenate(parts["X"]),
        label=np.concatenate(parts["label"]),
        t_end=np.concatenate(parts["t_end"]),
        session_id=np.concatenate(parts["session_id"]).astype(str),
        tier=np.concatenate(parts["tier"]).astype(str),
        context=np.concatenate(parts["context"]).astype(str),
        exclusion=np.concatenate(parts["exclusion"]).astype(str),
        valid_fraction=np.concatenate(parts["valid_fraction"]),
    )


def dataset_fingerprint(sessions: Sequence[EatingSession], config: Mapping[str, Any]) -> str:
    """Hash of label-relevant inputs: motion bytes, intervals, tiers and config."""

    payload = {
        "config": dict(config),
        "sessions": [
            {
                "session_id": s.session_id,
                "motion_sha256": s.motion_sha256,
                "intervals": [(round(i.start, 4), round(i.end, 4), i.label, i.tier) for i in s.intervals],
                "loop_role": s.loop_role,
            }
            for s in sessions
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
