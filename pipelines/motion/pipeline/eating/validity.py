"""Development-only validity study of the chewing-bout track.

The experiment (:mod:`.experiment`) answers "how well does the detector
score?".  This study asks whether that score means what it appears to mean:

* how many *independent* sittings stand behind the windows;
* whether cross-validation that splits one sitting inflates the result;
* whether the model separates chewing from pauses *inside* a meal, or only
  meals from other recordings (a recording-context shortcut);
* which part of the motion spectrum carries the evidence;
* how much the simplest possible detectors already achieve;
* whether more recordings of the same kind would still help;
* whether the resampling grid, the label clocks and the decision threshold
  distort the estimate.

Only development recordings are windowed, fitted or scored.  Locked-test
recordings contribute their capture time alone (to list sittings they share
with development data); ``splits.json`` and ``index.json`` are read, never
written, and the test-consultation count does not change.  Nothing here is a
model: no artifact is saved, registered, exported or deployed.
"""

from __future__ import annotations

import itertools
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..temporal import resample_motion_window
from .dataset import CHEWING, EatingSession, WindowSet, build_windows, dataset_fingerprint, discover_sessions
from .evaluate import (
    DecoderConfig,
    bout_metrics,
    choose_threshold,
    nested_threshold_metrics,
    recording_level,
    session_bootstrap,
    window_metrics,
)
from .features import SAMPLE_RATE_HZ, compute_features, feature_names
from .models import make_model
from .splits import DEV, TEST, development_folds, shared_sittings, sitting_groups


BANDS_HZ = (("0.3-5", 0.3, 5.0), ("5-12", 5.0, 12.0), ("12-25", 12.0, 25.01))
TABLE_BANDS_HZ = (("0.3-0.8", 0.3, 0.8), ("0.8-2.5", 0.8, 2.5), ("2.5-5", 2.5, 5.0),
                  ("5-12", 5.0, 12.0), ("12-25", 12.0, 25.01))
GRID_OFFSETS_MS = (0.0, 2.5, 5.0, 7.5, 10.0, 12.5, 15.0, 17.5)


def _round(value: Any, digits: int = 4) -> Any:
    if value is None:
        return None
    value = float(value)
    return round(value, digits) if np.isfinite(value) else None


def _auc(y: np.ndarray, p: np.ndarray) -> dict[str, float | None]:
    if len(np.unique(y)) < 2:
        return {"auc_roc": None, "auc_pr": None}
    return {"auc_roc": _round(roc_auc_score(y, p)), "auc_pr": _round(average_precision_score(y, p))}


def _estimator(name: str, seed: int = 0):
    if name == "band_logreg":
        return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000))
    return make_model(name, seed=seed).estimator


def band_power(windows: np.ndarray, bands: Sequence[tuple[str, float, float]]) -> dict[str, np.ndarray]:
    """Absolute band amplitude per window: accelerometer (g) and gyro (rad/s), axes combined.

    Returns ``{"accel:<band>": rms, "gyro:<band>": rms}`` where each value is
    the root of the summed one-sided power of the three axes in that band.
    """

    x = windows[:, :6, :].astype(np.float64)
    x = x - x.mean(axis=2, keepdims=True)
    samples = x.shape[2]
    taper = np.hanning(samples)
    power = np.abs(np.fft.rfft(x * taper, axis=2)) ** 2 / (SAMPLE_RATE_HZ * (taper**2).sum()) * 2
    freqs = np.fft.rfftfreq(samples, d=1.0 / SAMPLE_RATE_HZ)
    step = freqs[1] - freqs[0]
    result: dict[str, np.ndarray] = {}
    for name, lo, hi in bands:
        chosen = (freqs >= lo) & (freqs < hi)
        result[f"accel:{name}"] = np.sqrt(power[:, 0:3][:, :, chosen].sum(axis=(1, 2)) * step)
        result[f"gyro:{name}"] = np.sqrt(power[:, 3:6][:, :, chosen].sum(axis=(1, 2)) * step)
    return result


def band_filter(windows: np.ndarray, *, low_hz: float | None = None, high_hz: float | None = None) -> np.ndarray:
    """Zero spectral content below ``low_hz`` or above ``high_hz`` in every IMU channel."""

    filtered = windows.copy()
    spectrum = np.fft.rfft(filtered[:, :6, :], axis=2)
    freqs = np.fft.rfftfreq(windows.shape[2], d=1.0 / SAMPLE_RATE_HZ)
    if low_hz is not None:
        spectrum[:, :, freqs < low_hz] = 0
    if high_hz is not None:
        spectrum[:, :, freqs > high_hz] = 0
    filtered[:, :6, :] = np.fft.irfft(spectrum, n=windows.shape[2], axis=2)
    return filtered


class _Data:
    """Development windows with everything the analyses share."""

    def __init__(self, sessions: Sequence[EatingSession], sittings: Mapping[str, str],
                 nominal: WindowSet, aligned: WindowSet) -> None:
        self.sessions = list(sessions)
        self.by_id = {s.session_id: s for s in sessions}
        self.w = nominal
        self.y = nominal.label.astype(int)
        self.lab = nominal.label >= 0
        self.sid = nominal.session_id
        self.sitting = np.array([sittings[s] for s in self.sid])
        eating = [s.session_id for s in sessions if s.has_chewing]
        self.is_meal = np.isin(self.sid, eating)
        self.F = compute_features(nominal.X)
        self.F_aligned = compute_features(aligned.X)
        self.X_aligned = aligned.X
        self.names = feature_names()

    def held_out(
        self,
        groups: np.ndarray,
        *,
        model: str = "hgb",
        F: np.ndarray | None = None,
        F_score: np.ndarray | None = None,
        train_mask: np.ndarray | None = None,
        target: np.ndarray | None = None,
        columns: Sequence[int] | None = None,
    ) -> np.ndarray:
        """Predictions for every window from a model that never saw its group."""

        F = self.F if F is None else F
        F_score = F if F_score is None else F_score
        if columns is not None:
            F, F_score = F[:, columns], F_score[:, columns]
        target = self.y if target is None else target
        base = self.lab if train_mask is None else self.lab & train_mask
        p = np.full(len(self.y), np.nan)
        for group in np.unique(groups):
            fit = base & (groups != group)
            if len(np.unique(target[fit])) < 2:
                continue
            estimator = _estimator(model).fit(F[fit], target[fit])
            held = groups == group
            p[held] = estimator.predict_proba(F_score[held])[:, 1]
        return p

    def score(self, p: np.ndarray, groups: np.ndarray | None = None) -> dict[str, Any]:
        """Pooled and within-meal discrimination plus in-sample and nested thresholds."""

        rows = self.lab & np.isfinite(p)
        y, q = self.y[rows], p[rows]
        threshold = choose_threshold(y, q)
        at = window_metrics(y, q, threshold)
        meal = rows & self.is_meal
        result = {
            **_auc(y, q),
            "within_meal_auc_roc": _auc(self.y[meal], p[meal])["auc_roc"],
            "threshold_in_sample": threshold,
            "f1_in_sample_threshold": _round(at["f1"]),
            "recall": _round(at["recall"]),
            "false_positive_rate": _round(at["false_positive_rate"]),
            "false_positive_rate_meal_pauses": _round(np.mean(p[meal & (self.y == 0)] >= threshold))
            if (meal & (self.y == 0)).any() else None,
            "false_positive_rate_non_eating_recordings": _round(np.mean(p[rows & ~self.is_meal] >= threshold))
            if (rows & ~self.is_meal).any() else None,
        }
        if groups is not None:
            nested = nested_threshold_metrics(groups[rows], y, q)
            if nested.get("available"):
                result["nested_threshold"] = {k: _round(nested[k]) for k in ("f1", "precision", "recall", "false_positive_rate")}
        return result


def load_development(
    workspace: Path,
    *,
    canonical_roots: list[Path] | None = None,
    field_test_roots: list[Path] | None = None,
    loop_root: Path | None = None,
    splits_path: Path | None = None,
) -> tuple[list[EatingSession], list[EatingSession], dict[str, str], list[str]]:
    """Development recordings, all recordings, the split of each, and pending IDs.

    A recording the locked file does not know yet counts as development only
    when the loop gave it the ``training`` role (the rule ``assign_splits``
    applies); it is reported as pending.  Hash-assigned newcomers are left
    out, because this study must not decide their split.
    """

    workspace = Path(workspace)
    sessions, _ = discover_sessions(
        canonical_roots=canonical_roots if canonical_roots is not None else [
            workspace / "sessions" / "raw", workspace / "loop" / "assisted" / "sessions" / "raw"],
        field_test_roots=field_test_roots if field_test_roots is not None else [workspace / "sessions" / "field-tests"],
        loop_root=loop_root if loop_root is not None else workspace / "loop",
    )
    splits_path = splits_path or workspace / "experiments" / "eating" / "splits.json"
    assignments = json.loads(splits_path.read_text())["assignments"] if splits_path.exists() else {}
    split_of: dict[str, str] = {}
    pending: list[str] = []
    for session in sessions:
        known = assignments.get(session.session_id)
        if known:
            split_of[session.session_id] = known["split"]
        elif session.loop_role == "training":
            split_of[session.session_id] = DEV
            pending.append(session.session_id)
        elif session.loop_role == "evaluation":
            split_of[session.session_id] = TEST
    dev = [s for s in sessions if split_of.get(s.session_id) == DEV]
    return dev, sessions, split_of, pending


# ---------------------------------------------------------------- analyses


def _protocols(data: _Data, sittings: Mapping[str, str], folds: int, resamples: int, seed: int) -> dict[str, Any]:
    by_session = development_folds(data.sessions, k=folds)
    by_sitting = development_folds(data.sessions, k=folds, grouping="sitting", sittings=sittings)
    groupings = {
        f"{folds} folds dealt by recording (runs before 2026-11-10)": np.array([by_session[s] for s in data.sid]),
        f"{folds} folds dealt by sitting (experiment default)": np.array([by_sitting[s] for s in data.sid]),
        "leave one sitting out": data.sitting,
    }
    result: dict[str, Any] = {}
    predictions: dict[str, np.ndarray] = {}
    for model in ("logreg", "hgb"):
        for label, groups in groupings.items():
            p = data.held_out(groups, model=model)
            entry = data.score(p, groups)
            rows = data.lab & np.isfinite(p)
            entry["ci95_over_sittings"] = {
                "auc_pr": session_bootstrap(
                    data.sitting[rows], data.y[rows], p[rows],
                    lambda yy, pp: average_precision_score(yy, pp) if len(np.unique(yy)) == 2 else None,
                    n_resamples=resamples, seed=seed),
                "f1_in_sample_threshold": session_bootstrap(
                    data.sitting[rows], data.y[rows], p[rows],
                    lambda yy, pp, t=entry["threshold_in_sample"]: window_metrics(yy, pp, t)["f1"],
                    n_resamples=resamples, seed=seed),
            }
            result.setdefault(model, {})[label] = entry
            predictions[f"{model}|{label}"] = p
    result["_predictions"] = predictions  # removed before writing
    return result


def _per_sitting(data: _Data, p: np.ndarray) -> list[dict[str, Any]]:
    rows = data.lab & np.isfinite(p)
    predicted = np.zeros(len(p), dtype=bool)
    for sitting in np.unique(data.sitting[rows]):
        other = rows & (data.sitting != sitting)
        predicted[rows & (data.sitting == sitting)] = p[rows & (data.sitting == sitting)] >= choose_threshold(
            data.y[other], p[other])
    table = []
    for sitting in sorted(np.unique(data.sitting[rows])):
        here = rows & (data.sitting == sitting)
        chewing, other = here & (data.y == 1), here & (data.y == 0)
        table.append({
            "sitting": str(sitting),
            "recordings": sorted(set(data.sid[here].tolist())),
            "chewing_windows": int(chewing.sum()),
            "not_chewing_windows": int(other.sum()),
            "auc_roc": _auc(data.y[here], p[here])["auc_roc"],
            "recall": _round(predicted[chewing].mean()) if chewing.any() else None,
            "false_positive_rate": _round(predicted[other].mean()) if other.any() else None,
        })
    return table


def _within_meal_null(data: _Data, p: np.ndarray, permutations: int, seed: int) -> dict[str, Any]:
    """Is within-meal discrimination more than label autocorrelation could produce?

    Each eating recording's prediction track is shifted in time by a random
    offset (wrapping around) and re-scored against the unmoved labels.  Runs
    of chewing and pausing keep their lengths, so the null respects the
    temporal structure that plain shuffling would destroy.
    """

    rng = np.random.default_rng(seed)
    tracks = []
    for session in data.sessions:
        if not session.has_chewing:
            continue
        rows = np.flatnonzero(data.sid == session.session_id)
        rows = rows[np.argsort(data.w.t_end[rows])]
        tracks.append(rows)

    def pooled(shift: bool) -> float | None:
        ys, ps = [], []
        for rows in tracks:
            q = p[rows]
            if shift and len(rows) > 40:
                q = np.roll(q, int(rng.integers(20, len(rows) - 20)))
            keep = data.lab[rows] & np.isfinite(q)
            ys.append(data.y[rows][keep])
            ps.append(q[keep])
        y, q = np.concatenate(ys), np.concatenate(ps)
        return float(roc_auc_score(y, q)) if len(np.unique(y)) == 2 else None

    observed = pooled(False)
    null = np.array([value for value in (pooled(True) for _ in range(permutations)) if value is not None])
    return {
        "observed_within_meal_auc_roc": _round(observed),
        "null_mean": _round(null.mean()) if len(null) else None,
        "null_p97_5": _round(np.percentile(null, 97.5)) if len(null) else None,
        "null_max": _round(null.max()) if len(null) else None,
        "permutations": int(len(null)),
        "p_value": _round((1 + int((null >= observed).sum())) / (1 + len(null))) if observed is not None and len(null) else None,
        "method": "circular time shift of each eating recording's prediction track (at least 10 s)",
    }


def _context_transfer(data: _Data) -> dict[str, Any]:
    y, lab, meal, sitting = data.y, data.lab, data.is_meal, data.sitting
    pauses = lab & meal & (y == 0)
    reference = data.held_out(sitting)
    reference_threshold = choose_threshold(y[lab], reference[lab])

    no_pauses = data.held_out(sitting, train_mask=~(meal & (y == 0)))
    rows = lab & np.isfinite(no_pauses)
    threshold = choose_threshold(y[rows], no_pauses[rows])

    meals_only = data.held_out(sitting, train_mask=meal)
    rows_meal = lab & meal & np.isfinite(meals_only)
    threshold_meal = choose_threshold(y[rows_meal], meals_only[rows_meal])
    non_eating = lab & ~meal & np.isfinite(meals_only)

    negatives = lab & (y == 0)
    probe = data.held_out(sitting, target=meal.astype(int), train_mask=(y == 0))
    scored = negatives & np.isfinite(probe)
    return {
        "trained_without_meal_pauses": {
            "question": "Does a model that never saw a pause inside a meal call those pauses chewing?",
            "false_positive_rate_on_held_out_meal_pauses": _round(np.mean(no_pauses[pauses] >= threshold)),
            "reference_with_pauses_in_training": _round(np.mean(reference[pauses] >= reference_threshold)),
            "within_meal_auc_roc": _auc(y[lab & meal], no_pauses[lab & meal])["auc_roc"],
        },
        "trained_on_eating_recordings_only": {
            "question": "Does a model that only ever saw meals stay quiet in recordings without food?",
            "within_meal_auc_roc": _auc(y[rows_meal], meals_only[rows_meal])["auc_roc"],
            "false_positive_rate_on_non_eating_recordings": _round(np.mean(meals_only[non_eating] >= threshold_meal)),
            "reference_with_non_eating_recordings_in_training": _round(
                np.mean(reference[lab & ~meal] >= reference_threshold)),
        },
        "context_probe": {
            "question": "Can motion tell a pause inside a meal from a recording without food? (0.5 = no shortcut available)",
            "auc_roc": _round(roc_auc_score(meal[scored].astype(int), probe[scored]))
            if len(np.unique(meal[scored])) == 2 else None,
        },
    }


def _band_table(data: _Data) -> list[dict[str, Any]]:
    amplitudes = band_power(data.X_aligned, TABLE_BANDS_HZ)
    full = data.w.valid_fraction >= 0.999
    table = []
    keys = sorted({(context, int(label)) for context, label in zip(data.w.context[data.lab], data.y[data.lab])})
    for context, label in keys:
        rows = data.lab & full & (data.w.context == context) & (data.y == label)
        if rows.sum() < 5:
            continue
        table.append({
            "context": context,
            "label": "chewing" if label == 1 else "not_chewing",
            "windows": int(rows.sum()),
            "accel_mg": {name: _round(np.median(amplitudes[f"accel:{name}"][rows]) * 1e3, 2) for name, _, _ in TABLE_BANDS_HZ},
            "gyro_mrad_s": {name: _round(np.median(amplitudes[f"gyro:{name}"][rows]) * 1e3, 2) for name, _, _ in TABLE_BANDS_HZ},
        })
    return table


def _mechanism(data: _Data) -> dict[str, Any]:
    names = data.names

    def where(test: Callable[[str], bool]) -> list[int]:
        return [i for i, name in enumerate(names) if test(name)]

    def brief(p: np.ndarray) -> dict[str, Any]:
        scored = data.score(p)
        return {k: scored[k] for k in ("auc_pr", "auc_roc", "within_meal_auc_roc")}

    subsets = {
        "all 80 features": list(range(len(names))),
        "gyro features only": where(lambda n: n.startswith(("gx", "gy", "gz", "gyro"))),
        "accelerometer features only": where(lambda n: n.startswith(("ax", "ay", "az", "accel"))),
        "rhythm only (dominant frequency, chew-lag autocorrelation)": where(lambda n: "dominant_hz" in n or "chew_autocorr" in n),
        "amplitude only (8 log RMS)": where(lambda n: "log_rms" in n),
    }
    feature_subsets = {label: brief(data.held_out(data.sitting, columns=columns)) for label, columns in subsets.items()}
    filtered = {}
    for label, kwargs in (
        ("content above 12 Hz removed", {"high_hz": 12.0}),
        ("content above 5 Hz removed", {"high_hz": 5.0}),
        ("content above 2.5 Hz removed (chewing rhythm and slower only)", {"high_hz": 2.5}),
        ("content below 5 Hz removed", {"low_hz": 5.0}),
        ("content below 12 Hz removed", {"low_hz": 12.0}),
    ):
        filtered[label] = brief(data.held_out(data.sitting, F=compute_features(band_filter(data.w.X, **kwargs))))
    singles = []
    for index, name in enumerate(names):
        value = roc_auc_score(data.y[data.lab], data.F[data.lab, index])
        singles.append({"feature": name, "auc_roc": _round(value),
                        "within_meal_auc_roc": _round(roc_auc_score(
                            data.y[data.lab & data.is_meal], data.F[data.lab & data.is_meal, index]))})
    singles.sort(key=lambda row: -abs(row["auc_roc"] - 0.5))
    return {
        "band_amplitude_by_context": _band_table(data),
        "feature_subsets_hgb_leave_one_sitting_out": feature_subsets,
        "band_limited_windows_hgb_leave_one_sitting_out": filtered,
        "single_features_no_fitting_top10": singles[:10],
        "note": "auc_roc below 0.5 for a single feature means it is lower during chewing",
    }


def _simple_models(data: _Data, reference: Mapping[str, Any]) -> dict[str, Any]:
    amplitudes = band_power(data.X_aligned, BANDS_HZ)
    logs = {name: np.log10(value + 1e-9) for name, value in amplitudes.items()}
    result: dict[str, Any] = {}
    for name in ("accel:12-25", "gyro:12-25"):
        result[f"threshold on {name} Hz amplitude (no fitting)"] = {
            "auc_roc": _round(roc_auc_score(data.y[data.lab], logs[name][data.lab])),
            "within_meal_auc_roc": _round(roc_auc_score(
                data.y[data.lab & data.is_meal], logs[name][data.lab & data.is_meal])),
        }
    for label, columns in (
        ("logistic regression, 2 amplitudes (12-25 Hz accel and gyro)", ["accel:12-25", "gyro:12-25"]),
        ("logistic regression, 6 band amplitudes", list(logs)),
    ):
        matrix = np.column_stack([logs[c] for c in columns])
        p = data.held_out(data.sitting, model="band_logreg", F=matrix)
        scored = data.score(p, data.sitting)
        result[label] = {k: scored.get(k) for k in (
            "auc_pr", "auc_roc", "within_meal_auc_roc", "nested_threshold", "false_positive_rate_non_eating_recordings")}
    result["reference: logistic regression, 80 features"] = reference["logreg"]
    result["reference: gradient boosting, 80 features"] = reference["hgb"]
    return result


def _learning_curve(data: _Data, seed: int) -> dict[str, Any]:
    """Held-out discrimination against the number of meal sittings in training."""

    meal_sittings = sorted(set(data.sitting[data.is_meal].tolist()))
    other_sittings = sorted(set(data.sitting[~data.is_meal].tolist()))
    if len(meal_sittings) < 3 or len(other_sittings) < 3:
        return {"available": False, "reason": "needs at least three sittings with chewing and three without"}
    rng = np.random.default_rng(seed)
    rows_out = []
    for count in range(1, len(meal_sittings)):
        values = []
        for held in meal_sittings:
            others = [m for m in meal_sittings if m != held]
            for combo in itertools.combinations(others, count):
                held_negative = rng.choice(other_sittings, size=2, replace=False)
                fit = data.lab & (np.isin(data.sitting, combo) | (~data.is_meal & ~np.isin(data.sitting, held_negative)))
                test = data.lab & ((data.sitting == held) | np.isin(data.sitting, held_negative))
                if len(np.unique(data.y[fit])) < 2 or len(np.unique(data.y[test])) < 2:
                    continue
                estimator = _estimator("hgb").fit(data.F[fit], data.y[fit])
                p = estimator.predict_proba(data.F[test])[:, 1]
                values.append((average_precision_score(data.y[test], p), roc_auc_score(data.y[test], p)))
        if values:
            array = np.array(values)
            rows_out.append({
                "meal_sittings_in_training": count, "evaluations": len(values),
                "auc_pr_mean": _round(array[:, 0].mean()), "auc_pr_sd": _round(array[:, 0].std()),
                "auc_pr_min": _round(array[:, 0].min()), "auc_roc_mean": _round(array[:, 1].mean()),
            })
    return {"available": True, "rows": rows_out,
            "method": "hold out one meal sitting and two non-eating sittings; train on k other meal sittings and the remaining non-eating sittings"}


def _grid(data: _Data, reference_nominal: np.ndarray, seed: int) -> dict[str, Any]:
    clocks = []
    for session in data.sessions:
        dt = np.diff(session.motion_t) * 1e3
        steady = dt[dt < 30]
        # Rate of the AirPods sample clock against the phone clock, from the longest
        # stretch without a dropped sample: it sets how fast the grid offset drifts.
        edges = np.concatenate(([0], np.flatnonzero(dt > 30) + 1, [len(session.motion_t)]))
        longest = int(np.argmax(np.diff(edges)))
        run = session.motion_t[edges[longest]:edges[longest + 1]]
        ppm = (np.polyfit(np.arange(len(run)), run, 1)[0] * SAMPLE_RATE_HZ - 1) * 1e6 if len(run) >= 500 else None
        clocks.append({
            "session_id": session.session_id,
            "median_interval_ms": _round(np.median(dt), 3),
            "interval_sd_ms": _round(steady.std(), 4) if len(steady) else None,
            "clock_rate_offset_ppm": _round(ppm, 1),
            "minutes_per_full_grid_cycle": _round(1e6 / abs(ppm) / SAMPLE_RATE_HZ / 60, 1) if ppm else None,
            "single_dropped_samples": int((np.abs(dt - 40) < 1).sum()),
            "gaps_over_50_ms": int((dt > 50).sum()),
        })
    rng = np.random.default_rng(seed)
    ratios = []
    for session in data.sessions:
        ends = np.arange(3.0, session.duration_s - 0.1, 0.5)
        if len(ends) > 60:
            ends = np.sort(rng.choice(ends, size=60, replace=False))
        medians = []
        for offset in GRID_OFFSETS_MS:
            windows = []
            for end in ends:
                window, _ = resample_motion_window(session.motion_t, session.imu, end_timestamp=float(end) + offset / 1e3)
                if window[6].mean() >= 0.999:
                    windows.append(window)
            if len(windows) < 10:
                break
            amplitude = band_power(np.stack(windows), (("12-25", 12.0, 25.01),))
            medians.append(float(np.median(amplitude["accel:12-25"])) + 0.0)
        if len(medians) == len(GRID_OFFSETS_MS):
            ratios.append({"session_id": session.session_id, "max_over_min": _round(max(medians) / min(medians), 3)})
    values = [row["max_over_min"] for row in ratios]

    aligned_scores = data.held_out(data.sitting, F=data.F_aligned)
    skew = data.held_out(data.sitting, F=data.F, F_score=data.F_aligned)
    rows = data.lab
    threshold = choose_threshold(data.y[rows], reference_nominal[rows])

    def at(p: np.ndarray) -> dict[str, Any]:
        return {"false_positive_rate": _round(np.mean(p[rows & (data.y == 0)] >= threshold)),
                "recall": _round(np.mean(p[rows & (data.y == 1)] >= threshold))}

    return {
        "sample_clock": {
            "median_interval_sd_ms": _round(np.median([c["interval_sd_ms"] for c in clocks if c["interval_sd_ms"] is not None]), 4),
            "clock_rate_offset_ppm_range": [
                _round(func([c["clock_rate_offset_ppm"] for c in clocks if c["clock_rate_offset_ppm"] is not None]), 1)
                for func in (min, max)] if any(c["clock_rate_offset_ppm"] is not None for c in clocks) else None,
            "minutes_per_full_grid_cycle_median": _round(np.median(
                [c["minutes_per_full_grid_cycle"] for c in clocks if c["minutes_per_full_grid_cycle"]]), 1)
            if any(c["minutes_per_full_grid_cycle"] for c in clocks) else None,
            "recordings": clocks,
        },
        "accel_12_25_hz_amplitude_vs_grid_offset": {
            "offsets_ms": list(GRID_OFFSETS_MS),
            "max_over_min_median": _round(np.median(values), 3) if values else None,
            "max_over_min_largest": _round(max(values), 3) if values else None,
            "recordings": ratios,
            "meaning": "ratio of the largest to the smallest median amplitude when only the sub-sample grid offset changes",
        },
        "trained_and_scored_on_sample_grid": data.score(aligned_scores, data.sitting),
        "trained_on_nominal_scored_on_sample_grid": {
            **{k: v for k, v in data.score(skew).items() if k in ("auc_pr", "auc_roc")},
            "threshold_from_nominal_development_predictions": threshold,
            "nominal_grid_at_that_threshold": at(reference_nominal),
            "sample_grid_at_that_threshold": at(skew),
            "meaning": "Live starts every session with its grid on a sample; this is that case scored by a model trained on the nominal grid",
        },
    }


def _label_alignment(data: _Data) -> list[dict[str, Any]]:
    """Model-free check of label timing against the signal, per eating recording.

    The score is the 1.5 s RMS of the gyro's second difference (content near
    the top of the band), computed from raw samples.  Labels are shifted in
    time; the shift with the best separation is the implied label lag.
    """

    deltas = np.arange(-6.0, 6.01, 0.25)
    table = []
    for session in data.sessions:
        if not session.has_chewing:
            continue
        t, x = session.motion_t, session.imu.astype(np.float64)
        second = np.diff(x[:, 3:6], n=2, axis=0)
        steady = (np.diff(t)[1:] < 0.03) & (np.diff(t)[:-1] < 0.03)
        energy = (second**2).sum(axis=1)
        energy[~steady] = np.nan
        times = t[1:-1]
        centers = np.arange(1.0, session.duration_s - 1.0, 0.25)
        score = np.full(len(centers), np.nan)
        for index, center in enumerate(centers):
            values = energy[(times >= center - 0.75) & (times <= center + 0.75)]
            if np.isfinite(values).sum() > 30:
                score[index] = np.sqrt(np.nanmean(values))
        curve = []
        for delta in deltas:
            label = np.full(len(centers), -1)
            for item in session.intervals:
                inside = (centers + delta >= item.start) & (centers + delta < item.end)
                label[inside] = 1 if item.label == CHEWING else 0
            rows = (label >= 0) & np.isfinite(score)
            curve.append(roc_auc_score(label[rows], score[rows]) if len(np.unique(label[rows])) == 2 else np.nan)
        curve_array = np.array(curve)
        if not np.isfinite(curve_array).any():
            continue
        best = int(np.nanargmax(curve_array))
        near = deltas[curve_array >= curve_array[best] - 0.005]
        table.append({
            "session_id": session.session_id, "clock": session.clock,
            "auc_roc_as_labelled": _round(curve_array[int(np.argmin(np.abs(deltas)))]),
            "best_auc_roc": _round(curve_array[best]),
            "implied_label_lag_s": _round(deltas[best], 2),
            "lags_within_0.005_of_best_s": [_round(near.min(), 2), _round(near.max(), 2)],
        })
    return table


def _recordings(data: _Data, p: np.ndarray) -> dict[str, Any]:
    rows = data.lab & np.isfinite(p)
    threshold = choose_threshold(data.y[rows], p[rows])
    result = {}
    for label, start_windows in (("Live decoder (1.5 s to start)", 3), ("pre-registered 3 s persistence", 6)):
        decoder = DecoderConfig(high_threshold=threshold, low_threshold=max(0.02, threshold - 0.2),
                                min_start_windows=start_windows)
        per_session = {}
        for session in data.sessions:
            here = np.flatnonzero(data.sid == session.session_id)
            here = here[np.argsort(data.w.t_end[here])]
            per_session[session.session_id] = bout_metrics(session, data.w.t_end[here], p[here], decoder)
        result[label] = recording_level(per_session)
    return result


def _instructions(data: _Data) -> list[dict[str, Any]]:
    """Guided chew instructions against the wearer's own chewing spans."""

    table = []
    for session in data.sessions:
        instructed = [(a, b) for a, b, name in session.context_spans if name.startswith("guided:chew")]
        marked = [(i.start, i.end) for i in session.intervals if i.label == CHEWING and i.tier == "human_coarse"]
        for start, end in instructed:
            for chew_start, chew_end in marked:
                if min(end, chew_end) - max(start, chew_start) > 0:
                    table.append({
                        "session_id": session.session_id,
                        "instructed_s": [_round(start, 2), _round(end, 2)],
                        "marked_by_wearer_s": [_round(chew_start, 2), _round(chew_end, 2)],
                        "onset_lag_s": _round(chew_start - start, 2),
                        "offset_lag_s": _round(chew_end - end, 2),
                    })
    return table


# ------------------------------------------------------------------- driver


def _git_state(repo: Path | None) -> dict[str, Any]:
    if repo is None:
        return {}

    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def run_validity_study(
    *,
    workspace: Path,
    output_root: Path | None = None,
    repo_root: Path | None = None,
    canonical_roots: list[Path] | None = None,
    field_test_roots: list[Path] | None = None,
    loop_root: Path | None = None,
    folds: int = 5,
    permutations: int = 500,
    bootstrap_resamples: int = 1000,
    seed: int = 0,
    log=print,
) -> Path:
    workspace = Path(workspace)
    eating_root = Path(output_root) if output_root else workspace / "experiments" / "eating"
    index_path = eating_root / "index.json"
    consultations_before = (json.loads(index_path.read_text()).get("test_consultations")
                            if index_path.exists() else None)
    dev, sessions, split_of, pending = load_development(
        workspace, canonical_roots=canonical_roots, field_test_roots=field_test_roots, loop_root=loop_root,
        splits_path=eating_root / "splits.json")
    if not dev or not any(s.has_chewing for s in dev):
        raise RuntimeError("no development recordings with chewing labels were found")
    sittings = sitting_groups(sessions)
    started = datetime.now(timezone.utc)
    run_dir = eating_root / "validity" / started.strftime("validity-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    log(f"Validity study {run_dir.name}: {len(dev)} development recordings (locked test not windowed)")

    nominal = build_windows(dev)
    aligned = build_windows(dev, grid="sample")
    data = _Data(dev, sittings, nominal, aligned)
    dev_sittings = sorted({sittings[s.session_id] for s in dev})
    chewing_sittings = sorted({sittings[s.session_id] for s in dev if s.has_chewing})
    if len(chewing_sittings) < 2:
        raise RuntimeError("at least two development sittings with chewing are needed")

    log("  cross-validation protocols")
    protocols = _protocols(data, sittings, folds, bootstrap_resamples, seed)
    predictions = protocols.pop("_predictions")
    loso = predictions["hgb|leave one sitting out"]
    log("  within-meal null, context transfer")
    null = _within_meal_null(data, loso, permutations, seed)
    context = _context_transfer(data)
    log("  mechanism and simple models")
    mechanism = _mechanism(data)
    simple = _simple_models(data, {
        model: {k: protocols[model]["leave one sitting out"].get(k) for k in (
            "auc_pr", "auc_roc", "within_meal_auc_roc", "nested_threshold", "false_positive_rate_non_eating_recordings")}
        for model in ("logreg", "hgb")})
    log("  learning curve, resampling grid, labels, recordings")
    result = {
        "artifact_type": "eating_validity_study",
        "study_id": run_dir.name,
        "started_at": started.isoformat(),
        "git": _git_state(repo_root),
        "scope": {
            "split": "development only",
            "locked_test_windows_built": 0,
            "locked_test_use": "capture times only, to list sittings shared with development",
            "test_consultations_before": consultations_before,
            "pending_assignment_as_development": pending,
            "dataset_fingerprint": dataset_fingerprint(dev, {"window_s": 3.0, "stride_s": 0.5, "guard_s": 1.0}),
        },
        "independence": {
            "development_recordings": len(dev),
            "development_recordings_with_chewing": sum(1 for s in dev if s.has_chewing),
            "development_sittings": len(dev_sittings),
            "development_sittings_with_chewing": len(chewing_sittings),
            "sittings": {
                sitting: sorted(s.session_id for s in dev if sittings[s.session_id] == sitting)
                for sitting in dev_sittings},
            "test_recordings_sharing_a_development_sitting": shared_sittings(
                {s.session_id: sittings[s.session_id] for s in sessions}, split_of),
            "labelled_windows": int(data.lab.sum()),
            "chewing_windows": int((data.y == 1).sum()),
        },
        "protocols": protocols,
        "per_sitting_hgb_leave_one_sitting_out": _per_sitting(data, loso),
        "within_meal_null": null,
        "context_transfer": context,
        "mechanism": mechanism,
        "simple_models": simple,
        "learning_curve": _learning_curve(data, seed),
        "resampling_grid": _grid(data, loso, seed),
        "label_alignment": _label_alignment(data),
        "recording_level_hgb_leave_one_sitting_out": _recordings(data, loso),
        "instruction_vs_wearer_spans": _instructions(data),
    }
    consultations_after = (json.loads(index_path.read_text()).get("test_consultations")
                           if index_path.exists() else None)
    result["scope"]["test_consultations_after"] = consultations_after
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    (run_dir / "validity.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=float) + "\n")
    (run_dir / "summary.md").write_text(render_summary(result))
    (eating_root / "validity" / "latest.json").write_text(json.dumps(
        {"study_id": run_dir.name, "path": str(run_dir / "validity.json"), "summary": str(run_dir / "summary.md")},
        indent=2) + "\n")
    log(f"  wrote {run_dir / 'summary.md'}")
    return run_dir


def render_summary(result: Mapping[str, Any]) -> str:
    """Plain-language summary of one study (numbers only from ``result``)."""

    ind, scope = result["independence"], result["scope"]
    lines = [
        f"# Chewing-bout validity study {result['study_id']}",
        "",
        "Development recordings of one wearer only. The locked test was not windowed, fitted or scored "
        f"(test consultations before and after: {scope['test_consultations_before']} and {scope['test_consultations_after']}).",
        "",
        "## Independent evidence",
        f"- {ind['development_recordings']} recordings are {ind['development_sittings']} sittings; "
        f"{ind['development_recordings_with_chewing']} recordings with chewing are "
        f"{ind['development_sittings_with_chewing']} sittings.",
    ]
    shared = ind["test_recordings_sharing_a_development_sitting"]
    lines.append("- Locked-test recordings sharing a sitting with development: "
                 + (", ".join(item["session_id"] for item in shared) if shared else "none") + ".")
    lines += ["", "## Cross-validation protocol (gradient boosting, 80 features)",
              "| Protocol | AUC-PR | F1, in-sample threshold | F1, threshold from other folds | Within-meal AUC-ROC |",
              "| --- | --- | --- | --- | --- |"]
    for label, entry in result["protocols"]["hgb"].items():
        nested = (entry.get("nested_threshold") or {}).get("f1")
        ci = entry["ci95_over_sittings"]["auc_pr"]
        lines.append(f"| {label} | {entry['auc_pr']} [{_round(ci['low'], 3)}-{_round(ci['high'], 3)}] | "
                     f"{entry['f1_in_sample_threshold']} | {nested} | {entry['within_meal_auc_roc']} |")
    null = result["within_meal_null"]
    lines += ["", "## Chewing or recording context?",
              f"- Within-meal AUC-ROC {null['observed_within_meal_auc_roc']}; time-shift null mean {null['null_mean']}, "
              f"largest of {null['permutations']} {null['null_max']} (p = {null['p_value']}).",
              f"- Context probe (pause inside a meal vs recording without food): AUC-ROC "
              f"{result['context_transfer']['context_probe']['auc_roc']}.",
              f"- Trained on meals only, false-positive rate in recordings without food: "
              f"{result['context_transfer']['trained_on_eating_recordings_only']['false_positive_rate_on_non_eating_recordings']} "
              f"(with those recordings in training: "
              f"{result['context_transfer']['trained_on_eating_recordings_only']['reference_with_non_eating_recordings_in_training']})."]
    lines += ["", "## Where the evidence is in the spectrum (leave one sitting out)",
              "| Windows | AUC-PR | Within-meal AUC-ROC |", "| --- | --- | --- |"]
    for label, entry in result["mechanism"]["band_limited_windows_hgb_leave_one_sitting_out"].items():
        lines.append(f"| {label} | {entry['auc_pr']} | {entry['within_meal_auc_roc']} |")
    for label, entry in result["mechanism"]["feature_subsets_hgb_leave_one_sitting_out"].items():
        lines.append(f"| {label} | {entry['auc_pr']} | {entry['within_meal_auc_roc']} |")
    lines += ["", "## Simplest models (leave one sitting out)",
              "| Model | AUC-PR | AUC-ROC | Within-meal AUC-ROC | F1, nested threshold |", "| --- | --- | --- | --- | --- |"]
    for label, entry in result["simple_models"].items():
        lines.append(f"| {label} | {entry.get('auc_pr')} | {entry.get('auc_roc')} | {entry.get('within_meal_auc_roc')} | "
                     f"{(entry.get('nested_threshold') or {}).get('f1')} |")
    curve = result["learning_curve"]
    if curve.get("available"):
        lines += ["", "## Learning curve", "| Meal sittings in training | AUC-PR mean | sd | min |", "| --- | --- | --- | --- |"]
        lines += [f"| {row['meal_sittings_in_training']} | {row['auc_pr_mean']} | {row['auc_pr_sd']} | {row['auc_pr_min']} |"
                  for row in curve["rows"]]
    grid = result["resampling_grid"]
    skew = grid["trained_on_nominal_scored_on_sample_grid"]
    lines += ["", "## Resampling grid",
              f"- Sample interval sd (median over recordings): {grid['sample_clock']['median_interval_sd_ms']} ms.",
              f"- AirPods clock against the phone clock: {grid['sample_clock']['clock_rate_offset_ppm_range']} ppm, so the grid "
              f"offset completes a cycle about every {grid['sample_clock']['minutes_per_full_grid_cycle_median']} minutes (median).",
              f"- 12-25 Hz accelerometer amplitude changes by a factor of "
              f"{grid['accel_12_25_hz_amplitude_vs_grid_offset']['max_over_min_median']} (median; largest "
              f"{grid['accel_12_25_hz_amplitude_vs_grid_offset']['max_over_min_largest']}) with the sub-sample grid offset alone.",
              f"- Model trained on the nominal grid, threshold {skew['threshold_from_nominal_development_predictions']}: "
              f"false-positive rate {skew['nominal_grid_at_that_threshold']['false_positive_rate']} on the nominal grid, "
              f"{skew['sample_grid_at_that_threshold']['false_positive_rate']} on the sample grid "
              f"(recall {skew['nominal_grid_at_that_threshold']['recall']} and {skew['sample_grid_at_that_threshold']['recall']}).",
              f"- Trained and scored on the sample grid: AUC-PR {grid['trained_and_scored_on_sample_grid']['auc_pr']}."]
    lines += ["", "## Label timing (model-free)", "| Recording | Clock | Implied label lag (s) | AUC-ROC as labelled | Best |",
              "| --- | --- | --- | --- | --- |"]
    lines += [f"| {row['session_id']} | {row['clock']} | {row['implied_label_lag_s']} | {row['auc_roc_as_labelled']} | {row['best_auc_roc']} |"
              for row in result["label_alignment"]]
    lines += ["", "## One decision per recording"]
    for label, block in result["recording_level_hgb_leave_one_sitting_out"].items():
        lines.append(f"- {label}: eating recordings flagged {block['eating_recordings_flagged']}/{block['eating_recordings']}, "
                     f"non-eating recordings flagged {block['non_eating_recordings_flagged']}/{block['non_eating_recordings']} "
                     f"(95% interval for that rate {[_round(v, 3) for v in block['non_eating_false_positive_rate_ci95'] or []]}).")
    return "\n".join(lines) + "\n"
