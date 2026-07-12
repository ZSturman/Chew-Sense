"""
Canonical event schemas shared by bite segmentation, meal analysis, and
the chew/bite metrics evaluation harness.

All timestamps are in seconds, relative to the session start, matching the
convention used by `motion.csv` and `predictions.csv` in field-test bundles.

Mirror types live in Swift under
`apps/live-ios/ChewSenseLive/Models/`
(`ChewStroke.swift`, `BiteEvent.swift`). Keep field names in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional


CHEW_STROKE_SOURCES = ("heuristic", "model", "auto", "confirmed", "manual")


@dataclass(frozen=True)
class ChewStroke:
    """A single jaw-stroke event detected in the motion stream.

    `source` describes provenance:
      - "heuristic": Python/Swift peak-detector output
      - "model":     learned chew-stroke head output
      - "auto":      seeded from video (mouth-openness peak)
      - "confirmed": auto/heuristic candidate confirmed by a human reviewer
      - "manual":    placed by a human reviewer
    """
    timestamp: float
    confidence: float = 1.0
    source: str = "heuristic"

    def __post_init__(self) -> None:
        if self.source not in CHEW_STROKE_SOURCES:
            raise ValueError(
                f"ChewStroke.source must be one of {CHEW_STROKE_SOURCES}, "
                f"got {self.source!r}"
            )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BiteEvent:
    """A discrete bite: a contiguous chewing episode, with its strokes."""
    start_time: float
    end_time: float
    confidence: float = 0.0
    chew_count: Optional[int] = None
    chew_strokes: List[ChewStroke] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return float(self.end_time - self.start_time)

    def to_dict(self) -> dict:
        return {
            "start_time": float(self.start_time),
            "end_time": float(self.end_time),
            "duration": self.duration,
            "confidence": float(self.confidence),
            "chew_count": self.chew_count,
            "chew_strokes": [s.to_dict() for s in self.chew_strokes],
        }


__all__ = ["ChewStroke", "BiteEvent", "CHEW_STROKE_SOURCES"]
