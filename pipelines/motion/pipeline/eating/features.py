"""Interpretable window features computed from AirPods IMU alone.

Inputs are the ``(N, 7, samples)`` windows produced by
:func:`pipeline.temporal.resample_motion_window`: six IMU axes at 50 Hz plus
the gap-validity channel.  The validity channel is never a feature; it only
gates which windows are used.  Nothing here reads video, guided metadata,
labels, or neighbouring windows, so every feature is available on-device at
inference time.

Chewing is a quasi-periodic jaw motion of roughly 0.8-2.5 Hz.  The features
therefore describe per-signal energy, how that energy is distributed over
frequency bands, the dominant frequency and its prominence, spectral entropy,
and autocorrelation periodicity at chewing-like lags.
"""

from __future__ import annotations

import numpy as np


SAMPLE_RATE_HZ = 50
SIGNALS = ("gx", "gy", "gz", "gyro_mag", "ax", "ay", "az", "accel_mag")
BANDS = (
    ("low", 0.3, 0.8),
    ("chew", 0.8, 2.5),
    ("mid", 2.5, 5.0),
    ("high", 5.0, 12.0),
    ("vhigh", 12.0, 25.01),
)
CHEW_LAG_SECONDS = (0.33, 1.25)
ENERGY_FEATURE = "gyro_mag__log_rms"


def feature_names() -> list[str]:
    names: list[str] = []
    for signal in SIGNALS:
        names.append(f"{signal}__log_rms")
        names.extend(f"{signal}__band_{band}" for band, _, _ in BANDS)
        names.extend((
            f"{signal}__dominant_hz",
            f"{signal}__chew_peak_ratio",
            f"{signal}__spectral_entropy",
            f"{signal}__chew_autocorr",
        ))
    return names


def _signals(windows: np.ndarray) -> np.ndarray:
    """Return (N, 8, samples) signals: three gyro axes, |gyro|, three accel, |accel|."""

    accel = windows[:, 0:3, :].astype(np.float64)
    gyro = windows[:, 3:6, :].astype(np.float64)
    return np.concatenate(
        (
            gyro,
            np.linalg.norm(gyro, axis=1, keepdims=True),
            accel,
            np.linalg.norm(accel, axis=1, keepdims=True),
        ),
        axis=1,
    )


def compute_features(windows: np.ndarray, sample_rate_hz: int = SAMPLE_RATE_HZ) -> np.ndarray:
    """Return ``(N, len(feature_names()))`` float32 features."""

    windows = np.asarray(windows)
    if windows.ndim != 3 or windows.shape[1] != 7:
        raise ValueError("expected (N, 7, samples) windows")
    n, _, samples = windows.shape
    signals = _signals(windows)
    centered = signals - signals.mean(axis=2, keepdims=True)
    rms = centered.std(axis=2)

    taper = np.hanning(samples)
    spectrum = np.abs(np.fft.rfft(centered * taper, axis=2)) ** 2
    freqs = np.fft.rfftfreq(samples, d=1.0 / sample_rate_hz)
    analysed = (freqs >= 0.3) & (freqs <= 25.01)
    total = spectrum[:, :, analysed].sum(axis=2) + 1e-12
    band_fractions = [
        spectrum[:, :, (freqs >= lo) & (freqs < hi)].sum(axis=2) / total for _, lo, hi in BANDS
    ]

    dominant_range = (freqs >= 0.5) & (freqs <= 5.0)
    dominant_hz = freqs[dominant_range][np.argmax(spectrum[:, :, dominant_range], axis=2)]
    chew_range = (freqs >= 0.8) & (freqs < 2.5)
    chew_peak = spectrum[:, :, chew_range].max(axis=2)
    median_power = np.median(spectrum[:, :, analysed], axis=2) + 1e-12
    chew_peak_ratio = np.log10(chew_peak / median_power + 1e-12)

    probability = spectrum[:, :, analysed] / total[:, :, None]
    entropy = -(probability * np.log(probability + 1e-12)).sum(axis=2) / np.log(analysed.sum())

    lag_lo = int(round(CHEW_LAG_SECONDS[0] * sample_rate_hz))
    lag_hi = min(int(round(CHEW_LAG_SECONDS[1] * sample_rate_hz)), samples - 2)
    variance = (centered**2).sum(axis=2) + 1e-12
    autocorr = np.full((n, signals.shape[1]), 0.0)
    for lag in range(lag_lo, lag_hi + 1):
        value = (centered[:, :, lag:] * centered[:, :, :-lag]).sum(axis=2) / variance
        autocorr = np.maximum(autocorr, value)

    columns: list[np.ndarray] = []
    for index in range(signals.shape[1]):
        columns.append(np.log(rms[:, index] + 1e-6))
        columns.extend(fraction[:, index] for fraction in band_fractions)
        columns.extend((
            dominant_hz[:, index],
            chew_peak_ratio[:, index],
            entropy[:, index],
            autocorr[:, index],
        ))
    features = np.stack(columns, axis=1).astype(np.float32)
    if features.shape[1] != len(feature_names()):
        raise AssertionError("feature layout mismatch")
    return features
