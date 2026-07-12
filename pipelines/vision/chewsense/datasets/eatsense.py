"""EatSense adapter (stub).

Reads the EatSense dataset layout (https://groups.inf.ed.ac.uk/vision/DATASETS/EATSENSE/):
- RGB videos: deepfaked.zip -> {Video}_anonymized.mp4
- 2D/3D True poses: True2D3D.zip -> True2D/{Video}.mp4_preds_HigherHRNet.csv, True3D/{Video}.csv
- Per-frame action labels: TODO — load from the dataset's label CSVs once we
  inspect the actual schema (the dataset card mentions 4 abstraction levels of
  dense frame-wise labels but the column names are only documented in the readme
  packaged with the dataset).

For now this stub:
- knows how to locate clips by glob
- maps EatSense atomic-action strings to CANONICAL_LABELS via configs/eatsense_label_map.yaml
- exposes a `--stats` CLI

Phase 2 will fill in the label-loading code once the dataset is actually present.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import yaml

from .base import CANONICAL_LABELS, Clip, DatasetAdapter

EATSENSE_TO_CANONICAL_DEFAULT = {
    # Best-effort mapping; refine when the dataset is downloaded and inspected.
    "chew": "chewing",
    "chewing": "chewing",
    "move_hand_toward_mouth": "hand_to_mouth",
    "move_hand_away_from_mouth": "idle",
    "pick_food": "idle",
    "drink": "drinking",
    "rest": "idle",
    "stationary": "idle",
    "talk": "talking",
    "swallow": "probable_swallow",
}


class EatSenseAdapter(DatasetAdapter):
    name = "eatsense"

    def __init__(self, root: str | Path, label_map_path: str | Path | None = None):
        self.root = Path(root)
        if label_map_path and Path(label_map_path).exists():
            with open(label_map_path) as f:
                self.label_map = yaml.safe_load(f)
        else:
            self.label_map = dict(EATSENSE_TO_CANONICAL_DEFAULT)

    def list_clips(self) -> list[Clip]:
        clips: list[Clip] = []
        for video in sorted(self.root.glob("**/*_anonymized.mp4")):
            stem = video.stem.replace("_anonymized", "")
            clips.append(Clip(
                clip_id=stem,
                fps=15.0,
                n_frames=0,        # filled by the trainer if needed via probe()
                width=0, height=0,
                extras={"video_path": str(video)},
            ))
        return clips

    def get_video_path(self, clip: Clip) -> str | None:
        return clip.extras.get("video_path")

    def get_frame_labels(self, clip: Clip) -> np.ndarray:
        # TODO(phase 2): parse the dataset's per-frame label file.
        # Returns all-`idle` until labels are wired in.
        return np.zeros(max(clip.n_frames, 1), dtype=np.int32)


def _stats_main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=str)
    args = p.parse_args()
    adapter = EatSenseAdapter(args.root)
    clips = adapter.list_clips()
    print(f"clips found: {len(clips)}")
    for c in clips[:5]:
        print(" ", c.clip_id, c.extras.get("video_path"))
    print(f"canonical labels: {CANONICAL_LABELS}")


if __name__ == "__main__":
    _stats_main()
