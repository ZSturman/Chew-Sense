"""Read automatic Vision telemetry written by a detector run."""
from __future__ import annotations

import json
from pathlib import Path

from ..events.schema import VideoEvents


def load_events_json(events_json_path: str | Path) -> VideoEvents:
    """Parse detector telemetry; no motion-row labels are derived here."""
    with open(events_json_path) as f:
        data = json.load(f)
    return VideoEvents.model_validate(data)
