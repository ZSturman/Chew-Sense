"""Quality metric sanity checks on synthetic episodes."""
from __future__ import annotations

import numpy as np

from chewsense.config import load_config
from chewsense.events.schema import Bite, Chew, Episode, VideoEvents
from chewsense.quality.metrics import compute_quality_report


def _ep(rate_hz: float, n_chews: int, start_ts: float = 1.0) -> Episode:
    chews = []
    period = 1.0 / rate_hz
    for i in range(n_chews):
        t = start_ts + i * period
        chews.append(Chew(
            id=f"ch_{i}",
            start_ts=t - 0.15, peak_ts=t, end_ts=t + 0.15,
            duration_s=0.3, amplitude=0.05 - 0.001 * i,
            relative_amplitude=1.0, confidence=0.8, evidence={},
        ))
    return Episode(
        id="ep_0",
        start_ts=chews[0].start_ts, end_ts=chews[-1].end_ts,
        start_reason="bite_detected", end_reason="chewing_stopped",
        confidence=0.8,
        bites=[Bite(id="bt_0", ts=start_ts - 0.5, confidence=0.8, evidence={})],
        chews=chews,
    )


def test_too_fast_flag_fires_on_fast_chew_rate():
    cfg = load_config()
    ep = _ep(rate_hz=3.0, n_chews=20)  # > chew_rate_hz_max (2.5)
    ev = VideoEvents(video_id="t", duration_s=20.0, fps=30, width=1, height=1, episodes=[ep])
    ts = np.linspace(0, 20.0, 600, dtype=np.float64)
    js = np.zeros_like(ts, dtype=np.float32)
    rep = compute_quality_report(ev, timestamps=ts, jaw_open_smooth=js,
                                 face_detected_fraction=1.0, cfg=cfg, baseline=None, user_id=None)
    assert "too_fast" in rep.episodes[0].flags
    assert rep.episodes[0].chews_per_bite == 20.0


def test_too_few_chews_per_bite():
    cfg = load_config()
    ep = _ep(rate_hz=1.5, n_chews=5)  # < chews_per_bite_min (15)
    ev = VideoEvents(video_id="t", duration_s=10.0, fps=30, width=1, height=1, episodes=[ep])
    ts = np.linspace(0, 10.0, 300, dtype=np.float64)
    js = np.zeros_like(ts, dtype=np.float32)
    rep = compute_quality_report(ev, timestamps=ts, jaw_open_smooth=js,
                                 face_detected_fraction=1.0, cfg=cfg, baseline=None, user_id=None)
    assert "too_few_chews_per_bite" in rep.episodes[0].flags


def test_low_signal_quality_flag():
    cfg = load_config()
    ev = VideoEvents(video_id="t", duration_s=10.0, fps=30, width=1, height=1, episodes=[])
    ts = np.linspace(0, 10.0, 300, dtype=np.float64)
    rep = compute_quality_report(
        ev, timestamps=ts, jaw_open_smooth=np.zeros_like(ts, dtype=np.float32),
        face_detected_fraction=0.2, cfg=cfg, baseline=None, user_id=None,
    )
    assert "low_signal_quality" in rep.session.flags
