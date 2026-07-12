"""
Synthetic-signal tests for `pipeline.bite_metrics` and `bite_detector`.

Builds a 1.5 Hz "chew" bandpower signal, runs the bite detector, hand-builds
the corresponding ground-truth `bite_review.json`, and asserts the
evaluation harness reports near-perfect metrics.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE_ROOT))

from pipeline import bite_metrics  # noqa: E402
from pipeline.events import BiteEvent, ChewStroke  # noqa: E402
from pipeline.bite_detector import (  # noqa: E402
    BiteDetector,
    BiteDetectorConfig,
    detect_bites_from_bundle,
)


def _make_predictions_csv(path: Path, fs: float = 10.0) -> tuple[list, list]:
    """Create a synthetic predictions.csv with two distinct bites."""
    duration = 30.0
    t = np.arange(0, duration, 1.0 / fs)
    band = np.zeros_like(t)
    prob = np.zeros_like(t)

    # Two bite windows, 15 s apart, each 5 s long with 1.5 Hz chew bandpower
    bite_windows = [(2.0, 7.0), (17.0, 22.0)]
    for s, e in bite_windows:
        mask = (t >= s) & (t <= e)
        band[mask] = 0.6 + 0.4 * np.sin(2 * math.pi * 1.5 * (t[mask] - s))
        band[mask] = np.clip(band[mask], 0.05, None)
        prob[mask] = 0.85
    band += 0.02  # noise floor

    df = pd.DataFrame({
        "timestamp": [f"t={x:.2f}" for x in t],
        "window_start": t,
        "window_end": t + 0.75,
        "probability": prob,
        "smoothed": prob,
        "state": ["chewing" if p > 0.5 else "idle" for p in prob],
        "chew_bandpower_1_3hz": band,
    })
    df.to_csv(path, index=False)
    return bite_windows, t.tolist()


def _make_ground_truth(bite_windows: list[tuple[float, float]]) -> dict:
    bites = []
    strokes = []
    period = 1.0 / 1.5
    # Predicted peaks of `sin(2π·1.5·(t-s))` occur at (t-s) = 1/6, 1/6 + period, ...
    # Align GT strokes to those peaks so F1 reflects the matching machinery
    # rather than a deliberate phase offset.
    for s, e in bite_windows:
        bites.append({"start_time": s, "end_time": e})
        ts = s + 1.0 / 6.0
        while ts < e:
            strokes.append({"timestamp": ts, "source": "manual"})
            ts += period
    return {"bites": bites, "strokes": strokes, "session_note": "synthetic"}


def test_synthetic_bundle_round_trip(tmp_path: Path):
    bundle = tmp_path / "synthetic"
    bundle.mkdir()

    bite_windows, _ = _make_predictions_csv(bundle / "predictions.csv")
    (bundle / "bite_review.json").write_text(json.dumps(_make_ground_truth(bite_windows)))
    (bundle / "session_metadata.json").write_text(json.dumps({"activity": "eating_soft"}))

    # Run detector to materialize bite_events.csv / chew_events.csv
    detect_bites_from_bundle(bundle)

    assert (bundle / "bite_events.csv").exists()
    assert (bundle / "chew_events.csv").exists()

    agg = bite_metrics.evaluate_bundles([bundle])
    assert len(agg.bundles) == 1
    rep = agg.bundles[0]

    assert rep.has_ground_truth is True
    # Detector should find 2 bites
    assert rep.pred_bite_count == 2
    assert rep.gt_bite_count == 2
    assert rep.bite_count_mape == 0.0

    # Stroke F1 should be high at 250ms tolerance
    assert rep.stroke_f1_250ms >= 0.7, f"F1@250ms too low: {rep.stroke_f1_250ms}"
    # Bite IoU should be very high
    assert rep.mean_bite_iou >= 0.7, f"IoU too low: {rep.mean_bite_iou}"


def test_match_strokes_basic():
    pred = [ChewStroke(timestamp=t) for t in [1.0, 2.0, 3.0]]
    truth = [ChewStroke(timestamp=t) for t in [1.05, 2.0, 3.5]]
    tp, fp, fn = bite_metrics._match_strokes(pred, truth, tolerance_sec=0.1)
    assert tp == 2
    assert fp == 1
    assert fn == 1


def test_match_strokes_empty():
    assert bite_metrics._match_strokes([], [], 0.1) == (0, 0, 0)
    assert bite_metrics._match_strokes(
        [ChewStroke(timestamp=1.0)], [], 0.1
    ) == (0, 1, 0)
    assert bite_metrics._match_strokes(
        [], [ChewStroke(timestamp=1.0)], 0.1
    ) == (0, 0, 1)


def test_bite_iou():
    a = BiteEvent(start_time=0.0, end_time=10.0)
    b = BiteEvent(start_time=5.0, end_time=15.0)
    assert bite_metrics._bite_iou(a, b) == pytest.approx(5.0 / 15.0)
    c = BiteEvent(start_time=20.0, end_time=30.0)
    assert bite_metrics._bite_iou(a, c) == 0.0
