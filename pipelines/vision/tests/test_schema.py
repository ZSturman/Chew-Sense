"""Validate the automatic telemetry schema round-trips cleanly."""
from __future__ import annotations

from chewsense.events.schema import (
    FoodEntryProposal,
    MouthCycle,
    SCHEMA_VERSION,
    SessionCounters,
    VideoEvents,
)


def test_video_events_round_trip_uses_one_cycle_stream():
    events = VideoEvents(
        video_id="abc",
        duration_s=10.0,
        fps=30.0,
        width=640,
        height=480,
        mouth_cycles=[
            MouthCycle(
                id="mc_0000",
                start_ts=1.1,
                peak_ts=1.3,
                end_ts=1.5,
                duration_s=0.4,
                kind="visible_open",
                peak_magnitude=2.1,
                confidence=0.8,
                evidence={
                    "start_frame": 33,
                    "peak_frame": 39,
                    "end_frame": 45,
                },
            )
        ],
        food_entry_proposals=[
            FoodEntryProposal(
                id="fe_0000",
                food_entry_ts=0.9,
                confidence=0.7,
            )
        ],
        cycle_counts=SessionCounters(visible_open=1, total=1),
    )

    parsed = VideoEvents.model_validate_json(events.model_dump_json())

    assert parsed.mouth_cycles[0].peak_ts == 1.3
    assert parsed.food_entry_proposals[0].food_entry_ts == 0.9
    assert parsed.cycle_counts.total == 1
    assert parsed.schema_version == SCHEMA_VERSION == "2.0.0"
    assert "episodes" not in parsed.model_dump()
