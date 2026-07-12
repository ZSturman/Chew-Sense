"""Validate the pydantic schema round-trips cleanly."""
from __future__ import annotations

from chewsense.events.schema import (
    Bite, Chew, Episode, ProbableCompletion, SCHEMA_VERSION, VideoEvents,
)


def test_video_events_round_trip():
    ev = VideoEvents(
        video_id="abc",
        duration_s=10.0,
        fps=30.0,
        width=640,
        height=480,
        episodes=[
            Episode(
                id="ep_001",
                start_ts=1.0,
                end_ts=4.0,
                start_reason="bite_detected",
                end_reason="food_likely_cleared",
                confidence=0.7,
                bites=[Bite(id="bt_0", ts=0.9, confidence=0.8, evidence={})],
                chews=[Chew(id="ch_0",
                            start_ts=1.1, peak_ts=1.3, end_ts=1.5,
                            duration_s=0.4, amplitude=0.04,
                            relative_amplitude=1.0, confidence=0.6,
                            evidence={"start_frame": 33, "peak_frame": 39, "end_frame": 45})],
                probable_completion=ProbableCompletion(
                    kind="probable_swallow", ts=4.0, confidence=0.4),
            ),
        ],
    )
    s = ev.model_dump_json()
    parsed = VideoEvents.model_validate_json(s)
    assert parsed.episodes[0].chews[0].peak_ts == 1.3
    assert parsed.episodes[0].chew_count == 1
    assert parsed.episodes[0].bite_count == 1
    assert parsed.schema_version == SCHEMA_VERSION
