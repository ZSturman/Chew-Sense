"""Config loading. Configs are merged: defaults <- user overrides."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "configs" / "default.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    with open(DEFAULT_CONFIG) as f:
        cfg = yaml.safe_load(f)
    if path is not None:
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, user)
    return cfg
