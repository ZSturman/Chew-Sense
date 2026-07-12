"""Tests for Phase-5a two-head ChewNet + composite loss."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch
import torch.nn as nn

from pipeline.config import (
    ChewPhaseHeadConfig,
    FeaturesConfig,
    PipelineConfig,
)
from pipeline.features import extract_features_from_csv
from pipeline.train import (
    TwoHeadChewNet,
    two_head_composite_loss,
)


def test_two_head_forward_shape():
    model = TwoHeadChewNet(input_dim=12, hidden_dim=16)
    x = torch.randn(8, 12)
    out = model(x)
    assert out.shape == (8, 3)
    # phase columns must be tanh-bounded.
    assert torch.all(out[:, 1:].abs() <= 1.0 + 1e-6)


def test_composite_loss_mask_when_not_eating():
    # Build a batch where half the rows are "not eating". Phase targets are
    # garbage on those rows; with the mask on, the MSE term should ignore
    # them and the resulting loss should equal the BCE term times its weight.
    output = torch.zeros(4, 3)
    target = torch.tensor([
        [1.0, 0.5, 0.5],   # eating, "real" phase
        [1.0, -0.5, 0.5],  # eating
        [0.0, 999.0, 999.0],  # not eating - phase garbage
        [0.0, -999.0, 0.0],   # not eating - phase garbage
    ])
    bce = nn.BCEWithLogitsLoss()

    masked_total, parts_masked = two_head_composite_loss(
        output, target,
        eating_weight=1.0, phase_weight=1.0,
        eating_criterion=bce,
        mask_when_not_eating=True,
    )
    unmasked_total, parts_unmasked = two_head_composite_loss(
        output, target,
        eating_weight=1.0, phase_weight=1.0,
        eating_criterion=bce,
        mask_when_not_eating=False,
    )
    # Phase MSE on only-eating rows is bounded by 1.0 (sin/cos targets are in [-1,1]).
    assert parts_masked["loss_phase_mse"] < 1.0
    # Unmasked phase MSE is hugely larger because of the garbage rows.
    assert parts_unmasked["loss_phase_mse"] > parts_masked["loss_phase_mse"] * 1000
    # Total scales accordingly.
    assert unmasked_total.item() > masked_total.item()


def test_composite_loss_no_eating_rows_skips_phase():
    # When no row in the batch is eating, the masked phase MSE must contribute zero.
    output = torch.zeros(3, 3)
    target = torch.tensor([
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [0.0, 0.5, 0.5],
    ])
    bce = nn.BCEWithLogitsLoss()
    total, parts = two_head_composite_loss(
        output, target,
        eating_weight=1.0, phase_weight=10.0,
        eating_criterion=bce,
        mask_when_not_eating=True,
    )
    assert parts["loss_phase_mse"] == 0.0
    assert total.item() == pytest.approx(parts["loss_eating_bce"], rel=1e-5)


def _write_extended_csv(path: Path, n: int = 500, fs: float = 100.0) -> None:
    t = np.arange(n) / fs
    # Half the file is "eating" with a sinusoidal chew phase.
    eating = (t > 1.0) & (t < 3.5)
    theta = np.zeros(n)
    theta[eating] = 2 * np.pi * (t[eating] - 1.0) / 0.5  # 2 Hz chew
    sig = np.where(eating, 0.5 * np.sin(theta), 0.0)
    pd.DataFrame({
        "timestamp": t,
        "ax": sig,
        "ay": np.zeros(n),
        "az": np.full(n, 9.81),
        "gx": np.zeros(n),
        "gy": np.zeros(n),
        "gz": np.zeros(n),
        "label": eating.astype(int),
        "chewing_soft": eating.astype(float),
        "chew_phase_sin": np.sin(theta) * eating.astype(float),
        "chew_phase_cos": np.cos(theta) * eating.astype(float),
    }).to_csv(path, index=False)


def test_features_emit_phase_targets_and_validity_mask_when_extended(tmp_path):
    csv = tmp_path / "session.csv"
    _write_extended_csv(csv)

    # Default features config: extended flag OFF → scalar y.
    X1, y1 = extract_features_from_csv(csv, FeaturesConfig())
    assert y1.ndim == 1

    # extended_chew_phase ON → eating + sin + cos + phase-valid mask.
    cfg = FeaturesConfig(extended_chew_phase=True)
    X2, y2 = extract_features_from_csv(csv, cfg)
    assert y2.ndim == 2
    assert y2.shape[1] == 4
    assert np.any(y2[:, 3] > 0)
    assert X2.shape == X1.shape  # X stays 12-D


def test_extended_features_without_video_labels_only_train_eating_head(tmp_path):
    csv = tmp_path / "binary_only.csv"
    _write_extended_csv(csv)
    df = pd.read_csv(csv).drop(columns=["chew_phase_sin", "chew_phase_cos"])
    df.to_csv(csv, index=False)

    _, y = extract_features_from_csv(csv, FeaturesConfig(extended_chew_phase=True))

    assert y.shape[1] == 4
    assert np.all(y[:, 1:3] == 0)
    assert np.all(y[:, 3] == 0)
