"""Metrics for chewing-bout detection: windows, thresholds, sessions, bouts.

Window metrics use only labelled windows (guard-banded, human tiers).  Bout
metrics replay every window of a session through the same EMA + hysteresis
state machine as Live's ``ChewDetector`` and compare predicted bouts with the
human chewing intervals; predicted bouts lying in unlabelled time are not
scored either way.  Uncertainty is expressed with session-level bootstrap
intervals because windows within one session are strongly correlated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
from scipy.stats import beta
from sklearn.metrics import average_precision_score, roc_auc_score

from .dataset import CHEWING, NOT_CHEWING, EatingSession, _label_runs


def _safe_div(numerator: float, denominator: float) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def window_metrics(y: np.ndarray, p: np.ndarray, threshold: float) -> dict[str, Any]:
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=np.float64)
    predicted = p >= threshold
    tp = int(((y == 1) & predicted).sum())
    fp = int(((y == 0) & predicted).sum())
    tn = int(((y == 0) & ~predicted).sum())
    fn = int(((y == 1) & ~predicted).sum())
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else (0.0 if precision is not None or recall is not None else None)
    )
    fpr = _safe_div(fp, fp + tn)
    both = len(np.unique(y)) == 2
    specificity = None if fpr is None else 1.0 - fpr
    return {
        "threshold": float(threshold),
        "windows": int(len(y)),
        "positives": int((y == 1).sum()),
        "negatives": int((y == 0).sum()),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_positive_rate": fpr,
        "false_negative_rate": None if recall is None else 1.0 - recall,
        "specificity": specificity,
        "balanced_accuracy": (
            (recall + specificity) / 2 if recall is not None and specificity is not None else None
        ),
        "auc_roc": float(roc_auc_score(y, p)) if both else None,
        "auc_pr": float(average_precision_score(y, p)) if both else None,
        "positive_rate": float(y.mean()) if len(y) else None,
    }


def threshold_sweep(y: np.ndarray, p: np.ndarray, thresholds: Iterable[float] | None = None) -> list[dict[str, Any]]:
    grid = np.round(np.arange(0.05, 0.951, 0.05), 2) if thresholds is None else thresholds
    rows = []
    for threshold in grid:
        metrics = window_metrics(y, p, float(threshold))
        rows.append({k: metrics[k] for k in (
            "threshold", "precision", "recall", "f1", "false_positive_rate", "tp", "fp", "fn", "tn"
        )})
    return rows


def choose_threshold(y: np.ndarray, p: np.ndarray) -> float:
    """F1-optimal threshold on development predictions (ties → higher threshold)."""

    best_threshold, best_f1 = 0.5, -1.0
    for threshold in np.round(np.arange(0.05, 0.951, 0.01), 2):
        f1 = window_metrics(y, p, float(threshold))["f1"] or 0.0
        if f1 >= best_f1:
            best_threshold, best_f1 = float(threshold), f1
    return best_threshold


def nested_threshold_metrics(groups: np.ndarray, y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    """Window metrics when each group's threshold is chosen on the other groups.

    ``choose_threshold`` on all development predictions and scoring those same
    predictions lets the threshold see the windows it is scored on.  Here every
    held-out group (fold or sitting) is thresholded with the F1-optimal value
    of the remaining groups. This removes direct threshold selection on the
    scored group. It is not fully nested cross-validation: the other groups'
    out-of-fold models can have trained on the scored group.
    """

    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=np.float64)
    groups = np.asarray(groups)
    predicted = np.zeros(len(y), dtype=bool)
    thresholds: dict[str, float] = {}
    for group in sorted(set(groups.tolist()), key=str):
        held = groups == group
        if len(np.unique(y[~held])) < 2:
            return {"available": False, "reason": "a held-out group leaves only one class to choose a threshold on"}
        thresholds[str(group)] = choose_threshold(y[~held], p[~held])
        predicted[held] = p[held] >= thresholds[str(group)]
    tp = int((predicted & (y == 1)).sum())
    fp = int((predicted & (y == 0)).sum())
    fn = int((~predicted & (y == 1)).sum())
    tn = int((~predicted & (y == 0)).sum())
    precision, recall = _safe_div(tp, tp + fp), _safe_div(tp, tp + fn)
    return {
        "available": True,
        "thresholds": thresholds,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": _safe_div(2 * tp, 2 * tp + fp + fn),
        "false_positive_rate": _safe_div(fp, fp + tn),
    }


def exact_interval(successes: int, trials: int, level: float = 0.95) -> list[float] | None:
    """Clopper-Pearson interval for a proportion of independent recordings."""

    if trials <= 0:
        return None
    alpha = 1.0 - level
    low = 0.0 if successes == 0 else float(beta.ppf(alpha / 2, successes, trials - successes + 1))
    high = 1.0 if successes == trials else float(beta.ppf(1 - alpha / 2, successes + 1, trials - successes))
    return [low, high]


def recording_level(
    per_session: Mapping[str, Mapping[str, Any]], *, min_detected_s: float = 10.0
) -> dict[str, Any]:
    """One decision per recording: did the decoder report chewing in it?

    ``per_session`` holds :func:`bout_metrics` results.  A recording counts as
    flagged when its decoded bouts total at least ``min_detected_s`` seconds.
    Eating recordings have human chewing time; non-eating recordings have
    only human not-chewing time.  The intervals treat recordings as
    independent, which recordings of one sitting are not.
    """

    rows = []
    for session_id, item in sorted(per_session.items()):
        detected = float(sum(b["end"] - b["start"] for b in item.get("predicted_bout_spans", [])))
        kind = ("eating" if item.get("chewing_seconds", 0) > 0
                else "non_eating" if item.get("negative_seconds", 0) > 0 else "unlabelled")
        rows.append({"session_id": session_id, "kind": kind, "detected_chewing_s": round(detected, 2),
                     "bouts": int(item.get("predicted_bouts", 0)), "flagged": detected >= min_detected_s})
    eating = [r for r in rows if r["kind"] == "eating"]
    other = [r for r in rows if r["kind"] == "non_eating"]
    found, false = sum(r["flagged"] for r in eating), sum(r["flagged"] for r in other)
    return {
        "rule": f"decoded chewing bouts total at least {min_detected_s:g} s",
        "eating_recordings": len(eating),
        "eating_recordings_flagged": found,
        "eating_recall": _safe_div(found, len(eating)),
        "eating_recall_ci95": exact_interval(found, len(eating)),
        "non_eating_recordings": len(other),
        "non_eating_recordings_flagged": false,
        "non_eating_false_positive_rate": _safe_div(false, len(other)),
        "non_eating_false_positive_rate_ci95": exact_interval(false, len(other)),
        "recordings": rows,
    }


def session_bootstrap(
    session_ids: np.ndarray,
    y: np.ndarray,
    p: np.ndarray,
    statistic: Callable[[np.ndarray, np.ndarray], float | None],
    *,
    n_resamples: int = 1000,
    seed: int = 0,
) -> dict[str, float | None]:
    """95% percentile interval from resampling whole sessions with replacement."""

    sessions = np.unique(session_ids)
    if len(sessions) < 2:
        return {"low": None, "high": None, "resamples": 0}
    index_by_session = {s: np.flatnonzero(session_ids == s) for s in sessions}
    rng = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(n_resamples):
        chosen = rng.choice(sessions, size=len(sessions), replace=True)
        rows = np.concatenate([index_by_session[s] for s in chosen])
        value = statistic(y[rows], p[rows])
        if value is not None and np.isfinite(value):
            values.append(float(value))
    if len(values) < n_resamples * 0.5:
        return {"low": None, "high": None, "resamples": len(values)}
    return {
        "low": float(np.percentile(values, 2.5)),
        "high": float(np.percentile(values, 97.5)),
        "resamples": len(values),
    }


def grouped_window_metrics(
    groups: np.ndarray, y: np.ndarray, p: np.ndarray, threshold: float
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for group in sorted(set(map(str, groups))):
        mask = groups == group
        result[group] = window_metrics(y[mask], p[mask], threshold)
    return result


# --------------------------------------------------------------------------
# Bout replay (mirrors apps/live-ios ChewDetector)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DecoderConfig:
    high_threshold: float
    low_threshold: float
    alpha: float = 0.4
    min_start_windows: int = 3
    min_end_windows: int = 2

    @classmethod
    def from_threshold(cls, threshold: float) -> "DecoderConfig":
        # Live ships high/low = 0.6/0.4; keep that 0.2 hysteresis gap.
        return cls(high_threshold=threshold, low_threshold=max(0.02, threshold - 0.2))


def decode_bouts(times: np.ndarray, probability: np.ndarray, config: DecoderConfig) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Return per-step chewing state and bouts ``(start, end)`` in session time.

    A bout starts at the step where the state machine confirms chewing and
    ends at the step where it confirms idle (or the last step).  This is the
    latency a Live user would experience at the replay stride.
    """

    smoothed = 0.0
    state = False
    above = below = 0
    states = np.zeros(len(times), dtype=bool)
    bouts: list[tuple[float, float]] = []
    start: float | None = None
    for index, (t, p) in enumerate(zip(times, probability)):
        p = 0.0 if not np.isfinite(p) else float(p)
        smoothed = config.alpha * p + (1 - config.alpha) * smoothed
        if not state:
            if smoothed >= config.high_threshold:
                above += 1
                below = 0
                if above >= config.min_start_windows:
                    state, above, start = True, 0, float(t)
            else:
                above = 0
        else:
            if smoothed <= config.low_threshold:
                below += 1
                above = 0
                if below >= config.min_end_windows:
                    state, below = False, 0
                    bouts.append((float(start), float(t)))
                    start = None
            else:
                below = 0
        states[index] = state
    if state and start is not None:
        bouts.append((start, float(times[-1])))
    return states, bouts


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def bout_metrics(
    session: EatingSession,
    times: np.ndarray,
    probability: np.ndarray,
    config: DecoderConfig,
    *,
    min_reference_s: float = 3.0,
) -> dict[str, Any]:
    """Compare decoded bouts with the session's human chewing / not-chewing runs."""

    _, bouts = decode_bouts(times, probability, config)
    runs = _label_runs(session.intervals)
    chewing = [(r[0], r[1]) for r in runs if r[2] == CHEWING and r[1] - r[0] >= min_reference_s]
    negative = [(r[0], r[1]) for r in runs if r[2] == NOT_CHEWING]
    detected: list[dict[str, Any]] = []
    for reference in chewing:
        hits = [bout for bout in bouts if _overlap(bout, reference) > 0]
        latency = (min(b[0] for b in hits) - reference[0]) if hits else None
        covered = sum(_overlap(bout, reference) for bout in bouts)
        detected.append({
            "start": reference[0],
            "end": reference[1],
            "detected": bool(hits),
            "onset_latency_s": latency,
            "covered_fraction": covered / (reference[1] - reference[0]),
        })
    false_bouts: list[dict[str, Any]] = []
    scored_bouts = 0
    for bout in bouts:
        duration = max(bout[1] - bout[0], 1e-9)
        in_chewing = sum(_overlap(bout, r) for r in [(r[0], r[1]) for r in runs if r[2] == CHEWING])
        in_negative = sum(_overlap(bout, n) for n in negative)
        if in_chewing > 0:
            scored_bouts += 1
        elif in_negative / duration >= 0.5:
            scored_bouts += 1
            false_bouts.append({"start": bout[0], "end": bout[1]})
    negative_seconds = sum(n[1] - n[0] for n in negative)
    chewing_seconds = sum(r[1] - r[0] for r in runs if r[2] == CHEWING)
    false_seconds = sum(_overlap(b, n) for b in bouts for n in negative)
    true_seconds = sum(_overlap(b, (r[0], r[1])) for b in bouts for r in runs if r[2] == CHEWING)
    return {
        "reference_bouts": len(chewing),
        "detected_bouts": int(sum(item["detected"] for item in detected)),
        "predicted_bouts": len(bouts),
        "scored_predicted_bouts": scored_bouts,
        "false_bouts": len(false_bouts),
        "negative_seconds": negative_seconds,
        "chewing_seconds": chewing_seconds,
        "chewing_time_covered_s": true_seconds,
        "negative_time_flagged_s": false_seconds,
        "references": detected,
        "false_bout_spans": false_bouts,
        "predicted_bout_spans": [{"start": b[0], "end": b[1]} for b in bouts],
    }


def aggregate_bouts(per_session: Sequence[dict[str, Any]]) -> dict[str, Any]:
    references = sum(item["reference_bouts"] for item in per_session)
    detected = sum(item["detected_bouts"] for item in per_session)
    false_bouts = sum(item["false_bouts"] for item in per_session)
    scored = sum(item["scored_predicted_bouts"] for item in per_session)
    negative_hours = sum(item["negative_seconds"] for item in per_session) / 3600.0
    chewing_seconds = sum(item["chewing_seconds"] for item in per_session)
    latencies = [
        ref["onset_latency_s"]
        for item in per_session
        for ref in item["references"]
        if ref["onset_latency_s"] is not None
    ]
    return {
        "reference_bouts": references,
        "detected_bouts": detected,
        "bout_recall": _safe_div(detected, references),
        "false_bouts": false_bouts,
        "bout_precision": _safe_div(scored - false_bouts, scored),
        "false_bouts_per_negative_hour": _safe_div(false_bouts, negative_hours),
        "negative_hours": negative_hours,
        "chewing_time_recall": _safe_div(
            sum(item["chewing_time_covered_s"] for item in per_session), chewing_seconds
        ),
        "negative_time_flagged_fraction": _safe_div(
            sum(item["negative_time_flagged_s"] for item in per_session), negative_hours * 3600.0
        ),
        "onset_latency_median_s": float(np.median(latencies)) if latencies else None,
        "onset_latency_p90_s": float(np.percentile(latencies, 90)) if latencies else None,
    }
