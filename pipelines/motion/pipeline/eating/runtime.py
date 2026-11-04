"""Deployable eating-detector specification and its Python reference runtime.

An experiment's selected feature model is exported once into a versioned,
self-describing JSON specification.  The specification fixes everything an
on-device implementation needs to reproduce the Python decision exactly:

* input contract: channels, units, float32 value precision, 50 Hz grid,
  3 s window, 0.08 s maximum interpolation gap, validity rule;
* feature constants resolved by numpy (taper, FFT bin indices per band,
  autocorrelation lags) so no implementation re-derives a boundary;
* the gradient-boosted trees (``feature <= threshold`` goes left, NaN follows
  ``missing_left``) and the sigmoid link;
* the decision: development-chosen threshold, evaluation stride and the
  EMA + hysteresis decoder that mirrors Live's ``ChewDetector``.

:func:`run_stream` is the reference the Swift runtime is checked against on
identical recorded inputs.  It reuses the experiment's resampler, features and
decoder; the only behaviour it defines beyond the experiment's replay is that
a window with too little measured motion contributes probability 0 instead of
a model output (the experiment applied that only inside labelled time).

Nothing here promotes a model.  An exported specification is an experimental
candidate until physical-device evidence and an explicit decision say
otherwise.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from ..temporal import resample_motion_window
from .evaluate import DecoderConfig, decode_bouts
from .features import BANDS, CHEW_LAG_SECONDS, SAMPLE_RATE_HZ, SIGNALS, compute_features, feature_names

SPEC_FORMAT = "chewsense.eating-detector"
SPEC_FORMAT_VERSION = 1
WINDOW_S = 3.0
MAX_GAP_S = 0.08


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def feature_constants(samples: int = 150, sample_rate_hz: int = SAMPLE_RATE_HZ) -> dict[str, Any]:
    """Constants of :func:`features.compute_features`, resolved with numpy."""

    freqs = np.fft.rfftfreq(samples, d=1.0 / sample_rate_hz)

    def bins(mask: np.ndarray) -> list[int]:
        index = np.flatnonzero(mask)
        if not len(index) or not np.array_equal(index, np.arange(index[0], index[-1] + 1)):
            raise AssertionError("feature bins must be one contiguous range")
        return [int(index[0]), int(index[-1])]

    return {
        "names": feature_names(),
        "signals": list(SIGNALS),
        "per_signal": [
            "log_rms", *(f"band_{band}" for band, _, _ in BANDS),
            "dominant_hz", "chew_peak_ratio", "spectral_entropy", "chew_autocorr",
        ],
        "taper": np.hanning(samples).tolist(),
        "frequencies_hz": freqs.tolist(),
        "analysed_bins": bins((freqs >= 0.3) & (freqs <= 25.01)),
        "bands": [
            {"name": band, "bins": bins((freqs >= lo) & (freqs < hi))} for band, lo, hi in BANDS
        ],
        "dominant_bins": bins((freqs >= 0.5) & (freqs <= 5.0)),
        "chew_bins": bins((freqs >= 0.8) & (freqs < 2.5)),
        # Python's round() is round-half-even: 16.5 -> 16 and 62.5 -> 62.
        "autocorr_lags": [
            int(round(CHEW_LAG_SECONDS[0] * sample_rate_hz)),
            min(int(round(CHEW_LAG_SECONDS[1] * sample_rate_hz)), samples - 2),
        ],
        "rms_log_epsilon": 1e-6,
        "power_epsilon": 1e-12,
        "output_precision": "float32",
    }


def _trees_from_hgb(estimator) -> tuple[float, list[dict[str, list]]]:
    baseline = np.asarray(estimator._baseline_prediction, dtype=np.float64).ravel()
    if baseline.shape != (1,):
        raise ValueError("only binary gradient boosting can be exported")
    trees = []
    for iteration in estimator._predictors:
        if len(iteration) != 1:
            raise ValueError("only binary gradient boosting can be exported")
        nodes = iteration[0].nodes
        if nodes["is_categorical"].any():
            raise ValueError("categorical splits are not supported by the runtime")
        leaf = nodes["is_leaf"].astype(bool)
        trees.append({
            "feature": [int(v) if not is_leaf else -1 for v, is_leaf in zip(nodes["feature_idx"], leaf)],
            "threshold": [float(v) if not is_leaf else 0.0 for v, is_leaf in zip(nodes["num_threshold"], leaf)],
            "left": [int(v) if not is_leaf else -1 for v, is_leaf in zip(nodes["left"], leaf)],
            "right": [int(v) if not is_leaf else -1 for v, is_leaf in zip(nodes["right"], leaf)],
            "missing_left": [bool(v) for v in nodes["missing_go_to_left"]],
            "value": [float(v) if is_leaf else 0.0 for v, is_leaf in zip(nodes["value"], leaf)],
        })
    return float(baseline[0]), trees


def export_spec(run_dir: Path, *, model: str | None = None) -> dict[str, Any]:
    """Build the deployable specification of a run's selected feature model.

    The trees, threshold and decoder are taken unchanged from the run: the
    final model fitted on all development data and the threshold chosen on
    development out-of-fold predictions.
    """

    import joblib
    import sklearn

    run_dir = Path(run_dir)
    metrics = json.loads((run_dir / "metrics.json").read_text())
    config = json.loads((run_dir / "config.json").read_text())
    name = model or metrics.get("selected_model")
    if name != "hgb":
        raise ValueError(f"only the gradient-boosting model is exportable, not {name!r}")
    if abs(float(config["window_s"]) - WINDOW_S) > 1e-9:
        raise ValueError("the runtime contract requires 3-second windows")
    model_path = run_dir / "models" / f"{name}.joblib"
    model_bytes = model_path.read_bytes()
    wrapper = joblib.load(model_path)
    if list(wrapper.columns) != list(range(len(feature_names()))):
        raise ValueError("model does not use the full interpretable feature vector")
    baseline, trees = _trees_from_hgb(wrapper.estimator)
    result = metrics["results"][name]
    threshold = float(result["threshold"])
    decoder = DecoderConfig.from_threshold(threshold)

    def brief(split: str) -> dict[str, Any] | None:
        block = result.get(split)
        if not block:
            return None
        bouts = block.get("bouts") or {}
        return {
            **{k: block.get(k) for k in ("f1", "precision", "recall", "false_positive_rate", "auc_pr", "sessions", "windows")},
            "ci95_f1": (block.get("ci95") or {}).get("f1"),
            "bouts_detected": bouts.get("detected_bouts"),
            "bouts_reference": bouts.get("reference_bouts"),
            "false_bouts": bouts.get("false_bouts"),
        }

    spec: dict[str, Any] = {
        "format": SPEC_FORMAT,
        "format_version": SPEC_FORMAT_VERSION,
        "status": "experimental_candidate",
        "status_note": "Exported for on-device parity and shadow testing; not promoted. "
                       "Physical AirPods evidence is required before it drives Live by default.",
        "target": metrics["config"].get("label_target", "human_coarse_chewing_bout"),
        "provenance": {
            "run_id": metrics["run_id"],
            "model": name,
            "git_commit": (metrics.get("git") or {}).get("commit"),
            "dataset_fingerprint": metrics["dataset_fingerprint"],
            "model_artifact_sha256": _sha256_bytes(model_bytes),
            "sklearn_version": sklearn.__version__,
            "selection_rule": metrics.get("selection_rule"),
            "run_selected_model": metrics.get("selected_model"),
            "threshold_rule": "F1-optimal on development out-of-fold predictions",
            "participants": (metrics.get("counts") or {}).get("participants"),
        },
        "input": {
            "channels": ["ax", "ay", "az", "gx", "gy", "gz"],
            "units": {
                "ax,ay,az": "g, CMDeviceMotion.userAcceleration",
                "gx,gy,gz": "rad/s, CMDeviceMotion.rotationRate",
            },
            "value_precision": "float32",
            "timestamps": "seconds, strictly increasing; repeated or backward samples are dropped",
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "window_s": WINDOW_S,
            "window_samples": int(round(SAMPLE_RATE_HZ * WINDOW_S)),
            "max_gap_s": MAX_GAP_S,
            "exact_match_tolerance_s": 1e-9,
            "interpolation": "numpy.interp: slope=(y1-y0)/(t1-t0), y=slope*(t-t0)+y0 in float64, stored as float32",
            "min_valid_fraction": float(config["min_valid_fraction"]),
            "valid_fraction_rule": "float32(valid_samples)/float32(window_samples) compared in float64 with '<'",
        },
        "features": feature_constants(),
        "model": {
            "kind": "hist_gradient_boosting_binary",
            "feature_count": len(feature_names()),
            "baseline_logit": baseline,
            "split_rule": "go left when float64(feature) <= threshold; NaN goes left when missing_left",
            "summation": "logit = baseline + tree values summed in tree order",
            "link": "sigmoid",
            "trees": trees,
        },
        "decision": {
            "threshold": threshold,
            "stride_s": float(config["stride_s"]),
            "invalid_window_probability": 0.0,
            "decoder": {
                "alpha": decoder.alpha,
                "high_threshold": decoder.high_threshold,
                "low_threshold": decoder.low_threshold,
                "min_start_windows": decoder.min_start_windows,
                "min_end_windows": decoder.min_end_windows,
            },
        },
        "evaluation": {
            "development_out_of_fold": brief("dev_oof"),
            "locked_test": brief("test"),
            "note": "Pilot estimates from one wearer; the test interval is wide.",
        },
    }
    spec["model_id"] = f"eating-{name}-{metrics['run_id'].removeprefix('eating-')}-{decision_hash(spec)[:8]}"
    return spec


def decision_hash(spec: Mapping[str, Any]) -> str:
    """SHA-256 of everything that affects a decision (not provenance or notes)."""

    body = {key: spec[key] for key in ("input", "features", "model", "decision")}
    return _sha256_bytes(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())


def write_spec(spec: Mapping[str, Any], path: Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(spec, indent=1, sort_keys=True) + "\n").encode()
    path.write_bytes(data)
    return _sha256_bytes(data)


def load_spec(path: Path) -> dict[str, Any]:
    spec = json.loads(Path(path).read_text())
    if spec.get("format") != SPEC_FORMAT or spec.get("format_version") != SPEC_FORMAT_VERSION:
        raise ValueError(f"{path} is not a {SPEC_FORMAT} v{SPEC_FORMAT_VERSION} specification")
    if spec["features"]["names"] != feature_names():
        raise ValueError("specification feature layout differs from this pipeline")
    return spec


# ---------------------------------------------------------------- inference


@dataclass
class TreeEnsemble:
    baseline: float
    trees: list[dict[str, np.ndarray]]

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any]) -> "TreeEnsemble":
        model = spec["model"]
        trees = [
            {
                "feature": np.asarray(t["feature"], dtype=np.int64),
                "threshold": np.asarray(t["threshold"], dtype=np.float64),
                "left": np.asarray(t["left"], dtype=np.int64),
                "right": np.asarray(t["right"], dtype=np.int64),
                "missing_left": np.asarray(t["missing_left"], dtype=bool),
                "value": np.asarray(t["value"], dtype=np.float64),
            }
            for t in model["trees"]
        ]
        return cls(baseline=float(model["baseline_logit"]), trees=trees)

    def logit(self, features: np.ndarray) -> np.ndarray:
        x = np.asarray(features, dtype=np.float32).astype(np.float64)
        rows = np.arange(len(x))
        total = np.full(len(x), self.baseline, dtype=np.float64)
        for tree in self.trees:
            node = np.zeros(len(x), dtype=np.int64)
            while True:
                feature = tree["feature"][node]
                active = feature >= 0
                if not active.any():
                    break
                value = x[rows[active], feature[active]]
                go_left = np.where(
                    np.isnan(value), tree["missing_left"][node[active]], value <= tree["threshold"][node[active]]
                )
                node[active] = np.where(go_left, tree["left"][node[active]], tree["right"][node[active]])
            total += tree["value"][node]
        return total

    def probability(self, features: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.logit(features)))


def valid_fraction(window: np.ndarray) -> float:
    """Validity exactly as the experiment computed it (float32 mean)."""

    return float(np.asarray(window[6], dtype=np.float32).mean())


def decoder_config(spec: Mapping[str, Any]) -> DecoderConfig:
    d = spec["decision"]["decoder"]
    return DecoderConfig(
        high_threshold=float(d["high_threshold"]),
        low_threshold=float(d["low_threshold"]),
        alpha=float(d["alpha"]),
        min_start_windows=int(d["min_start_windows"]),
        min_end_windows=int(d["min_end_windows"]),
    )


def run_stream(
    spec: Mapping[str, Any],
    timestamps: np.ndarray,
    imu: np.ndarray,
    end_times: Sequence[float],
    *,
    ensemble: TreeEnsemble | None = None,
) -> pd.DataFrame:
    """Reference outputs for windows ending at ``end_times``.

    ``timestamps``/``imu`` are the recorded stream after dropping
    non-advancing rows (float64 seconds, float32 values).  Each window uses
    only samples up to the first sample at or after its end, exactly what a
    streaming implementation has seen when it evaluates that window.
    """

    ensemble = ensemble or TreeEnsemble.from_spec(spec)
    t = np.asarray(timestamps, dtype=np.float64)
    values = np.asarray(imu, dtype=np.float32)
    rate = int(spec["input"]["sample_rate_hz"])
    window_s = float(spec["input"]["window_s"])
    max_gap = float(spec["input"]["max_gap_s"])
    min_valid = float(spec["input"]["min_valid_fraction"])
    ends = np.asarray(end_times, dtype=np.float64)
    windows = np.zeros((len(ends), 7, int(round(rate * window_s))), dtype=np.float32)
    valid = np.zeros(len(ends), dtype=np.float64)
    for index, end in enumerate(ends):
        stop = int(np.searchsorted(t, end, side="left")) + 1
        if stop < 2:
            continue  # fewer than two samples seen: an all-invalid window
        window, _ = resample_motion_window(
            t[:stop], values[:stop], end_timestamp=float(end),
            sample_rate_hz=rate, context_seconds=window_s, max_gap_seconds=max_gap,
        )
        windows[index] = window
        valid[index] = valid_fraction(window)
    features = compute_features(windows, sample_rate_hz=rate) if len(ends) else np.zeros((0, 80), np.float32)
    usable = valid >= min_valid
    probability = np.full(len(ends), np.nan)
    if usable.any():
        probability[usable] = ensemble.probability(features[usable])
    used = np.where(usable, probability, float(spec["decision"]["invalid_window_probability"]))
    states, bouts = decode_bouts(ends, used, decoder_config(spec)) if len(ends) else (np.zeros(0, bool), [])
    smoothed = np.zeros(len(ends))
    alpha = float(spec["decision"]["decoder"]["alpha"])
    level = 0.0
    for index, p in enumerate(used):
        level = alpha * p + (1 - alpha) * level
        smoothed[index] = level
    frame = pd.DataFrame({
        "t_end": ends,
        "valid_fraction": valid,
        "probability": probability,
        "decoder_input": used,
        "smoothed": smoothed,
        "chewing": states,
    })
    frame.attrs["bouts"] = bouts
    frame.attrs["features"] = features
    return frame


def stream_end_times(timestamps: np.ndarray, stride_s: float, window_s: float = WINDOW_S,
                     first_end: float | None = None) -> np.ndarray:
    """End times a streaming detector evaluates: every stride after one full window."""

    t = np.asarray(timestamps, dtype=np.float64)
    if len(t) < 2:
        return np.zeros(0)
    start = float(t[0] + window_s) if first_end is None else float(first_end)
    count = int(np.floor((t[-1] - start) / stride_s + 1e-9)) + 1
    return start + stride_s * np.arange(max(count, 0))
