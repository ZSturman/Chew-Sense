"""Pydantic schema for automatic Vision telemetry.

Human-reviewed truth lives in :mod:`chewsense.canonical`.  This payload keeps
the fused mouth-state/cycle stream, independent food-entry proposals, and the
signals needed to inspect a detector run.  It must never be treated as reviewed
annotations.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "2.0.0"


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


class FoodEntryProposal(BaseModel):
    """Automatic telemetry only; never a verified food-entry boundary."""

    id: str
    food_entry_ts: float = Field(description="seconds in original video time domain")
    confidence: float
    evidence: dict = Field(default_factory=dict)
# ----------------------------- Spans / video -------------------------------

class Span(BaseModel):
    start_ts: float
    end_ts: float
    kind: str
    confidence: float = 1.0
    note: str = ""


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
    non_eating_mouth_spans: list[Span] = Field(default_factory=list)
    occlusion_spans: list[Span] = Field(default_factory=list)
    drinking_spans: list[Span] = Field(default_factory=list)
    signal_quality: SignalQuality | None = None
    mouth_state_segments: list[MouthStateSegment] = Field(default_factory=list)
    mouth_cycles: list[MouthCycle] = Field(default_factory=list)
    food_entry_proposals: list[FoodEntryProposal] = Field(default_factory=list)
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
    head_roll: float = 0.0
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
