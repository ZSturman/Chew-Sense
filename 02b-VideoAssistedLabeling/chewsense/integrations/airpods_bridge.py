"""Align video events onto the AirPods motion timestamp grid.

Canonical callers pass the measured ``motion_to_video_offset_s`` from
``session.json``. First-row inference remains only as a low-confidence
compatibility fallback for unmigrated legacy recordings.

Public surface
--------------
* :func:`detect_motion_video_offset` — return the motion-time origin
  (i.e. the value to subtract from a motion timestamp to land in
  video-time seconds).
* :func:`align_events_to_motion` — resample a :class:`VideoEvents` payload
  onto the motion CSV's per-row timeline; returns a tidy
  :class:`pandas.DataFrame` with the columns documented below.

Output schema (per motion row)
------------------------------
``timestamp``         monotonic seconds, copied from motion CSV
``video_ts``          ``timestamp - t_offset`` (seconds from video start)
``eating``            ``0``/``1`` — within any chewing bout
``mouth_state``       closed/opening/open/closing/unknown
``chew_phase``        idle/opening/closing
``chew_id``           id of the enclosing chew, ``""`` if none
``bite_event``        ``1`` on the motion row nearest a food-entry event
``bite_id``           id of that food-entry event
``bout_id``           id of the enclosing chewing bout
``label_valid``       ``1`` where video-derived targets are usable
``label_source``      automatic or human-reviewed provenance
``model_id``          serialized model-id mapping used for auto predictions
``annotation_revision`` immutable reviewed annotation revision
``video_confidence``  event confidence for the strongest active label
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..events.schema import Chew, Episode, MouthStateSegment, VideoEvents


# ---------------------------------------------------------------------------
# Time-offset detection
# ---------------------------------------------------------------------------

def detect_motion_video_offset(
    motion_timestamps: np.ndarray | pd.Series,
) -> float:
    """Return the motion-time origin (subtract from motion ts → video ts).

    This fallback treats the first IMU sample as video zero. New recordings
    must use the explicit shared-host-clock offset from ``session.json``.

    Parameters
    ----------
    motion_timestamps:
        Strictly increasing monotonic timestamps from the motion CSV's
        ``timestamp`` column (seconds since boot, per ``CMDeviceMotion``).
    """
    ts = np.asarray(motion_timestamps, dtype=np.float64)
    if ts.size == 0:
        raise ValueError("motion_timestamps is empty; cannot infer offset")
    return float(ts[0])


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

_PHASE_IDLE = "idle"
_PHASE_OPENING = "opening"
_PHASE_CLOSING = "closing"


def _label_phase(video_ts: np.ndarray, chews: Iterable[Chew]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized assignment of (chew_phase, chew_id, chew_confidence)."""
    n = video_ts.size
    phase = np.full(n, _PHASE_IDLE, dtype=object)
    chew_id = np.full(n, "", dtype=object)
    chew_conf = np.zeros(n, dtype=np.float64)

    for c in chews:
        # Opening half-window: [start_ts, peak_ts]
        op_mask = (video_ts >= c.start_ts) & (video_ts <= c.peak_ts)
        # Closing half-window: (peak_ts, end_ts]
        cl_mask = (video_ts > c.peak_ts) & (video_ts <= c.end_ts)
        if op_mask.any():
            phase[op_mask] = _PHASE_OPENING
            chew_id[op_mask] = c.id
            chew_conf[op_mask] = c.confidence
        if cl_mask.any():
            phase[cl_mask] = _PHASE_CLOSING
            chew_id[cl_mask] = c.id
            chew_conf[cl_mask] = c.confidence
    return phase, chew_id, chew_conf


def _label_eating(video_ts: np.ndarray, episodes: Iterable[Episode]) -> tuple[np.ndarray, np.ndarray]:
    """Return ``eating`` 0/1 mask plus per-row episode confidence."""
    n = video_ts.size
    eating = np.zeros(n, dtype=np.int8)
    ep_conf = np.zeros(n, dtype=np.float64)
    for ep in episodes:
        mask = (video_ts >= ep.start_ts) & (video_ts <= ep.end_ts)
        if mask.any():
            eating[mask] = 1
            ep_conf[mask] = np.maximum(ep_conf[mask], ep.confidence)
    return eating, ep_conf


def _label_bouts(
    video_ts: np.ndarray,
    bouts: Iterable[Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    eating = np.zeros(video_ts.size, dtype=np.int8)
    bout_id = np.full(video_ts.size, "", dtype=object)
    confidence = np.zeros(video_ts.size, dtype=np.float64)
    for bout in bouts:
        mask = (video_ts >= bout.start_ts) & (video_ts <= bout.end_ts)
        if mask.any():
            eating[mask] = 1
            bout_id[mask] = bout.id
            confidence[mask] = np.maximum(confidence[mask], float(bout.confidence))
    return eating, bout_id, confidence


def _label_mouth_state(
    video_ts: np.ndarray,
    segments: Iterable[Any],
) -> np.ndarray:
    state = np.full(video_ts.size, "unknown", dtype=object)
    for segment in segments:
        mask = (video_ts >= segment.start_ts) & (video_ts <= segment.end_ts)
        state[mask] = segment.state
    return state


def _label_bite_events(
    video_ts: np.ndarray,
    bites: Iterable[Any],
) -> tuple[np.ndarray, np.ndarray]:
    event = np.zeros(video_ts.size, dtype=np.int8)
    bite_id = np.full(video_ts.size, "", dtype=object)
    if video_ts.size == 0:
        return event, bite_id
    for bite in bites:
        idx = int(np.argmin(np.abs(video_ts - float(bite.ts))))
        event[idx] = 1
        bite_id[idx] = bite.id
    return event, bite_id


def align_events_to_motion(
    motion_csv_path: str | Path,
    events: VideoEvents,
    *,
    timestamp_col: str = "timestamp",
    time_offset: float | None = None,
    canonical_annotations: Any | None = None,
) -> pd.DataFrame:
    """Resample a :class:`VideoEvents` payload onto the motion-CSV grid.

    Parameters
    ----------
    motion_csv_path:
        Path to the motion CSV (e.g. ``<session>/<session>.csv``).
    events:
        Parsed :class:`VideoEvents` payload (read from ``events.json``).
    timestamp_col:
        Name of the motion-CSV timestamp column. Defaults to
        ``"timestamp"``.
    time_offset:
        Measured motion-to-video offset from ``session.json``. When ``None``,
        the first motion timestamp is used only for legacy compatibility.
    canonical_annotations:
        Optional reviewed annotation revision. When provided, reviewed chews,
        bites, bouts, provenance, and revision replace automatic event labels.

    Returns
    -------
    pandas.DataFrame
        One row per motion sample with the columns documented in the
        module docstring.
    """
    motion = pd.read_csv(motion_csv_path)
    if timestamp_col not in motion.columns:
        raise KeyError(
            f"motion CSV {motion_csv_path} is missing required column "
            f"{timestamp_col!r}; got {list(motion.columns)!r}"
        )
    motion_ts = motion[timestamp_col].astype(float).to_numpy()
    if time_offset is None:
        time_offset = detect_motion_video_offset(motion_ts)
    video_ts = motion_ts - float(time_offset)

    if canonical_annotations is not None:
        eating, bout_id, ep_conf = _label_bouts(
            video_ts,
            canonical_annotations.chewing_bouts,
        )
        all_chews = canonical_annotations.chew_cycles
        bites = canonical_annotations.bite_events
        mouth_segments = canonical_annotations.mouth_states
        label_source = (
            "human_reviewed" if canonical_annotations.verified else "automatic"
        )
        annotation_revision = int(canonical_annotations.revision)
        model_id = ";".join(
            f"{key}={value}"
            for key, value in sorted(canonical_annotations.model_ids.items())
        )
    else:
        eating, ep_conf = _label_eating(video_ts, events.episodes)
        bout_id = np.full(video_ts.size, "", dtype=object)
        for episode in events.episodes:
            mask = (video_ts >= episode.start_ts) & (video_ts <= episode.end_ts)
            bout_id[mask] = episode.id
        all_chews = [c for ep in events.episodes for c in ep.chews]
        bites = [b for ep in events.episodes for b in ep.bites]
        mouth_segments = events.mouth_state_segments
        label_source = "automatic"
        annotation_revision = 0
        model_id = ""

    phase, chew_id, chew_conf = _label_phase(video_ts, all_chews)
    mouth_state = _label_mouth_state(video_ts, mouth_segments)
    bite_event, bite_id = _label_bite_events(video_ts, bites)

    # Final per-row confidence: chew confidence wins inside a chew, else
    # episode confidence inside eating, else 0.
    video_confidence = np.where(chew_conf > 0, chew_conf, ep_conf)
    within_video = (video_ts >= 0.0) & (video_ts <= float(events.duration_s))
    label_valid = within_video & (mouth_state != "unknown")

    out = pd.DataFrame(
        {
            "timestamp": motion_ts,
            "video_ts": video_ts,
            "eating": eating,
            "mouth_state": mouth_state.astype(str),
            "chew_phase": phase.astype(str),
            "chew_id": chew_id.astype(str),
            "bite_event": bite_event,
            "bite_id": bite_id.astype(str),
            "bout_id": bout_id.astype(str),
            "label_valid": label_valid.astype(np.int8),
            "label_source": label_source,
            "model_id": model_id,
            "annotation_revision": annotation_revision,
            "video_confidence": video_confidence.astype(np.float32),
        }
    )
    out.attrs["time_offset"] = float(time_offset)
    out.attrs["video_id"] = events.video_id
    out.attrs["video_duration_s"] = events.duration_s
    return out
