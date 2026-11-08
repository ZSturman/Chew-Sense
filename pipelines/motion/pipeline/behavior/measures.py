"""Meal-level chewing measurements from AirPods motion alone.

The registry states, for every measurement, whether it is observed directly,
inferred, or not yet measurable, what would validate it and what currently
does.  :func:`session_behavior` computes the measurable ones for a recording
from the exported eating detector and the raw motion; nothing here uses video
or labels, so the same code can run on device later.

Timing convention: the detector's windows are causal (they end at ``t``).
Offline measurements place each decision at the window centre
(``t - window/2``); live latency is reported separately by the experiment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from ..eating.runtime import run_stream, stream_end_times

CADENCE_WINDOW_S = 6.0
CADENCE_STRIDE_S = 1.0
CADENCE_BAND_HZ = (0.5, 3.0)


@dataclass(frozen=True)
class MeasurementDefinition:
    name: str
    unit: str
    observability: str  # observed | inferred | not_measurable
    method: str
    reference: str
    status: str
    needed: str


REGISTRY: tuple[MeasurementDefinition, ...] = (
    MeasurementDefinition(
        "chewing_bout", "start/end s", "inferred",
        "exported motion detector, EMA + hysteresis decoder", "human coarse chewing spans",
        "pilot-validated at bout level (one wearer)", "more wearers; natural meals",
    ),
    MeasurementDefinition(
        "chewing_duration", "s", "inferred",
        "sum of decoded bout lengths", "human coarse chewing spans",
        "pilot-validated (development out-of-fold timing error)", "natural meals with long bouts",
    ),
    MeasurementDefinition(
        "pause_between_bouts", "s", "inferred",
        "gaps between decoded bouts", "gaps between human chewing spans",
        "pilot: few human-marked pauses exist", "natural meals; guided stop/start pauses",
    ),
    MeasurementDefinition(
        "chewing_cadence", "Hz", "inferred",
        f"dominant gyro frequency ({CADENCE_BAND_HZ[0]}-{CADENCE_BAND_HZ[1]} Hz) in "
        f"{CADENCE_WINDOW_S:g} s windows inside bouts; peak share as quality",
        "video jaw-opening frequency; hand-marked chews (frozen blind review)",
        "agrees with video and with 45 hand-marked chews (within 2.5% in 2 bouts, one recording, "
        "one self-selected pace); tracking of pace changes untested",
        "the chewing-pace guided experiment (slow/normal/fast)",
    ),
    MeasurementDefinition(
        "cadence_variability", "coefficient of variation", "inferred",
        "spread of window cadences within a meal", "none yet", "unvalidated (descriptive only)",
        "repeated meals; pace-change recordings",
    ),
    MeasurementDefinition(
        "chew_count_estimate", "chews", "inferred",
        "cadence x bout duration", "hand-marked chews from frozen blind reviews (chew-count-eval)",
        "pilot: 50 estimated vs 45 hand-marked chews (+11%) inside human spans on one recording; "
        "span padding past the last chew inflates counts; 45 of 300 verified chews needed",
        "independent annotation of individual chews",
    ),
    MeasurementDefinition(
        "individual_chew_events", "timestamps", "not_measurable",
        "the TCN event head is untrained", "verified chew cycles", "not measurable yet",
        ">= 300 independently annotated chews",
    ),
    MeasurementDefinition(
        "bite_events", "timestamps", "not_measurable",
        "no food-entry model; video hand-to-mouth proposals are unverified", "verified food entries",
        "not measurable yet", "food-entry annotation; a longer-context motion model",
    ),
    MeasurementDefinition(
        "time_between_bites", "s", "not_measurable", "depends on bite events", "verified food entries",
        "not measurable yet", "bite events first",
    ),
    MeasurementDefinition(
        "eating_vs_talking", "class", "inferred",
        "the eating detector's false-positive rate on talking", "guided talking with human labels",
        "10 labelled windows (20% flagged) - too few", "talking and talking-while-eating recordings",
    ),
    MeasurementDefinition(
        "eating_vs_drinking", "class", "not_measurable", "no drinking data", "guided drinking",
        "not measurable yet", "drinking recordings",
    ),
    MeasurementDefinition(
        "eating_vs_gum", "class", "not_measurable", "no gum data", "guided gum chewing",
        "not measurable yet (gum is chewing without eating; motion alone may not separate it)",
        "gum-chewing recordings",
    ),
    MeasurementDefinition(
        "head_movement_rejection", "class", "inferred",
        "the eating detector's false-positive rate on nodding / shaking", "guided head movement",
        "nodding 17% of 72 windows flagged; shaking 0%", "more head-movement recordings",
    ),
)


def registry() -> list[dict[str, str]]:
    return [definition.__dict__.copy() for definition in REGISTRY]


def dominant_frequency(values: np.ndarray, rate: float, band: tuple[float, float] = CADENCE_BAND_HZ) -> tuple[float, float]:
    """Dominant in-band frequency and its share of in-band power (zero-padded to 512)."""

    x = np.asarray(values, dtype=float)
    x = x - x.mean()
    n = max(len(x), 512)
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)), n=n)) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / rate)
    mask = (freqs >= band[0]) & (freqs <= band[1])
    if not mask.any() or spectrum[mask].sum() <= 0:
        return float("nan"), 0.0
    index = int(np.argmax(spectrum[mask]))
    return float(freqs[mask][index]), float(spectrum[mask][index] / spectrum[mask].sum())


def motion_cadence(window: np.ndarray, rate: float = 50.0) -> tuple[float, float]:
    """Cadence of one resampled (7, n) window: the gyro axis with the most prominent peak."""

    best = (float("nan"), 0.0)
    for axis in (3, 4, 5):
        candidate = dominant_frequency(window[axis], rate)
        if candidate[1] > best[1]:
            best = candidate
    return best


def bouts_from_states(t_end: np.ndarray, states: np.ndarray, window_s: float) -> list[tuple[float, float]]:
    """Decoded bouts on the offline (window-centre) timeline."""

    bouts, start = [], None
    for t, state in zip(t_end, states):
        if state and start is None:
            start = float(t)
        elif not state and start is not None:
            bouts.append((start - window_s / 2, float(t) - window_s / 2))
            start = None
    if start is not None and len(t_end):
        bouts.append((start - window_s / 2, float(t_end[-1]) - window_s / 2))
    return bouts


def cadence_track(t: np.ndarray, imu: np.ndarray, spans: Sequence[tuple[float, float]]) -> list[dict[str, float]]:
    """Cadence every second inside ``spans`` using causal 6 s windows fully within a span."""

    from ..temporal import resample_motion_window

    rows = []
    for start, end in spans:
        for window_end in np.arange(start + CADENCE_WINDOW_S, end + 1e-9, CADENCE_STRIDE_S):
            window, _ = resample_motion_window(
                t, imu, end_timestamp=float(window_end), context_seconds=CADENCE_WINDOW_S
            )
            if window[6].mean() < 0.9:
                continue
            hz, share = motion_cadence(window)
            rows.append({"t_end": float(window_end), "hz": hz, "peak_share": share})
    return rows


def summarize_bouts(
    bouts: Sequence[tuple[float, float]], cadence: Sequence[Mapping[str, float]], duration_s: float
) -> dict[str, Any]:
    pauses = [b[0] - a[1] for a, b in zip(bouts, bouts[1:])]
    per_bout = []
    for start, end in bouts:
        inside = [c for c in cadence if start + CADENCE_WINDOW_S <= c["t_end"] <= end + 1e-9]
        hz = np.array([c["hz"] for c in inside], dtype=float)
        share = np.array([c["peak_share"] for c in inside], dtype=float)
        median_hz = float(np.nanmedian(hz)) if len(hz) else None
        per_bout.append({
            "start_s": round(start, 2),
            "end_s": round(end, 2),
            "duration_s": round(end - start, 2),
            "cadence_hz": None if median_hz is None else round(median_hz, 3),
            "cadence_windows": int(len(hz)),
            "cadence_peak_share": None if not len(share) else round(float(np.median(share)), 3),
            "chew_count_estimate": None if median_hz is None else int(round(median_hz * (end - start))),
        })
    cadences = np.array([c["hz"] for c in cadence], dtype=float)
    cadences = cadences[np.isfinite(cadences)]
    chewing = sum(end - start for start, end in bouts)
    bout_cadences = [b["cadence_hz"] for b in per_bout if b["cadence_hz"] is not None]
    trend = None
    if len(bout_cadences) >= 3:
        mids = [(b["start_s"] + b["end_s"]) / 2 / 60 for b in per_bout if b["cadence_hz"] is not None]
        trend = round(float(np.polyfit(mids, bout_cadences, 1)[0]), 4)
    return {
        "bouts": per_bout,
        "chewing_bouts": len(bouts),
        "chewing_time_s": round(chewing, 1),
        "chewing_fraction": round(chewing / duration_s, 3) if duration_s else None,
        "pauses_s": [round(p, 2) for p in pauses],
        "median_pause_s": round(float(np.median(pauses)), 2) if pauses else None,
        "cadence_hz_median": round(float(np.median(cadences)), 3) if len(cadences) else None,
        "cadence_cv": round(float(np.std(cadences) / np.mean(cadences)), 3) if len(cadences) >= 3 else None,
        "cadence_trend_hz_per_min": trend,
        "chew_count_estimate": int(sum(b["chew_count_estimate"] or 0 for b in per_bout)),
    }


def session_behavior(spec: Mapping[str, Any], t: np.ndarray, imu: np.ndarray, duration_s: float) -> dict[str, Any]:
    """Motion-only measurements for one recording, each tagged with its status."""

    stride = float(spec["decision"]["stride_s"])
    window_s = float(spec["input"]["window_s"])
    ends = stream_end_times(t, stride, window_s=window_s, first_end=window_s)
    stream = run_stream(spec, t, imu, ends)
    bouts = bouts_from_states(stream["t_end"].to_numpy(), stream["chewing"].to_numpy(), window_s)
    cadence = cadence_track(t, imu, bouts)
    return {
        "model_id": spec.get("model_id"),
        "duration_s": round(float(duration_s), 1),
        "measurements": summarize_bouts(bouts, cadence, duration_s),
        "status": {d.name: d.status for d in REGISTRY},
        "note": "Inferred from AirPods motion only; see the registry for what has been validated.",
    }
