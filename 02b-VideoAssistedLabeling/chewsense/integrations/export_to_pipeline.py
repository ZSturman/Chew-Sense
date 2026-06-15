"""Export aligned labels + the canonical chew-cycle JSON for one session.

Wraps :mod:`chewsense.integrations.airpods_bridge` with the concrete file
layout used by ``run_on_sessions.py`` and by the future macOS reviewer
app: per-session outputs land beside the rest of the video-labeller
artifacts under ``02-DataPipeline/data/derived/video_labels/<sid>/``.

Two files are written:

``motion_aligned_labels.csv``
    Per-motion-row labels (see
    :func:`chewsense.integrations.airpods_bridge.align_events_to_motion`).
``chew_cycles.json``
    Slim sidecar with the full event payload keyed by motion timestamps;
    consumed by the macOS reviewer to seed its editable timeline.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from ..events.schema import VideoEvents, Episode
from .airpods_bridge import align_events_to_motion

logger = logging.getLogger(__name__)

# Minimum gap between adjacent chews (seconds).  Mirrors the Swift constant
# ``ReviewerState.neighborGap`` and ``video_labels.NEIGHBOR_GAP_S``.
_NEIGHBOR_GAP_S = 1.0 / 30.0


def _deoverlap_episode(ep: Episode) -> Episode:
    """Return a copy of *ep* with overlapping chews trimmed / dropped.

    Chews are sorted by start_ts.  When a chew's start_ts falls within
    (prev.end_ts + gap) the later chew's start_ts is pushed forward.  If
    pushing it forward would violate start < peak the chew is dropped entirely.
    """
    gap = _NEIGHBOR_GAP_S
    ordered = sorted(ep.chews, key=lambda c: c.start_ts)
    clean = []
    prev_end: float = float("-inf")
    for c in ordered:
        if c.start_ts >= prev_end + gap:
            clean.append(c)
            prev_end = c.end_ts
        else:
            new_start = prev_end + gap
            if new_start < c.peak_ts:
                logger.warning(
                    "session chew %s overlaps previous (start %.4f → %.4f); trimming",
                    c.id, c.start_ts, new_start,
                )
                clean.append(c.model_copy(update={"start_ts": new_start}))
                prev_end = c.end_ts
            else:
                logger.warning(
                    "session chew %s overlaps previous irreparably (start %.4f peak %.4f); dropping",
                    c.id, c.start_ts, c.peak_ts,
                )
    return ep.model_copy(update={"chews": clean})


def export_session(
    motion_csv_path: str | Path,
    events: VideoEvents,
    out_dir: str | Path,
    *,
    timestamp_col: str = "timestamp",
    time_offset: float | None = None,
    session_manifest_path: str | Path | None = None,
) -> dict:
    """Write the aligned-labels CSV and chew-cycle JSON for one session.

    Returns a small dict summary (paths + row counts + offset).
    """
    motion_csv_path = Path(motion_csv_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if session_manifest_path is not None:
        from ..session_bundle import SessionManifest

        manifest = SessionManifest.model_validate(
            json.loads(Path(session_manifest_path).read_text())
        )
        time_offset = manifest.clock_sync.motion_to_video_offset_s

    aligned = align_events_to_motion(
        motion_csv_path,
        events,
        timestamp_col=timestamp_col,
        time_offset=time_offset,
    )

    aligned_csv = out_dir / "motion_aligned_labels.csv"
    aligned.to_csv(aligned_csv, index=False)

    cycles_json = out_dir / "chew_cycles.json"
    clean_episodes = [_deoverlap_episode(ep) for ep in events.episodes]
    payload = {
        "schema_version": events.schema_version,
        "video_id": events.video_id,
        "duration_s": events.duration_s,
        "fps": events.fps,
        "time_offset": aligned.attrs["time_offset"],
        "motion_csv": str(motion_csv_path.resolve()),
        "n_motion_rows": int(len(aligned)),
        "episodes": [ep.model_dump() for ep in clean_episodes],
    }
    with open(cycles_json, "w") as f:
        json.dump(payload, f, indent=2)

    return {
        "motion_aligned_labels": str(aligned_csv),
        "chew_cycles": str(cycles_json),
        "n_motion_rows": int(len(aligned)),
        "n_eating_rows": int(aligned["eating"].sum()),
        "n_chew_rows": int((aligned["chew_id"] != "").sum()),
        "time_offset": aligned.attrs["time_offset"],
    }


def load_events_json(events_json_path: str | Path) -> VideoEvents:
    """Parse a previously-written ``events.json`` back into a ``VideoEvents``."""
    with open(events_json_path) as f:
        data = json.load(f)
    return VideoEvents.model_validate(data)
