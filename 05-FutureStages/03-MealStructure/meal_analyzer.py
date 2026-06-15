"""
Meal Analyzer — derive meal-level metrics from a sequence of `BiteEvent`s.

Inputs are the canonical `BiteEvent` objects emitted by
`05-FutureStages/02-BiteSegmentation/bite_detector.py` (and the runtime
`BiteSegmenter` Swift mirror). The metrics here are the motion-only
quantities promised in the project plan:

    chews_per_bite, bite_rate, inter_bite_interval, rushed_bite_count,
    bite_count, chew_count, plus pace trend.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

import numpy as np

# Allow running from this folder.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATAPIPE = _REPO_ROOT / "02-DataPipeline"
if str(_DATAPIPE) not in sys.path:
    sys.path.insert(0, str(_DATAPIPE))

from pipeline.events import BiteEvent  # noqa: E402


# ----------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------

@dataclass
class MealMetrics:
    """Motion-only metrics computed for a single meal/session."""
    total_duration: float          # seconds (last bite end - first bite start)
    bite_count: int
    chew_count: int
    total_chewing_time: float      # seconds (sum of bite durations)
    avg_bite_duration: float       # seconds
    avg_chews_per_bite: float
    avg_inter_bite_interval: float # seconds (mean of consecutive start-to-start gaps)
    median_inter_bite_interval: float
    bite_rate_per_minute: float    # bites / (total_duration / 60)
    rushed_bite_count: int
    rushed_bite_fraction: float    # rushed_bite_count / bite_count
    eating_efficiency: float       # chewing_time / total_duration
    pace_variability: float        # std of IBIs (seconds)
    first_half_pace: float         # mean IBI in first half (seconds)
    second_half_pace: float        # mean IBI in second half (seconds)
    pace_trend: str                # "slowing" | "speeding" | "stable"


@dataclass
class MealPhase:
    """A contiguous run of bites separated from the next by a long pause."""
    start_time: float
    end_time: float
    bite_count: int
    avg_pace: float
    phase_label: Optional[str] = None


@dataclass
class MealAnalyzerConfig:
    """Configuration for meal analysis."""
    min_meal_duration: float = 60.0
    min_bites_for_meal: int = 5
    phase_gap_threshold: float = 30.0  # IBI > this starts a new phase
    pace_slow_threshold: float = 10.0  # IBI in seconds
    pace_fast_threshold: float = 3.0
    rushed_chews_per_bite: int = 5     # fewer than this -> rushed
    pace_trend_min_pct_delta: float = 0.15  # < 15% change is "stable"


# ----------------------------------------------------------------------

class MealAnalyzer:
    """Compute meal-level metrics and detect coarse meal phases."""

    def __init__(self, config: MealAnalyzerConfig | None = None):
        self.config = config or MealAnalyzerConfig()

    # ------------------------------------------------------------------
    def analyze_meal(self, bites: List[BiteEvent]) -> MealMetrics:
        """Compute meal metrics from a sequence of bites."""
        if not bites:
            return MealMetrics(
                total_duration=0.0,
                bite_count=0,
                chew_count=0,
                total_chewing_time=0.0,
                avg_bite_duration=0.0,
                avg_chews_per_bite=0.0,
                avg_inter_bite_interval=0.0,
                median_inter_bite_interval=0.0,
                bite_rate_per_minute=0.0,
                rushed_bite_count=0,
                rushed_bite_fraction=0.0,
                eating_efficiency=0.0,
                pace_variability=0.0,
                first_half_pace=0.0,
                second_half_pace=0.0,
                pace_trend="stable",
            )

        cfg = self.config
        sorted_bites = sorted(bites, key=lambda b: b.start_time)
        starts = np.asarray([b.start_time for b in sorted_bites], dtype=float)
        ends = np.asarray([b.end_time for b in sorted_bites], dtype=float)
        durations = np.asarray([b.duration for b in sorted_bites], dtype=float)
        chew_counts = np.asarray(
            [int(b.chew_count or 0) for b in sorted_bites], dtype=int
        )

        total_duration = float(ends[-1] - starts[0]) if len(starts) > 1 else float(durations[0])
        total_chewing_time = float(np.sum(durations))
        avg_bite_duration = float(np.mean(durations))
        avg_chews_per_bite = float(np.mean(chew_counts))
        chew_count = int(np.sum(chew_counts))

        ibis = self._compute_inter_bite_intervals(sorted_bites)
        if ibis.size:
            avg_ibi = float(np.mean(ibis))
            median_ibi = float(np.median(ibis))
            pace_var = float(np.std(ibis, ddof=0))
        else:
            avg_ibi = 0.0
            median_ibi = 0.0
            pace_var = 0.0

        bite_rate = (
            len(sorted_bites) / (total_duration / 60.0)
            if total_duration > 0 else 0.0
        )

        rushed = int(np.sum(chew_counts < cfg.rushed_chews_per_bite))
        rushed_frac = rushed / len(sorted_bites)
        efficiency = (
            total_chewing_time / total_duration if total_duration > 0 else 0.0
        )

        first_half, second_half = self._half_pace(ibis)
        trend = self._detect_pace_trend(first_half, second_half)

        return MealMetrics(
            total_duration=total_duration,
            bite_count=len(sorted_bites),
            chew_count=chew_count,
            total_chewing_time=total_chewing_time,
            avg_bite_duration=avg_bite_duration,
            avg_chews_per_bite=avg_chews_per_bite,
            avg_inter_bite_interval=avg_ibi,
            median_inter_bite_interval=median_ibi,
            bite_rate_per_minute=float(bite_rate),
            rushed_bite_count=rushed,
            rushed_bite_fraction=float(rushed_frac),
            eating_efficiency=float(efficiency),
            pace_variability=pace_var,
            first_half_pace=first_half,
            second_half_pace=second_half,
            pace_trend=trend,
        )

    # ------------------------------------------------------------------
    def detect_phases(self, bites: List[BiteEvent]) -> List[MealPhase]:
        """Split bites into phases at gaps > `phase_gap_threshold`."""
        if not bites:
            return []
        sorted_bites = sorted(bites, key=lambda b: b.start_time)
        cfg = self.config

        groups: list[list[BiteEvent]] = [[sorted_bites[0]]]
        for prev, curr in zip(sorted_bites, sorted_bites[1:]):
            gap = curr.start_time - prev.end_time
            if gap > cfg.phase_gap_threshold:
                groups.append([curr])
            else:
                groups[-1].append(curr)

        phases: list[MealPhase] = []
        for group in groups:
            ibis = self._compute_inter_bite_intervals(group)
            avg_pace = float(np.mean(ibis)) if ibis.size else 0.0
            label = self._label_pace(avg_pace)
            phases.append(MealPhase(
                start_time=float(group[0].start_time),
                end_time=float(group[-1].end_time),
                bite_count=len(group),
                avg_pace=avg_pace,
                phase_label=label,
            ))
        return phases

    # ------------------------------------------------------------------
    def _compute_inter_bite_intervals(self, bites: List[BiteEvent]) -> np.ndarray:
        """Time between consecutive bite *starts*."""
        if len(bites) < 2:
            return np.asarray([], dtype=float)
        starts = np.asarray([b.start_time for b in bites], dtype=float)
        return np.diff(starts)

    def _half_pace(self, ibis: np.ndarray) -> tuple[float, float]:
        if ibis.size == 0:
            return 0.0, 0.0
        if ibis.size == 1:
            v = float(ibis[0])
            return v, v
        mid = ibis.size // 2
        first = float(np.mean(ibis[:mid])) if mid > 0 else float(ibis[0])
        second = float(np.mean(ibis[mid:]))
        return first, second

    def _detect_pace_trend(self, first: float, second: float) -> str:
        if first <= 0 and second <= 0:
            return "stable"
        denom = max(first, 1e-6)
        delta = (second - first) / denom
        if delta > self.config.pace_trend_min_pct_delta:
            # second-half IBI is *larger* -> slower eating
            return "slowing"
        if delta < -self.config.pace_trend_min_pct_delta:
            return "speeding"
        return "stable"

    def _label_pace(self, ibi: float) -> Optional[str]:
        cfg = self.config
        if ibi <= 0:
            return None
        if ibi < cfg.pace_fast_threshold:
            return "fast"
        if ibi > cfg.pace_slow_threshold:
            return "slow"
        return None


# ----------------------------------------------------------------------

def main():
    """CLI: load bite_events.csv from a bundle and emit meal_metrics.json."""
    import argparse
    import csv
    import json

    parser = argparse.ArgumentParser(description="Compute meal metrics from a bundle's bite_events.csv")
    parser.add_argument("input", help="Bundle dir containing bite_events.csv, or a bite_events JSON")
    parser.add_argument("--output", "-o", help="Output meal_metrics.json (default: <bundle>/meal_metrics.json)")
    args = parser.parse_args()

    in_path = Path(args.input)
    bites: list[BiteEvent] = []
    if in_path.is_dir():
        bites_csv = in_path / "bite_events.csv"
        if not bites_csv.exists():
            raise FileNotFoundError(f"No bite_events.csv in: {in_path}")
        with open(bites_csv, newline="") as f:
            for row in csv.DictReader(f):
                bites.append(BiteEvent(
                    start_time=float(row["start_time"]),
                    end_time=float(row["end_time"]),
                    confidence=float(row.get("confidence", 0.0) or 0.0),
                    chew_count=int(float(row.get("chew_count", 0) or 0)),
                ))
        out = Path(args.output) if args.output else in_path / "meal_metrics.json"
    else:
        payload = json.loads(in_path.read_text())
        for b in payload.get("bites", []):
            bites.append(BiteEvent(
                start_time=float(b["start_time"]),
                end_time=float(b["end_time"]),
                confidence=float(b.get("confidence", 0.0)),
                chew_count=int(b.get("chew_count") or 0),
            ))
        out = Path(args.output) if args.output else in_path.with_name("meal_metrics.json")

    metrics = MealAnalyzer().analyze_meal(bites)
    out.write_text(json.dumps(asdict(metrics), indent=2))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
