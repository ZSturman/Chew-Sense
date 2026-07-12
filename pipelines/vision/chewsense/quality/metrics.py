"""Quality metrics derived from the deterministic pipeline output.

These metrics are pure functions over `VideoEvents` + per-frame signals; they
add no ML. Per-user baselines (loaded from `chewsense/quality/baselines.py`)
tighten flag thresholds once enough history exists.

Headline numbers:
- chews_per_bite          (the "are you actually chewing your food?" number)
- chew_rate_hz            (cycles per second within an episode)
- rhythm_cv               (coefficient of variation of inter-peak interval)
- amplitude_decay_slope   (negative = food being broken down)
- bite_size_norm          (peak jaw_open during food-entry / user max baseline)
- inter_episode_gap_s     (rest between sessions)
"""
from __future__ import annotations

import numpy as np

from ..events.schema import (
    Episode,
    EpisodeQuality,
    QualityFlag,
    QualityReport,
    SessionQuality,
    VideoEvents,
)
from .baselines import Baseline


def compute_episode_quality(
    ep: Episode,
    *,
    timestamps: np.ndarray,
    jaw_open_smooth: np.ndarray,
    cfg: dict,
    baseline: Baseline | None = None,
) -> EpisodeQuality:
    q = cfg["quality"]

    peaks = np.array([c.peak_ts for c in ep.chews], dtype=np.float64)
    durs = np.array([c.duration_s for c in ep.chews], dtype=np.float64)
    amps = np.array([c.amplitude for c in ep.chews], dtype=np.float64)

    if len(peaks) >= 2:
        ipi = np.diff(peaks)
        chew_rate_hz = float(1.0 / np.median(ipi)) if np.median(ipi) > 0 else 0.0
        cv = float(np.std(ipi) / np.mean(ipi)) if np.mean(ipi) > 0 else 0.0
    else:
        chew_rate_hz = 0.0
        cv = 0.0

    mean_dur = float(np.mean(durs)) if len(durs) else 0.0

    decay_slope = 0.0
    if len(amps) >= 4:
        x = np.arange(len(amps), dtype=np.float64)
        # least-squares slope, normalized by mean amplitude
        slope = np.polyfit(x, amps, 1)[0]
        decay_slope = float(slope / max(np.mean(amps), 1e-6))

    # chews_per_bite — only defined when at least one bite is attached
    cpb: float | None = None
    if ep.bites:
        cpb = len(ep.chews) / max(1, len(ep.bites))

    # bite_size_norm: peak jaw_open in [bite.ts, bite.ts + 1.0s]
    bite_size: float | None = None
    if ep.bites and len(timestamps) > 0:
        b = ep.bites[0]
        a_idx = int(np.searchsorted(timestamps, b.ts))
        b_idx = int(np.searchsorted(timestamps, b.ts + 1.0))
        a_idx = max(0, a_idx); b_idx = min(len(jaw_open_smooth), b_idx)
        if b_idx > a_idx:
            bite_size = float(np.max(jaw_open_smooth[a_idx:b_idx]))

    flags: list[QualityFlag] = []
    notes: list[str] = []

    # Baseline-tightened thresholds when available
    rate_min, rate_max = q["chew_rate_hz_min"], q["chew_rate_hz_max"]
    cpb_min, cpb_max = q["chews_per_bite_min"], q["chews_per_bite_max"]
    if baseline is not None and baseline.is_ready(q["baseline_min_sessions"]):
        k = q["baseline_k_sigma"]
        if baseline.chew_rate_hz_std > 0:
            rate_min = max(rate_min, baseline.chew_rate_hz_mean - k * baseline.chew_rate_hz_std)
            rate_max = min(rate_max, baseline.chew_rate_hz_mean + k * baseline.chew_rate_hz_std)
        notes.append(f"using baseline (n={baseline.n_sessions})")

    if chew_rate_hz > 0:
        if chew_rate_hz > rate_max:
            flags.append("too_fast")
        elif chew_rate_hz < rate_min:
            flags.append("too_slow")
    if cv > q["rhythm_cv_max"]:
        flags.append("rhythm_irregular")
    if cpb is not None:
        if cpb < cpb_min:
            flags.append("too_few_chews_per_bite")
        elif cpb > cpb_max:
            flags.append("too_many_chews_per_bite")
    if bite_size is not None and baseline is not None and baseline.bite_size_max > 0:
        if bite_size >= q["bite_size_oversized_ratio"] * baseline.bite_size_max:
            flags.append("bite_appears_oversized")

    return EpisodeQuality(
        episode_id=ep.id,
        chew_count=len(ep.chews),
        duration_s=ep.duration_s,
        chew_rate_hz=chew_rate_hz,
        chew_rate_cv=cv,
        mean_chew_duration_s=mean_dur,
        amplitude_decay_slope=decay_slope,
        chews_per_bite=cpb,
        bite_size_norm=bite_size,
        flags=flags,
        notes=notes,
    )


def compute_quality_report(
    events: VideoEvents,
    *,
    timestamps: np.ndarray,
    jaw_open_smooth: np.ndarray,
    face_detected_fraction: float,
    cfg: dict,
    baseline: Baseline | None,
    user_id: str | None,
) -> QualityReport:
    q = cfg["quality"]
    ep_quals = [
        compute_episode_quality(
            ep,
            timestamps=timestamps,
            jaw_open_smooth=jaw_open_smooth,
            cfg=cfg,
            baseline=baseline,
        )
        for ep in events.episodes
    ]

    # Inter-episode gaps
    gaps: list[float] = []
    for prev, nxt in zip(events.episodes, events.episodes[1:]):
        gaps.append(max(0.0, nxt.start_ts - prev.end_ts))

    n_chews = sum(e.chew_count for e in events.episodes)
    n_bites = sum(e.bite_count for e in events.episodes)
    total_eat = sum(e.duration_s for e in events.episodes)

    cpb_values = [eq.chews_per_bite for eq in ep_quals if eq.chews_per_bite is not None]
    rate_values = [eq.chew_rate_hz for eq in ep_quals if eq.chew_rate_hz > 0]
    cv_values = [eq.chew_rate_cv for eq in ep_quals if eq.chew_rate_cv > 0]

    sess_flags: list[QualityFlag] = []
    if face_detected_fraction < q["min_face_detection_fraction"]:
        sess_flags.append("low_signal_quality")
    if any(g < q["inter_session_gap_s_min"] for g in gaps):
        sess_flags.append("inter_session_gap_too_short")

    session = SessionQuality(
        n_episodes=len(events.episodes),
        n_bites=n_bites,
        n_chews=n_chews,
        total_eating_duration_s=total_eat,
        mean_chews_per_bite=float(np.mean(cpb_values)) if cpb_values else None,
        mean_chew_rate_hz=float(np.mean(rate_values)) if rate_values else None,
        rhythm_cv=float(np.mean(cv_values)) if cv_values else None,
        inter_episode_gaps_s=gaps,
        flags=sess_flags,
        user_baseline_used=baseline is not None and baseline.is_ready(q["baseline_min_sessions"]),
        user_id=user_id,
    )
    return QualityReport(session=session, episodes=ep_quals)
