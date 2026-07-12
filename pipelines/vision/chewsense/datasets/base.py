"""Dataset adapter ABC.

A `DatasetAdapter` exposes a sequence of clips, where each clip yields per-frame
features (or raw video) and per-frame canonical labels. Trainers consume the
adapter only — they never know which dataset they are reading.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

# Canonical taxonomy used everywhere downstream
CANONICAL_LABELS = (
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
)


@dataclass
class Clip:
    clip_id: str
    fps: float
    n_frames: int
    width: int
    height: int
    extras: dict


class DatasetAdapter(ABC):
    name: str = "abstract"

    @abstractmethod
    def list_clips(self) -> list[Clip]: ...

    @abstractmethod
    def get_video_path(self, clip: Clip) -> str | None:
        """Return path to the RGB video for this clip, or None if not available."""

    @abstractmethod
    def get_frame_labels(self, clip: Clip) -> np.ndarray:
        """1D int array of length n_frames, values are indices into CANONICAL_LABELS."""

    def get_skeleton(self, clip: Clip) -> np.ndarray | None:
        """Optional: (n_frames, n_joints, 2 or 3) skeleton."""
        return None
