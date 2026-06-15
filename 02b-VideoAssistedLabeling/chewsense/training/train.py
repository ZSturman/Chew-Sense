"""Train the chew-head from one or more (features.parquet, labels.parquet) pairs.

Usage from CLI:
    chewsense train-chew-head \\
        --pair features1.parquet:labels1.parquet \\
        --pair features2.parquet:labels2.parquet \\
        --out ~/.cache/chewsense/models/chew_head.pt
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from ..features.extract import FEATURE_COLS
from ..models.chew_head import ChewHeadConfig, build_model

try:
    import torch
    from torch.utils.data import DataLoader, Dataset
    _HAS_TORCH = True
except Exception:
    _HAS_TORCH = False


def _windowize(features: np.ndarray, weights: np.ndarray, labels: np.ndarray,
               window: int):
    """Slice (N, F) into (N - window + 1, window, F) sliding windows; align label/weight to center."""
    if window % 2 == 0:
        window += 1
    half = window // 2
    n = len(features)
    if n <= window:
        return None, None, None
    X = np.lib.stride_tricks.sliding_window_view(features, window_shape=window, axis=0)
    # X has shape (n - window + 1, F, window) -> transpose to (N, window, F)
    X = X.transpose(0, 2, 1).copy()
    y = labels[half: n - half]
    w = weights[half: n - half]
    return X, y, w


class _PairDataset:
    def __init__(self, X, y, w):
        self.X = X.astype(np.float32)
        self.y = y.astype(np.int64)
        self.w = w.astype(np.float32)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, i):
        return self.X[i], self.y[i], self.w[i]


def _device():
    if not _HAS_TORCH:
        raise RuntimeError("torch missing")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def train(
    pairs: list[tuple[Path, Path]],
    out_path: Path,
    cfg: dict,
    *,
    epochs: int = 20,
    batch_size: int = 256,
    lr: float = 3e-3,
    val_frac: float = 0.15,
    seed: int = 0,
) -> dict:
    if not _HAS_TORCH:
        raise RuntimeError("torch is required: pip install -e '.[ml]'")
    rng = np.random.default_rng(seed)
    window = cfg["chew_head"]["feature_window_frames"]

    # Stack all pairs
    Xs, ys, ws = [], [], []
    for fp, lp in pairs:
        feats = pd.read_parquet(fp)
        labs = pd.read_parquet(lp)
        # align on frame_idx
        merged = feats.merge(labs[["frame_idx", "label_id", "weight"]], on="frame_idx", how="inner")
        feat_arr = merged[FEATURE_COLS].to_numpy(dtype=np.float32)
        lab_arr = merged["label_id"].to_numpy(dtype=np.int64)
        w_arr = merged["weight"].to_numpy(dtype=np.float32)
        X, y, w = _windowize(feat_arr, w_arr, lab_arr, window)
        if X is None:
            continue
        Xs.append(X); ys.append(y); ws.append(w)
    if not Xs:
        raise RuntimeError("no usable training data after windowization")
    X = np.concatenate(Xs); y = np.concatenate(ys); w = np.concatenate(ws)

    # Normalize features (mean/std) and stash for inference
    mean = X.reshape(-1, X.shape[-1]).mean(axis=0)
    std = X.reshape(-1, X.shape[-1]).std(axis=0) + 1e-6
    X = (X - mean) / std

    n = len(X)
    idx = rng.permutation(n)
    n_val = max(1, int(val_frac * n))
    val_idx, train_idx = idx[:n_val], idx[n_val:]

    device = _device()
    model_cfg = ChewHeadConfig(in_dim=len(FEATURE_COLS))
    model = build_model(model_cfg).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)

    def _batches(idx_arr, bs):
        for i in range(0, len(idx_arr), bs):
            sel = idx_arr[i:i + bs]
            yield (torch.from_numpy(X[sel]).to(device),
                   torch.from_numpy(y[sel]).to(device),
                   torch.from_numpy(w[sel]).to(device))

    history = []
    for ep in range(epochs):
        model.train()
        rng.shuffle(train_idx)
        tr_loss = 0.0; tr_n = 0
        for xb, yb, wb in _batches(train_idx, batch_size):
            opt.zero_grad()
            logits = model(xb)
            loss = torch.nn.functional.cross_entropy(logits, yb, reduction="none")
            loss = (loss * wb).sum() / (wb.sum() + 1e-6)
            loss.backward()
            opt.step()
            tr_loss += float(loss) * len(xb); tr_n += len(xb)

        model.eval()
        with torch.no_grad():
            v_loss = 0.0; v_n = 0; correct = 0; total = 0
            for xb, yb, wb in _batches(val_idx, batch_size):
                logits = model(xb)
                loss = torch.nn.functional.cross_entropy(logits, yb)
                pred = logits.argmax(dim=-1)
                correct += int((pred == yb).sum()); total += len(yb)
                v_loss += float(loss) * len(xb); v_n += len(xb)
        history.append({
            "epoch": ep,
            "train_loss": tr_loss / max(1, tr_n),
            "val_loss": v_loss / max(1, v_n),
            "val_acc": correct / max(1, total),
        })
        print(f"epoch {ep:02d}  train={history[-1]['train_loss']:.4f}  "
              f"val={history[-1]['val_loss']:.4f}  acc={history[-1]['val_acc']:.3f}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": model.state_dict(),
        "model_cfg": asdict(model_cfg),
        "feature_cols": list(FEATURE_COLS),
        "mean": mean.tolist(),
        "std": std.tolist(),
        "window": window,
        "trained_at": time.time(),
    }, out_path)

    summary = {
        "out_path": str(out_path),
        "n_train": int(len(train_idx)),
        "n_val": int(len(val_idx)),
        "history": history,
    }
    (out_path.with_suffix(".json")).write_text(json.dumps(summary, indent=2))
    return summary
