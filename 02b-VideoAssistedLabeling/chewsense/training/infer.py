"""Apply a trained chew-head to per-frame features.

Returns an array of length n_frames with P(chewing). Frames near the edges
where a full feature window can't be assembled get the closest valid
prediction (clamped).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..models.chew_head import ChewHeadConfig, build_model

try:
    import torch
    _HAS_TORCH = True
except Exception:
    _HAS_TORCH = False


_DEFAULT_CKPT = Path.home() / ".cache" / "chewsense" / "models" / "chew_head.pt"


def default_checkpoint_path() -> Path:
    return _DEFAULT_CKPT


def is_available(cfg: dict) -> bool:
    if not cfg["chew_head"].get("enabled", False):
        return False
    if not _HAS_TORCH:
        return False
    p = cfg["chew_head"].get("model_path") or _DEFAULT_CKPT
    return Path(p).exists()


def predict(features: np.ndarray, cfg: dict) -> np.ndarray:
    """features: (N, F) array, columns must match FEATURE_COLS order."""
    if not _HAS_TORCH:
        raise RuntimeError("torch missing")
    p = cfg["chew_head"].get("model_path") or _DEFAULT_CKPT
    p = Path(p)
    ckpt = torch.load(p, map_location="cpu", weights_only=False)
    model_cfg = ChewHeadConfig(**ckpt["model_cfg"])
    model = build_model(model_cfg)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    mean = np.array(ckpt["mean"], dtype=np.float32)
    std = np.array(ckpt["std"], dtype=np.float32)
    window = int(ckpt["window"])
    half = window // 2

    feats = (features.astype(np.float32) - mean) / std
    n = len(feats)
    if n <= window:
        return np.full(n, 0.5, dtype=np.float32)

    X = np.lib.stride_tricks.sliding_window_view(feats, window_shape=window, axis=0)
    X = X.transpose(0, 2, 1).copy()  # (M, T, F)

    device = torch.device("mps") if torch.backends.mps.is_available() \
        else (torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu"))
    model.to(device)

    probs = np.empty(len(X), dtype=np.float32)
    bs = 1024
    with torch.no_grad():
        for i in range(0, len(X), bs):
            xb = torch.from_numpy(X[i:i + bs]).to(device)
            logits = model(xb)
            probs[i:i + bs] = torch.softmax(logits, dim=-1)[:, 1].cpu().numpy()

    out = np.empty(n, dtype=np.float32)
    out[half: n - half] = probs
    out[:half] = probs[0]
    out[n - half:] = probs[-1]
    return out
