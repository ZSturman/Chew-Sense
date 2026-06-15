"""Synthetic-signal sanity checks for the chew detector."""
from __future__ import annotations

import numpy as np

from chewsense.config import load_config
from chewsense.signals.jaw_signal import bandpass, detect_chews, smooth


def _make_chew_signal(fps: float, n_cycles: int, freq_hz: float = 1.5,
                     amp: float = 0.05, baseline: float = 0.05,
                     noise: float = 0.002):
    rng = np.random.default_rng(0)
    duration = n_cycles / freq_hz
    t = np.arange(0, duration, 1.0 / fps)
    sig = baseline + amp * (1 - np.cos(2 * np.pi * freq_hz * t)) / 2
    sig += rng.normal(0, noise, size=t.shape)
    return sig.astype(np.float32), t


def test_detect_chews_matches_cycle_count():
    cfg = load_config()
    fps = 30.0
    n_cycles = 8
    sig, _ = _make_chew_signal(fps, n_cycles)
    s = smooth(sig, cfg)
    bp = bandpass(s, fps, cfg)
    cands = detect_chews(s, bp, fps, cfg)
    # Bandpass edges trim a couple of cycles; allow ±3
    assert n_cycles - 3 <= len(cands) <= n_cycles + 1, len(cands)
    # Amplitudes should all be positive
    assert all(c.amplitude > 0 for c in cands)
