"""Adapter for videos labelled by ChewSense and corrected by a human via the
review app. Phase 2.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .base import CANONICAL_LABELS, Clip, DatasetAdapter

_CANON_INDEX = {n: i for i, n in enumerate(CANONICAL_LABELS)}


class CustomCorrectedAdapter(DatasetAdapter):
    name = "custom_corrected"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def list_clips(self) -> list[Clip]:
        clips: list[Clip] = []
        for events_json in sorted(self.root.glob("**/events.json")):
            try:
                with open(events_json) as f:
                    ev = json.load(f)
            except Exception:
                continue
            clips.append(Clip(
                clip_id=ev.get("video_id", events_json.parent.name),
                fps=ev.get("fps", 30.0),
                n_frames=int(round(ev.get("duration_s", 0) * ev.get("fps", 30.0))),
                width=ev.get("width", 0),
                height=ev.get("height", 0),
                extras={"out_dir": str(events_json.parent)},
            ))
        return clips

    def get_video_path(self, clip: Clip) -> str | None:
        return None  # corrected adapter is feature-only; original video resolved by user

    def get_frame_labels(self, clip: Clip) -> np.ndarray:
        out = Path(clip.extras["out_dir"])
        # Prefer corrected.parquet, fall back to frame_predictions.parquet
        for cand in ("frame_predictions_corrected.parquet", "frame_predictions.parquet"):
            p = out / cand
            if p.exists():
                try:
                    import pandas as pd
                    df = pd.read_parquet(p)
                    return np.array([_CANON_INDEX.get(x, 0) for x in df["label"].tolist()],
                                    dtype=np.int32)
                except Exception:
                    pass
        return np.zeros(max(clip.n_frames, 1), dtype=np.int32)
