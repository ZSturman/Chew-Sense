"""Episode assembly.

Builds Episode objects from chew candidates and bite candidates plus per-frame
flags (talking, occluded). Operates as a deterministic state machine; explicit
evidence dict on every emitted object.

States:
  IDLE -> APPROACHING -> CHEWING -> SLOWING -> ENDING -> IDLE

In v1 we keep it simple: episodes are derived by clustering chews in time,
optionally anchored by a recent bite, and ended when chewing stops for
`episode_end_hold_s` or interrupted by talking/occlusion/drinking spans.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .schema import (
    Bite,
    Chew,
    Episode,
    ProbableCompletion,
    Span,
)


@dataclass
class _ChewIn:
    start_idx: int
    peak_idx: int
    end_idx: int
    start_ts: float
    peak_ts: float
    end_ts: float
    amplitude: float
    prominence: float
    talking_overlap: float  # fraction of cycle where speaking
    occluded_overlap: float


@dataclass
class _BiteIn:
    frame_idx: int
    ts: float
    confidence: float
    evidence: dict


def build_episodes(
    *,
    chews: list[_ChewIn],
    bites: list[_BiteIn],
    timestamps: np.ndarray,
    talking_flags: np.ndarray,
    occluded_flags: np.ndarray,
    cfg: dict,
) -> tuple[list[Episode], list[Span]]:
    """Returns (episodes, non_eating_mouth_spans).

    Chews whose talking_overlap > 0.5 are reclassified as non-eating mouth
    motion and emitted as a span instead.
    """
    ep_cfg = cfg["episode"]
    merge_gap = ep_cfg["merge_gap_s"]
    end_hold = ep_cfg["episode_end_hold_s"]
    min_chews = ep_cfg["min_chews_per_episode"]
    min_dur = ep_cfg["min_chewing_duration_s"]
    # Bite attachment window: how far before episode start (or after end) a
    # bite candidate can still be linked to the episode. Wider than merge_gap
    # because the chew detector often misses the first few chews after a bite
    # (food entering the mouth obscures landmark motion).
    bite_attach_window = float(cfg.get("bite", {}).get("episode_attach_window_s", merge_gap))

    # Filter chews that occur primarily during talking or full occlusion
    real_chews: list[_ChewIn] = []
    talking_spans: list[Span] = []
    for c in chews:
        if c.talking_overlap > 0.5:
            talking_spans.append(
                Span(start_ts=c.start_ts, end_ts=c.end_ts,
                     kind="talking_mouth_motion", confidence=c.talking_overlap)
            )
            continue
        if c.occluded_overlap > 0.7:
            # ignore — too uncertain
            continue
        real_chews.append(c)

    if not real_chews:
        return [], talking_spans

    # Group chews into clusters separated by > merge_gap
    clusters: list[list[_ChewIn]] = []
    cur = [real_chews[0]]
    for c in real_chews[1:]:
        if c.start_ts - cur[-1].end_ts > merge_gap:
            clusters.append(cur)
            cur = [c]
        else:
            cur.append(c)
    clusters.append(cur)

    episodes: list[Episode] = []
    ep_id = 0
    bite_id = 0
    chew_id = 0

    # Track bites already attached to an earlier episode so a single bite
    # never appears in two episodes (can happen when the bite falls in the
    # gap between two clusters that are both within `bite_attach_window`).
    used_bite_ids: set[int] = set()

    for cluster in clusters:
        if len(cluster) < min_chews:
            continue
        ep_start_ts = cluster[0].start_ts
        ep_end_ts = cluster[-1].end_ts
        if (ep_end_ts - ep_start_ts) < min_dur:
            continue

        # Attach bites: any bite within
        # [ep_start - bite_attach_window, ep_end + bite_attach_window],
        # skipping bites that already belong to an earlier episode.
        attached_bites = [
            b for idx, b in enumerate(bites)
            if idx not in used_bite_ids
            and (ep_start_ts - bite_attach_window) <= b.ts <= (ep_end_ts + bite_attach_window)
        ]
        for idx, b in enumerate(bites):
            if b in attached_bites:
                used_bite_ids.add(idx)

        # Determine start_reason
        if attached_bites and attached_bites[0].ts <= ep_start_ts + 0.5:
            start_reason = "bite_detected"
            ep_start_ts = min(ep_start_ts, attached_bites[0].ts)
        elif attached_bites:
            start_reason = "bite_detected"
        else:
            start_reason = "chewing_without_observed_bite"

        # End reason: did chewing fade (amplitude trend) or get cut short by other event?
        amps = np.array([c.amplitude for c in cluster], dtype=np.float32)
        decay_window = ep_cfg["amplitude_decay_window"]
        end_reason = "chewing_stopped"
        prob_completion: ProbableCompletion | None = None
        if len(amps) >= decay_window + 2:
            tail = amps[-decay_window:]
            head_med = float(np.median(amps[:decay_window]))
            tail_med = float(np.median(tail))
            decay_ratio = tail_med / max(head_med, 1e-6)
            if decay_ratio < 0.7:
                end_reason = "food_likely_cleared"
                prob_completion = ProbableCompletion(
                    ts=ep_end_ts,
                    kind="food_likely_cleared",
                    confidence=float(min(1.0, (1.0 - decay_ratio) * 1.5)),
                )

        # Confidence: mean chew confidence proxy from prominence percentile
        proms = np.array([c.prominence for c in cluster], dtype=np.float32)
        prom_score = float(np.tanh(np.mean(proms) / (np.median(proms) + 1e-6)))
        ep_conf = float(np.clip(0.5 + 0.5 * prom_score, 0.0, 1.0))

        # Build chew objects with relative_amplitude rolling within episode
        chew_objs: list[Chew] = []
        running = []
        for c in cluster:
            running.append(c.amplitude)
            med = float(np.median(running)) if running else 1.0
            rel = c.amplitude / max(med, 1e-6)
            chew_objs.append(
                Chew(
                    id=f"ch_{chew_id:04d}",
                    start_ts=c.start_ts,
                    peak_ts=c.peak_ts,
                    end_ts=c.end_ts,
                    duration_s=c.end_ts - c.start_ts,
                    amplitude=c.amplitude,
                    relative_amplitude=rel,
                    confidence=float(np.clip(0.4 + 0.6 * np.tanh(c.prominence / (np.median(proms) + 1e-6)), 0, 1)),
                    evidence={
                        "start_frame": c.start_idx,
                        "peak_frame": c.peak_idx,
                        "end_frame": c.end_idx,
                        "talking_overlap": c.talking_overlap,
                        "occluded_overlap": c.occluded_overlap,
                    },
                )
            )
            chew_id += 1

        bite_objs: list[Bite] = []
        for b in attached_bites:
            bite_objs.append(
                Bite(
                    id=f"bt_{bite_id:04d}",
                    ts=b.ts,
                    confidence=b.confidence,
                    evidence=b.evidence,
                )
            )
            bite_id += 1

        episodes.append(
            Episode(
                id=f"ep_{ep_id:03d}",
                start_ts=ep_start_ts,
                end_ts=ep_end_ts,
                start_reason=start_reason,
                end_reason=end_reason,
                confidence=ep_conf,
                uncertainty_notes=[],
                evidence={
                    "n_chews": len(cluster),
                    "amp_head_median": float(np.median(amps[:max(1, decay_window)])),
                    "amp_tail_median": float(np.median(amps[-max(1, decay_window):])),
                },
                bites=bite_objs,
                chews=chew_objs,
                probable_completion=prob_completion,
            )
        )
        ep_id += 1

    # Optional: merge episodes that are still close (post-filter)
    if episodes:
        merged = [episodes[0]]
        for ep in episodes[1:]:
            if ep.start_ts - merged[-1].end_ts <= merge_gap:
                # merge
                prev = merged[-1]
                prev_chews = list(prev.chews) + list(ep.chews)
                prev_bites = list(prev.bites) + list(ep.bites)
                merged[-1] = Episode(
                    id=prev.id,
                    start_ts=prev.start_ts,
                    end_ts=ep.end_ts,
                    start_reason=prev.start_reason,
                    end_reason=ep.end_reason,
                    confidence=(prev.confidence + ep.confidence) / 2,
                    uncertainty_notes=prev.uncertainty_notes,
                    evidence=prev.evidence,
                    bites=prev_bites,
                    chews=prev_chews,
                    probable_completion=ep.probable_completion or prev.probable_completion,
                )
            else:
                merged.append(ep)
        episodes = merged

    # Renumber after merging
    for i, ep in enumerate(episodes):
        ep.id = f"ep_{i:03d}"

    # Even with end_hold respected via merge_gap, mark last frame's end if needed
    _ = end_hold

    return episodes, talking_spans


def overlap_fraction(a: int, b: int, flags: np.ndarray) -> float:
    a = max(0, a); b = min(len(flags), b)
    if b <= a:
        return 0.0
    return float(np.mean(flags[a:b].astype(np.float32)))
