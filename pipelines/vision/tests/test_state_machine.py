"""State-machine sanity check using synthetic chew streams."""
from __future__ import annotations

import numpy as np

from chewsense.config import load_config
from chewsense.events.state_machine import _BiteIn, _ChewIn, build_episodes


def _ts_array(fps: float, n: int) -> np.ndarray:
    return np.arange(n, dtype=np.float64) / fps


def test_simple_episode_emerges_from_chew_burst():
    cfg = load_config()
    fps = 30.0
    n = int(20 * fps)
    ts = _ts_array(fps, n)
    # 5 chews, peaks at 5,6,7,8,9 seconds, each 0.4s wide
    chews: list[_ChewIn] = []
    for i, peak_t in enumerate([5.0, 5.7, 6.4, 7.1, 7.8]):
        s = int((peak_t - 0.2) * fps); p = int(peak_t * fps); e = int((peak_t + 0.2) * fps)
        chews.append(_ChewIn(
            start_idx=s, peak_idx=p, end_idx=e,
            start_ts=float(ts[s]), peak_ts=float(ts[p]), end_ts=float(ts[e]),
            amplitude=0.04 - i * 0.002, prominence=0.01,
            talking_overlap=0.0, occluded_overlap=0.0,
        ))
    bites = [_BiteIn(frame_idx=int(4.5 * fps), ts=4.5, confidence=0.7, evidence={})]
    episodes, _ = build_episodes(
        chews=chews, bites=bites, timestamps=ts,
        talking_flags=np.zeros(n, dtype=bool),
        occluded_flags=np.zeros(n, dtype=bool),
        cfg=cfg,
    )
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.start_reason == "bite_detected"
    assert ep.bite_count == 1
    assert ep.chew_count == 5


def test_talking_chews_are_filtered():
    cfg = load_config()
    fps = 30.0
    n = int(10 * fps)
    ts = _ts_array(fps, n)
    talking = np.zeros(n, dtype=bool)
    talking[int(3 * fps):int(8 * fps)] = True
    chews: list[_ChewIn] = []
    for peak_t in [4.0, 4.6, 5.2, 5.8, 6.4]:
        s = int((peak_t - 0.2) * fps); p = int(peak_t * fps); e = int((peak_t + 0.2) * fps)
        chews.append(_ChewIn(
            start_idx=s, peak_idx=p, end_idx=e,
            start_ts=float(ts[s]), peak_ts=float(ts[p]), end_ts=float(ts[e]),
            amplitude=0.04, prominence=0.01,
            talking_overlap=1.0, occluded_overlap=0.0,
        ))
    eps, talk_spans = build_episodes(
        chews=chews, bites=[], timestamps=ts,
        talking_flags=talking,
        occluded_flags=np.zeros(n, dtype=bool),
        cfg=cfg,
    )
    assert len(eps) == 0
    assert len(talk_spans) >= 1
