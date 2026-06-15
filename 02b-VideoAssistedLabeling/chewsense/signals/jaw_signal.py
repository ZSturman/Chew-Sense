"""Jaw-open signal processing: smoothing, bandpass, peak detection -> chew cycles.

A chew is one open-close cycle:
  start: last preceding local minimum (within `chew_flank_max_s`) before peak
  peak:  local maximum of the bandpassed signal
  end:   next following local minimum within `chew_flank_max_s` after peak

Amplitude is read from the *smoothed* (non-bandpassed) signal so it lives in
real face-scale units. Peak prominence threshold is adaptive: a multiple of the
rolling MAD inside a 3-second window, so chew amplitude can drift over an
episode without the detector becoming insensitive.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, filtfilt, find_peaks, savgol_filter


@dataclass
class ChewCandidate:
    start_idx: int
    peak_idx: int
    end_idx: int
    amplitude: float
    prominence: float


def _safe_savgol(x: np.ndarray, window: int, polyorder: int) -> np.ndarray:
    if len(x) < window:
        return x.copy()
    if window % 2 == 0:
        window += 1
    polyorder = min(polyorder, window - 1)
    return savgol_filter(x, window, polyorder)


def smooth(jaw_open: np.ndarray, cfg: dict) -> np.ndarray:
    return _safe_savgol(
        jaw_open.astype(np.float32),
        cfg["jaw_signal"]["smooth_window"],
        cfg["jaw_signal"]["smooth_polyorder"],
    )


def bandpass(smoothed: np.ndarray, fps: float, cfg: dict) -> np.ndarray:
    if len(smoothed) < 16 or fps <= 0:
        return np.zeros_like(smoothed)
    nyq = fps / 2.0
    low = cfg["jaw_signal"]["bandpass_low_hz"] / nyq
    high = cfg["jaw_signal"]["bandpass_high_hz"] / nyq
    low = max(1e-3, min(low, 0.99))
    high = max(low + 1e-3, min(high, 0.99))
    b, a = butter(2, [low, high], btype="band")
    pad = max(9, len(b) * 3)
    if len(smoothed) <= pad:
        return np.zeros_like(smoothed)
    return filtfilt(b, a, smoothed).astype(np.float32)


def _rolling_mad(x: np.ndarray, window: int) -> np.ndarray:
    """Approximate rolling MAD using a sliding window via numpy stride tricks
    fallback. Not perfectly efficient but fine at video FPS lengths."""
    n = len(x)
    if n == 0:
        return np.zeros_like(x)
    pad = window // 2
    out = np.empty(n, dtype=np.float32)
    for i in range(n):
        a = max(0, i - pad); b = min(n, i + pad + 1)
        seg = x[a:b]
        med = np.median(seg)
        out[i] = float(np.median(np.abs(seg - med)) + 1e-6)
    return out


def detect_cycles(
    signal_smoothed: np.ndarray,
    signal_bandpassed: np.ndarray,
    fps: float,
    cfg: dict,
    *,
    min_amplitude: float = 0.0,
) -> list[ChewCandidate]:
    """Generic open->close peak/cycle detector.

    Identical algorithm to the original chew detector: adaptive prominence
    threshold on the bandpassed signal, then locate flanking minima on the
    smoothed signal. Lives here so both the chew detector and the new
    mouth-cycle detector (`signals.mouth_state`) share one implementation.
    """
    if len(signal_bandpassed) < 8 or fps <= 0:
        return []
    js = cfg["jaw_signal"]
    min_dist = max(1, int(js["peak_min_distance_s"] * fps))
    flank = max(1, int(js["chew_flank_max_s"] * fps))
    win = max(int(js["rolling_mad_window_s"] * fps), 9)

    mad = _rolling_mad(signal_bandpassed, win)
    prom_threshold = js["peak_prominence_mad_k"] * np.median(mad)

    peaks, props = find_peaks(
        signal_bandpassed,
        distance=min_dist,
        prominence=max(prom_threshold, 1e-4),
    )
    out: list[ChewCandidate] = []
    for p, prom in zip(peaks, props["prominences"]):
        a = max(0, p - flank)
        b = min(len(signal_smoothed), p + flank + 1)
        if b - a < 3:
            continue
        pre = signal_smoothed[a:p + 1]
        post = signal_smoothed[p:b]
        if len(pre) < 2 or len(post) < 2:
            continue
        start = a + int(np.argmin(pre))
        end = p + int(np.argmin(post))
        if start >= p or end <= p:
            continue
        baseline = (signal_smoothed[start] + signal_smoothed[end]) / 2.0
        amp = float(signal_smoothed[p] - baseline)
        if amp <= min_amplitude:
            continue
        out.append(
            ChewCandidate(
                start_idx=int(start),
                peak_idx=int(p),
                end_idx=int(end),
                amplitude=amp,
                prominence=float(prom),
            )
        )
    return out


def detect_chews(
    smoothed: np.ndarray,
    bandpassed: np.ndarray,
    fps: float,
    cfg: dict,
) -> list[ChewCandidate]:
    """Back-compat wrapper around :func:`detect_cycles`."""
    return detect_cycles(smoothed, bandpassed, fps, cfg)


def build_jaw_proxy(chin_to_nose: np.ndarray, rest_value: float) -> np.ndarray:
    """Signed jaw-translation residual.

    A larger ``chin_to_nose`` than the resting value means the jaw has dropped
    (mouth opening) even when the lips stay sealed. Returned as a residual so
    the existing :func:`bandpass`/:func:`detect_cycles` pipeline can be reused
    unchanged.
    """
    x = np.asarray(chin_to_nose, dtype=np.float32)
    return (x - float(rest_value)).astype(np.float32)
