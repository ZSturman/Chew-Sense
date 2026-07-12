"""Chew-head: tiny 1D dilated CNN that classifies a center frame as chewing/not.

Input  : (B, T, F) feature window (T = feature_window_frames, F = len(FEATURE_COLS))
Output : (B, 2) logits for [not_chewing, chewing]

~30k parameters; trains in seconds on Apple MPS.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    import torch
    from torch import nn
    _HAS_TORCH = True
except Exception:  # torch is an optional dep
    _HAS_TORCH = False
    torch = None
    nn = None  # type: ignore[assignment]


@dataclass
class ChewHeadConfig:
    in_dim: int = 13
    hidden: int = 32
    n_layers: int = 4
    dropout: float = 0.1
    num_classes: int = 2


def build_model(cfg: ChewHeadConfig):
    if not _HAS_TORCH:
        raise RuntimeError("torch is not installed. Install with `pip install -e .[ml]`.")

    class DilatedBlock(nn.Module):
        def __init__(self, ch: int, dilation: int, dropout: float):
            super().__init__()
            self.conv = nn.Conv1d(ch, ch, kernel_size=3, padding=dilation, dilation=dilation)
            self.norm = nn.BatchNorm1d(ch)
            self.act = nn.GELU()
            self.drop = nn.Dropout(dropout)

        def forward(self, x):
            return x + self.drop(self.act(self.norm(self.conv(x))))

    class ChewHead(nn.Module):
        def __init__(self, c: ChewHeadConfig):
            super().__init__()
            self.proj = nn.Conv1d(c.in_dim, c.hidden, kernel_size=1)
            self.blocks = nn.Sequential(*[
                DilatedBlock(c.hidden, dilation=2 ** i, dropout=c.dropout)
                for i in range(c.n_layers)
            ])
            self.head = nn.Linear(c.hidden, c.num_classes)

        def forward(self, x):  # x: (B, T, F)
            x = x.transpose(1, 2)            # (B, F, T)
            x = self.proj(x)
            x = self.blocks(x)
            # Center-frame pooling
            t_center = x.shape[-1] // 2
            return self.head(x[:, :, t_center])

    return ChewHead(cfg)
