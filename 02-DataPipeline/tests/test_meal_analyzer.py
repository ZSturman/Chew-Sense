"""Phase 6 — synthetic tests for the meal analyzer and quality scorer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "05-FutureStages" / "03-MealStructure"))
sys.path.insert(0, str(_REPO_ROOT / "05-FutureStages" / "04-QualityEvaluation"))
sys.path.insert(0, str(_REPO_ROOT / "02-DataPipeline"))

from pipeline.events import BiteEvent  # noqa: E402
from meal_analyzer import MealAnalyzer  # noqa: E402
from chew_quality import ChewQualityEvaluator, QualityRating  # noqa: E402


def _bite(t0: float, duration: float, chews: int) -> BiteEvent:
    return BiteEvent(
        start_time=t0,
        end_time=t0 + duration,
        confidence=1.0,
        chew_count=chews,
        chew_strokes=[],
    )


def test_meal_analyzer_basic_counts():
    # Five bites of ~2s, spaced 8s apart, 25 chews each.
    bites = [_bite(i * 8.0, 2.0, 25) for i in range(5)]
    m = MealAnalyzer().analyze_meal(bites)

    assert m.bite_count == 5
    assert m.chew_count == 125
    assert m.avg_chews_per_bite == pytest.approx(25.0)
    assert m.avg_inter_bite_interval == pytest.approx(8.0)
    assert m.rushed_bite_count == 0
    # Stable pace (first half ≈ second half).
    assert m.pace_trend == "stable"


def test_meal_analyzer_detects_pace_slowing():
    # Pace doubles in the second half → "slowing".
    bites = [
        _bite(0.0, 2.0, 20),
        _bite(4.0, 2.0, 20),
        _bite(8.0, 2.0, 20),
        _bite(20.0, 2.0, 20),
        _bite(32.0, 2.0, 20),
    ]
    m = MealAnalyzer().analyze_meal(bites)
    assert m.pace_trend == "slowing"


def test_meal_analyzer_flags_rushed_bites():
    # Three bites with chew_count below the rushed threshold (default 5).
    bites = [_bite(i * 6.0, 1.0, 3) for i in range(3)]
    m = MealAnalyzer().analyze_meal(bites)
    assert m.rushed_bite_count == 3
    assert m.rushed_bite_fraction == pytest.approx(1.0)


def test_quality_scorer_rates_balanced_meal():
    # Target band: 20–30 chews/bite, 5–15 s gap.
    bites = [_bite(i * 8.0, 2.5, 24) for i in range(6)]
    m = MealAnalyzer().analyze_meal(bites)
    q = ChewQualityEvaluator().evaluate_meal(m, bites)
    assert q.overall_rating in (QualityRating.GOOD, QualityRating.EXCELLENT)
    assert q.thoroughness_rating == QualityRating.EXCELLENT


def test_quality_scorer_flags_rushed_meal():
    # Tiny chew counts + tight 2 s gaps → bad pace + thoroughness.
    bites = [_bite(i * 2.0, 0.5, 2) for i in range(6)]
    m = MealAnalyzer().analyze_meal(bites)
    q = ChewQualityEvaluator().evaluate_meal(m, bites)
    assert q.overall_rating in (
        QualityRating.NEEDS_IMPROVEMENT,
        QualityRating.FAIR,
    )
    assert q.thoroughness_rating == QualityRating.NEEDS_IMPROVEMENT
    assert q.recommendations  # non-empty


def test_meal_analyzer_phase_split():
    # Two clusters separated by a 60 s pause → 2 phases (gap threshold 30 s).
    bites = [
        _bite(0.0, 2.0, 20),
        _bite(8.0, 2.0, 20),
        _bite(16.0, 2.0, 20),
        _bite(80.0, 2.0, 20),
        _bite(88.0, 2.0, 20),
    ]
    phases = MealAnalyzer().detect_phases(bites)
    assert len(phases) == 2
    assert phases[0].bite_count == 3
    assert phases[1].bite_count == 2
