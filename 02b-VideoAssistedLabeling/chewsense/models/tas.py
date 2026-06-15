"""Temporal Action Segmentation head — Phase 3 stub.

Plan: a small 1D-TCN (multi-stage) over per-frame feature vectors derived from
skeleton + face landmarks. For now this file just declares the input/output
contract so the dataset adapters and trainers have something to import.

When implemented the model will be a thin wrapper around a 1D MS-TCN
(Farha & Gall, 2019) with per-frame softmax over CANONICAL_LABELS.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..datasets.base import CANONICAL_LABELS

NUM_CLASSES = len(CANONICAL_LABELS)


@dataclass
class TASConfig:
    num_classes: int = NUM_CLASSES
    n_stages: int = 4
    n_layers: int = 10
    n_features: int = 64
    in_dim: int = 64  # set per feature pipeline


class TASHead:
    """Placeholder. Replace with a torch nn.Module in Phase 3."""

    def __init__(self, cfg: TASConfig):
        self.cfg = cfg

    def __call__(self, features):
        raise NotImplementedError("TASHead is a stub; implement in Phase 3.")
