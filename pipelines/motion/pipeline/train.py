"""
Training stage: Train ChewNet model with W&B logging.

This module wraps the existing train_pytorch.py with better W&B integration
and config management.
"""

from __future__ import annotations

import hashlib
import json
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import TensorDataset, DataLoader, WeightedRandomSampler

from .config import PipelineConfig
from .temporal import (
    CONTEXT_SAMPLES,
    MASK_EATING,
    MASK_EVENT,
    MODEL_INPUT_CHANNELS,
    MODEL_OUTPUTS,
    TARGET_EATING,
    TARGET_EVENT,
    TARGET_WIDTH,
    TEMPORAL_CONTRACT_VERSION,
    TemporalChewNet,
    temporal_multitask_loss,
)

try:
    import wandb
    WANDB_AVAILABLE = True
except ImportError:
    WANDB_AVAILABLE = False


class ChewNet(nn.Module):
    """Legacy aggregate-feature MLP for binary eating detection only."""
    
    def __init__(self, input_dim: int, hidden_dim: int = 32, num_outputs: int = 1):
        super().__init__()
        self.num_outputs = num_outputs
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, num_outputs),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.net(x)
        if self.num_outputs == 1:
            return out.squeeze(-1)
        return out


class FocalLoss(nn.Module):
    """Binary focal loss on logits. Reduces the weight of easy examples."""

    def __init__(self, alpha: float = 0.25, gamma: float = 2.0,
                 pos_weight: torch.Tensor | None = None):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.pos_weight = pos_weight

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        bce = F.binary_cross_entropy_with_logits(
            logits, targets, reduction="none", pos_weight=self.pos_weight
        )
        p = torch.sigmoid(logits)
        p_t = p * targets + (1.0 - p) * (1.0 - targets)
        alpha_t = self.alpha * targets + (1.0 - self.alpha) * (1.0 - targets)
        loss = alpha_t * (1.0 - p_t).clamp(min=1e-8).pow(self.gamma) * bce
        return loss.mean()


class _NoisyTensorDataset(torch.utils.data.Dataset):
    """Wrap a tensor pair and add Gaussian noise to features at training time."""

    def __init__(self, X: torch.Tensor, y: torch.Tensor, noise_std: float):
        self.X = X
        self.y = y
        self.noise_std = noise_std

    def __len__(self) -> int:
        return self.X.size(0)

    def __getitem__(self, idx: int):
        x = self.X[idx]
        if self.noise_std > 0:
            x = x + torch.randn_like(x) * self.noise_std
        return x, self.y[idx]


def _resolve_pos_weight(pos_weight_cfg: str, y_bin: np.ndarray) -> float | None:
    """Return scalar pos_weight or None to disable."""
    if pos_weight_cfg is None:
        return None
    s = str(pos_weight_cfg).strip().lower()
    if s in {"none", "off", "false", "0", ""}:
        return None
    if s == "auto":
        n_pos = float((y_bin >= 0.5).sum())
        n_neg = float((y_bin < 0.5).sum())
        if n_pos < 1:
            print("   ⚠️  No positives in training set; pos_weight disabled.")
            return None
        return n_neg / n_pos
    try:
        return float(s)
    except ValueError:
        print(f"   ⚠️  Unrecognized pos_weight={pos_weight_cfg!r}; disabling.")
        return None


def compute_data_hash(X: np.ndarray, y: np.ndarray) -> str:
    """Compute hash of training data for reproducibility tracking."""
    hasher = hashlib.sha256()
    hasher.update(X.tobytes())
    hasher.update(y.tobytes())
    return hasher.hexdigest()[:16]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def train_temporal_model(
    windows: np.ndarray,
    targets: np.ndarray,
    *,
    features_dir: Path,
    output_path: Path,
    config: PipelineConfig,
    dataset_hash: str | None = None,
    data_source: str = "unknown",
    split_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Train the v2 causal TCN without leaking rows between sessions.

    ``windows`` must be ``(N, 7, 150)`` and targets must use the eight-column
    mask-aware layout in :mod:`pipeline.temporal`.  A locked session manifest
    is preferred.  Without one, whole sessions are assigned chronologically;
    a row split is available only through the explicit legacy
    ``training.allow_unsplit`` escape hatch.
    """

    windows = np.asarray(windows, dtype=np.float32)
    targets = np.asarray(targets, dtype=np.float32)
    expected = (len(MODEL_INPUT_CHANNELS), CONTEXT_SAMPLES)
    if windows.ndim != 3 or tuple(windows.shape[1:]) != expected:
        raise ValueError(f"temporal X.npy must have shape (N, {expected[0]}, {expected[1]})")
    if targets.ndim != 2 or targets.shape != (len(windows), TARGET_WIDTH):
        raise ValueError(f"temporal y.npy must have shape (N, {TARGET_WIDTH})")

    temporal_manifest_path = Path(features_dir) / "dataset_manifest.json"
    temporal_manifest: dict[str, Any] = {}
    if temporal_manifest_path.exists():
        temporal_manifest = json.loads(temporal_manifest_path.read_text())
        if (
            temporal_manifest.get("artifact_type") != "temporal_motion_dataset"
            or temporal_manifest.get("temporal_contract_version") != TEMPORAL_CONTRACT_VERSION
        ):
            raise ValueError("features dataset_manifest.json is not a temporal v2 dataset")
        recorded_hash = temporal_manifest.get("dataset_hash")
        if not isinstance(recorded_hash, str) or len(recorded_hash) != 64:
            raise ValueError("temporal dataset manifest has no full SHA-256 dataset hash")
        if dataset_hash is not None and recorded_hash != dataset_hash:
            raise ValueError("requested dataset hash does not match temporal dataset manifest")
        dataset_hash = recorded_hash
        for name, metadata in temporal_manifest.get("arrays", {}).items():
            path = Path(features_dir) / name
            if not path.exists() or not isinstance(metadata, dict):
                raise ValueError(f"temporal dataset array is missing: {name}")
            digest = _file_sha256(path)
            if digest != metadata.get("sha256"):
                raise ValueError(f"temporal dataset array hash mismatch: {name}")

    session_ids_path = Path(features_dir) / "session_ids.npy"
    session_ids = (
        np.load(session_ids_path, allow_pickle=True).astype(str)
        if session_ids_path.exists()
        else None
    )
    if session_ids is not None and len(session_ids) != len(windows):
        raise ValueError("session_ids.npy length does not match temporal windows")
    train_idx: np.ndarray | None = None
    val_idx: np.ndarray | None = None
    train_sessions: list[str] = []
    validation_sessions: list[str] = []
    split_name = ""

    if session_ids is not None and split_manifest_path and Path(split_manifest_path).exists():
        manifest = json.loads(Path(split_manifest_path).read_text())
        if temporal_manifest and manifest.get("dataset_hash") != dataset_hash:
            raise ValueError("locked split manifest does not match temporal dataset hash")
        splits = manifest.get("splits", {})
        train_sessions = list(map(str, splits.get("train", [])))
        validation_sessions = list(map(str, splits.get("validation_locked", [])))
        train_idx = np.flatnonzero(np.isin(session_ids, train_sessions))
        val_idx = np.flatnonzero(np.isin(session_ids, validation_sessions))
        if len(train_idx) and len(val_idx):
            split_name = "locked_session_split"

    if not split_name and session_ids is not None and config.training.allow_unsplit:
        unique = sorted(set(session_ids))
        if len(unique) >= 2:
            n_val_sessions = max(1, int(round(len(unique) * config.training.val_ratio)))
            validation_sessions = unique[-n_val_sessions:]
            train_sessions = unique[:-n_val_sessions]
            train_idx = np.flatnonzero(np.isin(session_ids, train_sessions))
            val_idx = np.flatnonzero(np.isin(session_ids, validation_sessions))
            split_name = "chronological_session_split"

    if not split_name:
        if not config.training.allow_unsplit:
            raise ValueError(
                "temporal training requires a locked split manifest with non-empty "
                "train and validation_locked session assignments"
            )
        cutoff = max(1, int(round(len(windows) * (1.0 - config.training.val_ratio))))
        train_idx = np.arange(cutoff)
        val_idx = np.arange(cutoff, len(windows))
        split_name = "explicit_unsafe_row_split"

    assert train_idx is not None and val_idx is not None
    if not len(train_idx) or not len(val_idx):
        raise ValueError("temporal train and validation splits must both be non-empty")
    if not np.any(
        (targets[train_idx, MASK_EVENT] > 0)
        & (targets[train_idx, TARGET_EVENT] > 0.5)
    ):
        raise ValueError(
            "temporal training split has no reviewed observable chew events; "
            "legacy/coarse sessions may supplement eating but cannot train the event head"
        )

    def require_eating_classes(indices: np.ndarray, split_label: str) -> None:
        supervised = indices[targets[indices, MASK_EATING] >= 0.5]
        labels = targets[supervised, TARGET_EATING] >= 0.5
        if not len(supervised) or not labels.any() or labels.all():
            raise ValueError(
                f"temporal {split_label} split must contain supervised eating and negative rows"
            )

    require_eating_classes(train_idx, "train")
    require_eating_classes(val_idx, "validation")

    # Per-axis normalization uses valid training samples only.  The validity
    # mask remains binary and invalid normalized values stay exactly zero.
    mean = np.zeros(6, dtype=np.float32)
    std = np.ones(6, dtype=np.float32)
    train_valid = windows[train_idx, 6, :] >= 0.5
    for channel in range(6):
        values = windows[train_idx, channel, :][train_valid]
        if len(values):
            mean[channel] = float(values.mean())
            channel_std = float(values.std())
            std[channel] = channel_std if channel_std > 1e-8 else 1.0

    def normalize(source: np.ndarray) -> np.ndarray:
        result = source.copy()
        result[:, :6, :] = (
            result[:, :6, :] - mean.reshape(1, 6, 1)
        ) / std.reshape(1, 6, 1)
        result[:, :6, :] *= result[:, 6:7, :]
        return result

    train_x = torch.from_numpy(normalize(windows[train_idx]))
    val_x = torch.from_numpy(normalize(windows[val_idx]))
    train_y = torch.from_numpy(targets[train_idx])
    val_y = torch.from_numpy(targets[val_idx])
    train_loader = DataLoader(
        TensorDataset(train_x, train_y),
        batch_size=config.training.batch_size,
        shuffle=True,
    )
    val_loader = DataLoader(
        TensorDataset(val_x, val_y),
        batch_size=config.training.batch_size,
        shuffle=False,
    )

    temporal = config.temporal_model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = TemporalChewNet(
        channels=temporal.channels,
        kernel_size=temporal.kernel_size,
        dropout=temporal.dropout,
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.training.lr)
    best_loss = float("inf")
    best_state: dict[str, torch.Tensor] | None = None
    epochs_no_improve = 0
    epoch = 0

    def loss_for(output: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        loss, _ = temporal_multitask_loss(
            output,
            target,
            eating_weight=temporal.eating_weight,
            event_weight=temporal.event_weight,
            phase_weight=temporal.phase_weight,
            confidence_weight=temporal.confidence_weight,
        )
        return loss

    for epoch in range(1, config.training.epochs + 1):
        model.train()
        train_total = 0.0
        for batch_x, batch_y in train_loader:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)
            optimizer.zero_grad()
            loss = loss_for(model(batch_x), batch_y)
            loss.backward()
            optimizer.step()
            train_total += float(loss.detach()) * len(batch_x)

        model.eval()
        validation_total = 0.0
        with torch.no_grad():
            for batch_x, batch_y in val_loader:
                batch_x, batch_y = batch_x.to(device), batch_y.to(device)
                validation_total += float(loss_for(model(batch_x), batch_y)) * len(batch_x)
        train_loss = train_total / len(train_idx)
        validation_loss = validation_total / len(val_idx)
        print(
            f"Epoch {epoch:03d} | temporal_train_loss={train_loss:.4f} "
            f"| temporal_val_loss={validation_loss:.4f}"
        )
        if validation_loss < best_loss - 1e-4:
            best_loss = validation_loss
            best_state = copy.deepcopy(model.state_dict())
            epochs_no_improve = 0
        else:
            epochs_no_improve += 1
            if epochs_no_improve >= config.training.patience:
                break

    if best_state is None:
        best_state = copy.deepcopy(model.state_dict())
    dataset_hash = dataset_hash or compute_data_hash(windows, targets)
    model_id = (
        f"chewnet-temporal-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        f"-{dataset_hash[:8]}"
    )
    checkpoint = {
        "model_id": model_id,
        "mode": "temporal_tcn",
        "temporal_contract_version": TEMPORAL_CONTRACT_VERSION,
        "model_state_dict": best_state,
        "input_channels": len(MODEL_INPUT_CHANNELS),
        "input_names": list(MODEL_INPUT_CHANNELS),
        "context_samples": CONTEXT_SAMPLES,
        "output_names": list(MODEL_OUTPUTS),
        "num_outputs": len(MODEL_OUTPUTS),
        "channels": list(temporal.channels),
        "kernel_size": temporal.kernel_size,
        "dropout": temporal.dropout,
        "mean": mean,
        "std": std,
        "best_val_loss": best_loss,
        "dataset_hash": dataset_hash,
        "data_source": data_source,
        "config": config.to_dict(),
        "split_name": split_name,
        "split_manifest_path": str(split_manifest_path) if split_manifest_path else None,
        "split_train_sessions": train_sessions,
        "split_validation_sessions": validation_sessions,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, output_path)
    return {
        "output_path": output_path,
        "model_id": model_id,
        "best_val_loss": best_loss,
        "final_epoch": epoch,
        "wandb_run_id": None,
        "wandb_run_url": None,
        "mean": mean.tolist(),
        "std": std.tolist(),
    }


def train_model(
    features_dir: Path,
    output_path: Path,
    config: PipelineConfig,
    dataset_hash: Optional[str] = None,
    data_source: str = "unknown",
    split_manifest_path: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Train ChewNet model on precomputed features.
    
    Args:
        features_dir: Directory containing X.npy and y.npy
        output_path: Path to save model checkpoint
        config: Pipeline configuration
        dataset_hash: Optional hash from ingest stage (for W&B)
        data_source: "sample" or "user" (for W&B)
    
    Returns:
        Dictionary with training results and W&B run info
    """
    features_dir = Path(features_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Load data
    X = np.load(features_dir / "X.npy").astype(np.float32)
    y = np.load(features_dir / "y.npy").astype(np.float32)
    
    if X.ndim == 3:
        return train_temporal_model(
            X,
            y,
            features_dir=features_dir,
            output_path=output_path,
            config=config,
            dataset_hash=dataset_hash,
            data_source=data_source,
            split_manifest_path=split_manifest_path,
        )
    if config.training.arch == "causal_tcn":
        raise ValueError(
            "causal_tcn requires temporal X.npy shaped (N, 7, 150); "
            "12-D aggregate features are legacy eating-only inputs"
        )
    if y.ndim != 1:
        raise ValueError(
            "aggregate-feature MLP accepts scalar eating targets only; "
            "use canonical v2 temporal windows for chew event and phase supervision"
        )

    n_samples, input_dim = X.shape

    print(f"Loaded X: {X.shape}, y: {y.shape}")
    print("Mode: legacy binary eating classification")
    
    # Compute data hash if not provided
    if dataset_hash is None:
        dataset_hash = compute_data_hash(X, y)
    
    # Training config
    tc = config.training
    
    # Initialize W&B
    wandb_run_id = None
    wandb_run_url = None
    use_wandb = config.wandb.enabled and WANDB_AVAILABLE
    
    if use_wandb:
        wandb_config = {
            **config.to_dict(),
            "dataset_hash": dataset_hash,
            "data_source": data_source,
            "n_samples": n_samples,
            "input_dim": input_dim,
            "num_outputs": 1,
            "mode": "binary",
            "architecture": "3-layer MLP",
        }
        
        wandb.init(
            project=config.wandb.project,
            entity=config.wandb.entity,
            config=wandb_config,
        )
        wandb_run_id = wandb.run.id
        wandb_run_url = wandb.run.get_url()
        print(f"W&B run: {wandb_run_url}")
    
    # Train/val split. Prefer locked session splits when available.
    split_name = "random_row_split"
    split_train_sessions: list[str] = []
    split_val_sessions: list[str] = []

    X_train: np.ndarray
    X_val: np.ndarray
    y_train: np.ndarray
    y_val: np.ndarray

    session_ids_path = features_dir / "session_ids.npy"
    have_session_ids = session_ids_path.exists()

    if split_manifest_path and have_session_ids:
        with open(split_manifest_path, "r") as f:
            split_manifest = json.load(f)
        splits = split_manifest.get("splits", {})
        train_sessions = set(splits.get("train", []))
        val_sessions = set(splits.get("validation_locked", []))
        session_ids = np.load(session_ids_path, allow_pickle=True).astype(str)
        train_idx = np.flatnonzero(np.isin(session_ids, list(train_sessions)))
        val_idx = np.flatnonzero(np.isin(session_ids, list(val_sessions)))

        # Reject single-class train or validation splits — they break
        # learning (single-class train) or make val_loss uninformative
        # (single-class val). Fall back to a session-aware random split.
        train_single_class = False
        val_single_class = False
        eating_targets = y
        if len(train_idx) > 0:
            yt = (eating_targets[train_idx] >= 0.5).astype(np.int32)
            train_single_class = (yt.sum() == 0) or (yt.sum() == len(yt))
        if len(val_idx) > 0:
            yv = (eating_targets[val_idx] >= 0.5).astype(np.int32)
            val_single_class = (yv.sum() == 0) or (yv.sum() == len(yv))

        if (
            len(train_idx) > 0
            and len(val_idx) > 0
            and not train_single_class
            and not val_single_class
        ):
            train_idx = np.random.permutation(train_idx)
            X_train, X_val = X[train_idx], X[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]
            split_name = "locked_session_split"
            split_train_sessions = sorted(train_sessions)
            split_val_sessions = sorted(val_sessions)
        else:
            if train_single_class:
                yt_sum = int((eating_targets[train_idx] >= 0.5).sum())
                print(
                    "⚠️  Locked TRAIN split is single-class "
                    f"(train pos={yt_sum}/{len(train_idx)}); "
                    "cannot learn from it. Falling back to session-aware split."
                )
            elif val_single_class:
                yv_sum = int((eating_targets[val_idx] >= 0.5).sum())
                print(
                    "⚠️  Locked validation split is single-class "
                    f"(val pos={yv_sum}/{len(val_idx)}); "
                    "falling back to session-aware split."
                )
            else:
                print("Locked split unavailable or empty; falling back to session-aware split.")
            split_manifest_path = None

    if split_name == "random_row_split":
        # Fallback: session-aware random split (no within-session leakage).
        # Prefer a split where train has both classes (essential for learning)
        # and val also has both classes (essential for meaningful val_loss).
        # If train cannot be made two-class with any seed, we have a data
        # problem and warn the user explicitly.
        if have_session_ids:
            session_ids = np.load(session_ids_path, allow_pickle=True).astype(str)
            unique_sessions = np.array(sorted(set(session_ids)))
            n_val_sessions = max(1, int(len(unique_sessions) * tc.val_ratio))
            best: tuple[np.ndarray, np.ndarray, set, int] | None = None  # +score
            for seed in range(64):
                rng = np.random.default_rng(42 + seed)
                order = rng.permutation(len(unique_sessions))
                val_set = set(unique_sessions[order[:n_val_sessions]])
                v_idx = np.flatnonzero(np.isin(session_ids, list(val_set)))
                t_idx = np.flatnonzero(~np.isin(session_ids, list(val_set)))
                if len(t_idx) == 0 or len(v_idx) == 0:
                    continue
                source_targets = y
                yv = (source_targets[v_idx] >= 0.5).astype(np.int32)
                yt = (source_targets[t_idx] >= 0.5).astype(np.int32)
                two_class_val = 0 < int(yv.sum()) < len(yv)
                two_class_train = 0 < int(yt.sum()) < len(yt)
                # Score: train-two-class is worth 2; val-two-class is worth 1.
                score = (2 if two_class_train else 0) + (1 if two_class_val else 0)
                if best is None or score > best[3]:
                    best = (t_idx, v_idx, val_set, score)
                if score == 3:
                    break
            if best is not None:
                train_idx, val_idx, val_set, score = best
                X_train, X_val = X[train_idx], X[val_idx]
                y_train, y_val = y[train_idx], y[val_idx]
                split_name = "session_aware_random_split"
                if score < 3:
                    print(
                        "⚠️  Best session-aware split has score "
                        f"{score}/3 (train two-class={(score & 2) != 0}, "
                        f"val two-class={(score & 1) != 0}). "
                        "Add more sessions of the under-represented class to fix this."
                    )

        if split_name == "random_row_split":
            if not tc.allow_unsplit and split_manifest_path is None:
                # Caller asked for locked split but we ended up with no session info.
                # Still allow training (no hard abort) but warn loudly.
                print(
                    "   ⚠️  No session_ids.npy and no locked split — training with row-level "
                    "shuffle. Consider rerunning feature extraction or passing "
                    "training.allow_unsplit=true to silence."
                )
            n_val = int(n_samples * tc.val_ratio)
            n_train_count = n_samples - n_val
            indices = np.random.permutation(n_samples)
            X = X[indices]
            y = y[indices]
            X_train, X_val = X[:n_train_count], X[n_train_count:]
            y_train, y_val = y[:n_train_count], y[n_train_count:]

    n_train = len(X_train)
    n_val = len(X_val)
    
    # Normalization (from training data only)
    mean = X_train.mean(axis=0)
    std = X_train.std(axis=0)
    std[std == 0] = 1.0
    
    X_train_norm = (X_train - mean) / std
    X_val_norm = (X_val - mean) / std
    
    # Dataloaders
    feature_noise = float(getattr(tc, "feature_noise_std", 0.0))
    train_X_t = torch.from_numpy(X_train_norm)
    train_y_t = torch.from_numpy(y_train)
    val_X_t = torch.from_numpy(X_val_norm)
    val_y_t = torch.from_numpy(y_val)

    train_ds: torch.utils.data.Dataset
    if feature_noise > 0:
        train_ds = _NoisyTensorDataset(train_X_t, train_y_t, feature_noise)
    else:
        train_ds = TensorDataset(train_X_t, train_y_t)
    val_ds = TensorDataset(val_X_t, val_y_t)

    # Class-balanced sampler for the legacy binary eating model.
    sampler = None
    pos_weight_value: float | None = None
    y_train_bin = (y_train >= config.evaluation.chew_threshold).astype(np.int64)
    n_pos_train = int(y_train_bin.sum())
    n_neg_train = int(len(y_train_bin) - n_pos_train)
    n_pos_val = int((y_val >= config.evaluation.chew_threshold).sum())
    n_neg_val = int(len(y_val) - n_pos_val)
    print("\nDATASET BALANCE")
    print("---------------")
    print(f"  train: pos={n_pos_train}  neg={n_neg_train}  "
          f"pos_pct={100.0 * n_pos_train / max(1, len(y_train_bin)):.1f}%")
    print(f"    val: pos={n_pos_val}  neg={n_neg_val}  "
          f"pos_pct={100.0 * n_pos_val / max(1, len(y_val)):.1f}%")

    pos_weight_value = _resolve_pos_weight(tc.pos_weight, y_train_bin)
    if pos_weight_value is not None:
        print(f"  pos_weight: {pos_weight_value:.3f}")

    if tc.use_weighted_sampler and n_pos_train > 0 and n_neg_train > 0:
        class_w = np.where(y_train_bin == 1,
                           1.0 / max(n_pos_train, 1),
                           1.0 / max(n_neg_train, 1)).astype(np.float64)
        sampler = WeightedRandomSampler(
            weights=torch.from_numpy(class_w),
            num_samples=len(class_w),
            replacement=True,
        )
        print("  weighted sampler: ON")
    else:
        print("  weighted sampler: OFF")

    if sampler is not None:
        train_loader = DataLoader(train_ds, batch_size=tc.batch_size, sampler=sampler)
    else:
        train_loader = DataLoader(train_ds, batch_size=tc.batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=tc.batch_size, shuffle=False)
    
    # Model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = ChewNet(input_dim=input_dim, hidden_dim=tc.hidden_dim).to(device)

    pw_tensor = (
        torch.tensor([pos_weight_value], dtype=torch.float32, device=device)
        if pos_weight_value is not None else None
    )
    if tc.loss == "focal":
        criterion = FocalLoss(
            alpha=tc.focal_alpha, gamma=tc.focal_gamma, pos_weight=pw_tensor
        )
        print(f"  loss: focal(alpha={tc.focal_alpha}, gamma={tc.focal_gamma})")
    else:
        criterion = nn.BCEWithLogitsLoss(pos_weight=pw_tensor)
        print("  loss: bce_with_logits"
              + (f"(pos_weight={pos_weight_value:.3f})" if pw_tensor is not None else ""))
    optimizer = torch.optim.Adam(model.parameters(), lr=tc.lr)
    
    best_val_loss = float("inf")
    best_state = None
    epochs_no_improve = 0
    
    # Training loop
    for epoch in range(1, tc.epochs + 1):
        model.train()
        train_loss = 0.0
        
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            
            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            optimizer.step()
            
            train_loss += loss.item() * xb.size(0)
        
        train_loss /= n_train
        
        # Validation
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                logits = model(xb)
                loss = criterion(logits, yb)
                val_loss += loss.item() * xb.size(0)
        
        val_loss /= n_val
        
        print(f"Epoch {epoch:03d} | train_loss={train_loss:.4f} | val_loss={val_loss:.4f}")
        
        if use_wandb:
            wandb.log({
                "epoch": epoch,
                "train_loss": train_loss,
                "val_loss": val_loss,
            })
        
        # Early stopping
        if val_loss < best_val_loss - 1e-4:
            best_val_loss = val_loss
            best_state = model.state_dict()
            epochs_no_improve = 0
        else:
            epochs_no_improve += 1
            if epochs_no_improve >= tc.patience:
                print("Early stopping triggered.")
                break
    
    if best_state is None:
        best_state = model.state_dict()
    
    model_id = f"chewnet-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{dataset_hash[:8]}"

    # Save checkpoint with all metadata
    checkpoint = {
        "model_id": model_id,
        "model_state_dict": best_state,
        "input_dim": input_dim,
        "hidden_dim": tc.hidden_dim,
        "num_outputs": 1,
        "mean": mean,
        "std": std,
        "best_val_loss": float(best_val_loss),
        "dataset_hash": dataset_hash,
        "config": config.to_dict(),
        "data_source": data_source,
        "split_name": split_name,
        "split_manifest_path": str(split_manifest_path) if split_manifest_path else None,
        "split_train_sessions": split_train_sessions,
        "split_validation_sessions": split_val_sessions,
        "loss": getattr(tc, "loss", "bce"),
        "pos_weight_used": pos_weight_value,
        "weighted_sampler_used": sampler is not None,
        "feature_noise_std": feature_noise,
        "mode": "binary",
    }
    
    if use_wandb:
        checkpoint["wandb_run_id"] = wandb_run_id
        checkpoint["wandb_run_url"] = wandb_run_url
    
    torch.save(checkpoint, output_path)
    print(f"Saved model to: {output_path}")
    print(f"Model ID: {model_id}")
    print(f"Best val loss: {best_val_loss:.4f}")
    
    # Log to W&B
    if use_wandb:
        artifact = wandb.Artifact(
            name="chewnet-model",
            type="model",
            description="ChewNet binary chewing detection model",
            metadata={
                "best_val_loss": best_val_loss,
                "dataset_hash": dataset_hash,
                "model_id": model_id,
                "split_name": split_name,
            },
        )
        artifact.add_file(str(output_path))
        wandb.log_artifact(artifact)
        
        wandb.summary["best_val_loss"] = best_val_loss
        wandb.summary["final_epoch"] = epoch
        
        wandb.finish()
    
    return {
        "output_path": output_path,
        "model_id": model_id,
        "best_val_loss": best_val_loss,
        "final_epoch": epoch,
        "wandb_run_id": wandb_run_id,
        "wandb_run_url": wandb_run_url,
        "mean": mean.tolist(),
        "std": std.tolist(),
    }
