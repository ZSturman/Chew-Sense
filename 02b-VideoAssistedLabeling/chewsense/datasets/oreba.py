"""OREBA adapter (stub). Phase 2."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .base import Clip, DatasetAdapter


class OrebaAdapter(DatasetAdapter):
    name = "oreba"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def list_clips(self) -> list[Clip]:
        return []

    def get_video_path(self, clip: Clip) -> str | None:
        return clip.extras.get("video_path")

    def get_frame_labels(self, clip: Clip) -> np.ndarray:
        return np.zeros(max(clip.n_frames, 1), dtype=np.int32)
