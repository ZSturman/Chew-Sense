"""Bridges between the video labeller and the AirPods motion pipeline.

The :mod:`chewsense` package emits per-video event timelines
(`events.json`, `review_payload.json`). The training pipeline in
``02-DataPipeline/`` consumes per-row labels on the AirPods motion CSV.
This subpackage owns the **time-alignment + label-export** glue between
the two.

See ``airpods_bridge.align_events_to_motion`` and
``export_to_pipeline.export_session`` for the two entry points used by
``run_on_sessions.py`` and the future macOS reviewer app.
"""
from __future__ import annotations

from .airpods_bridge import (
    align_events_to_motion,
    detect_motion_video_offset,
)
from .export_to_pipeline import export_session

__all__ = [
    "align_events_to_motion",
    "detect_motion_video_offset",
    "export_session",
]
