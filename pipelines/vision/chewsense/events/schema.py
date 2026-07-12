"""Pydantic schema for ChewSense events.

This is the canonical contract between the pipeline, the exporter, and the
future correction/review app. Bump `SCHEMA_VERSION` on breaking changes; readers
must check it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0.0"


# ----------------------------- Frame-level labels ----------------------------

FrameLabel = Literal[
    "idle",
    "hand_to_mouth",
    "food_entry",
    "mouth_closing",
    "chewing",
    "inter_chew_pause",
    "probable_swallow",
    "drinking",
    "talking",
    "occluded",
    "other",
]


# --------------------------- Mouth state / cycles ---------------------------

MouthState = Literal["closed", "opening", "open", "closing", "unknown"]
MouthCycleKind = Literal["visible_open", "closed_lip", "ambiguous"]
PoseClass = Literal["frontal", "profile", "tilted_down", "other"]


class MouthStateSegment(BaseModel):
    state: MouthState
    start_ts: float
    end_ts: float
    duration_s: float
    pose_class: PoseClass = "other"


class MouthCycle(BaseModel):
    id: str
    start_ts: float
    peak_ts: float
    end_ts: float
    duration_s: float
    kind: MouthCycleKind
    peak_magnitude: float = Field(description="unified open-magnitude z-score at peak")
    pose_class: PoseClass = "other"
    confidence: float = 0.0
    evidence: dict = Field(default_factory=dict)


class SessionCounters(BaseModel):
    visible_open: int = 0
    closed_lip: int = 0
    ambiguous: int = 0
    total: int = 0


# ----------------------------- Atomic events --------------------------------

class Bite(BaseModel):
    id: str
    ts: float = Field(description="seconds in original video time domain")
    confidence: float
    evidence: dict = Field(default_factory=dict)


class Chew(BaseModel):
    id: str
    start_ts: float
    peak_ts: float
    end_ts: float
    duration_s: float
    amplitude: float = Field(description="normalized jaw-open amplitude (face-scale units)")
    relative_amplitude: float = Field(
        description="amplitude / median(amplitudes earlier in episode)"
    )
    confidence: float
    evidence: dict = Field(default_factory=dict)


class ProbableCompletion(BaseModel):
    ts: float
    kind: Literal[
        "probable_swallow",
        "likely_episode_end",
        "chewing_stopped",
        "food_likely_cleared",
    ]
    confidence: float


# ----------------------------- Episodes -------------------------------------

EpisodeStartReason = Literal[
    "bite_detected",
    "chewing_without_observed_bite",
    "food_entry",
]
EpisodeEndReason = Literal[
    "chewing_stopped",
    "food_likely_cleared",
    "new_bite_started",
    "occlusion_episode_break",
    "interrupted_by_drinking",
    "interrupted_by_talking",
    "video_ended",
]


class Episode(BaseModel):
    id: str
    start_ts: float
    end_ts: float
    start_reason: EpisodeStartReason
    end_reason: EpisodeEndReason
    confidence: float
    uncertainty_notes: list[str] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    bites: list[Bite] = Field(default_factory=list)
    chews: list[Chew] = Field(default_factory=list)
    probable_completion: ProbableCompletion | None = None

    @property
    def chew_count(self) -> int:
        return len(self.chews)

    @property
    def bite_count(self) -> int:
        return len(self.bites)

    @property
    def duration_s(self) -> float:
        return self.end_ts - self.start_ts


# ----------------------------- Spans / video -------------------------------

class Span(BaseModel):
    start_ts: float
    end_ts: float
    kind: str
    confidence: float = 1.0
    note: str = ""


# ----------------------------- Quality metrics -----------------------------

QualityFlag = Literal[
    "too_fast",
    "too_slow",
    "too_few_chews_per_bite",
    "too_many_chews_per_bite",
    "rhythm_irregular",
    "inter_session_gap_too_short",
    "bite_appears_oversized",
    "low_signal_quality",
]


class EpisodeQuality(BaseModel):
    episode_id: str
    chew_count: int
    duration_s: float
    chew_rate_hz: float
    chew_rate_cv: float                 # coefficient-of-variation of inter-peak intervals
    mean_chew_duration_s: float
    amplitude_decay_slope: float        # negative = slowing/decaying
    chews_per_bite: float | None        # None if no bite detected
    bite_size_norm: float | None        # max jaw_open during food-entry window, face-scale units
    flags: list[QualityFlag] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SessionQuality(BaseModel):
    """Aggregate metrics across all episodes in this video."""
    n_episodes: int
    n_bites: int
    n_chews: int
    total_eating_duration_s: float
    mean_chews_per_bite: float | None
    mean_chew_rate_hz: float | None
    rhythm_cv: float | None
    inter_episode_gaps_s: list[float] = Field(default_factory=list)
    flags: list[QualityFlag] = Field(default_factory=list)
    user_baseline_used: bool = False
    user_id: str | None = None


class QualityReport(BaseModel):
    session: SessionQuality
    episodes: list[EpisodeQuality] = Field(default_factory=list)


class SignalQuality(BaseModel):
    """Phase B: per-video signal-health summary.

    Lets reviewers tell at a glance whether the labels are trustworthy or the
    inputs (face detection, blendshapes, etc.) were too noisy to rely on.
    """
    n_frames: int
    mean_face_confidence: float
    median_face_confidence: float
    face_detected_fraction: float
    unknown_state_fraction: float
    occluded_fraction: float
    no_channel_fraction: float            # frames with zero consensus channels
    channel_coverage: dict[str, float] = Field(default_factory=dict)  # bs/inner/proxy
    talking_fraction: float
    hand_to_mouth_fraction: float


class VideoEvents(BaseModel):
    video_id: str
    schema_version: str = SCHEMA_VERSION
    duration_s: float
    fps: float
    width: int
    height: int
    episodes: list[Episode] = Field(default_factory=list)
    non_eating_mouth_spans: list[Span] = Field(default_factory=list)
    occlusion_spans: list[Span] = Field(default_factory=list)
    drinking_spans: list[Span] = Field(default_factory=list)
    quality: QualityReport | None = None
    signal_quality: SignalQuality | None = None
    # Phase 1 additions — emitted alongside the existing chew/episode streams.
    mouth_state_segments: list[MouthStateSegment] = Field(default_factory=list)
    mouth_cycles: list[MouthCycle] = Field(default_factory=list)
    cycle_counts: SessionCounters = Field(default_factory=SessionCounters)


# ----------------------------- Per-frame record -----------------------------

class FrameRecord(BaseModel):
    """One row of `frame_predictions.parquet`. Kept lean; large arrays go to .npy."""

    frame_idx: int
    ts: float
    face_detected: bool
    face_scale: float = 0.0
    jaw_open: float = 0.0          # raw normalized lip distance
    jaw_open_smooth: float = 0.0   # smoothed
    jaw_open_bp: float = 0.0       # bandpassed
    mouth_width: float = 0.0
    head_yaw: float = 0.0
    head_pitch: float = 0.0
    hand_to_mouth_min: float = float("inf")  # min over detected hands, normalized
    n_hands: int = 0
    is_speaking: bool = False
    audio_rms: float = 0.0
    occluded: bool = False
    label: FrameLabel = "idle"
    label_confidence: float = 0.0
    # Phase 1 additions (mouth-state FSM)
    mouth_state: MouthState = "unknown"
    pose_class: PoseClass = "other"
    inner_lip_open: float = 0.0
    outer_lip_open: float = 0.0
    lip_aperture_ratio: float = 0.0
    chin_to_nose: float = 0.0
    jaw_proxy_residual: float = 0.0
    open_magnitude_z: float = 0.0
    # Phase 1.5 additions (MediaPipe blendshapes — pose- and lip-closure-invariant)
    jaw_open_bs: float = 0.0
    mouth_close_bs: float = 0.0
    mouth_funnel_bs: float = 0.0
    mouth_pucker_bs: float = 0.0
    mouth_roll_lower_bs: float = 0.0
    mouth_roll_upper_bs: float = 0.0
    mouth_press_left_bs: float = 0.0
    mouth_press_right_bs: float = 0.0
    mouth_smile_left_bs: float = 0.0
    mouth_smile_right_bs: float = 0.0
    jaw_open_bs_z: float = 0.0
    face_confidence: float = 0.0
    used_channels: int = 0          # bit-mask: 1=jaw_open_bs, 2=inner_lip, 4=jaw_proxy
