"""Shared smoothing helpers for jaw-open debug telemetry.

Chew proposals are produced only by :mod:`chewsense.signals.mouth_state`.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, filtfilt, savgol_filter


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
    nyquist = fps / 2.0
    low = cfg["jaw_signal"]["bandpass_low_hz"] / nyquist
    high = cfg["jaw_signal"]["bandpass_high_hz"] / nyquist
    low = max(1e-3, min(low, 0.99))
    high = max(low + 1e-3, min(high, 0.99))
    b, a = butter(2, [low, high], btype="band")
    pad = max(9, len(b) * 3)
    if len(smoothed) <= pad:
        return np.zeros_like(smoothed)
    return filtfilt(b, a, smoothed).astype(np.float32)
