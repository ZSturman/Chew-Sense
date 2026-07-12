"""Repository and local workspace path resolution."""

from __future__ import annotations

import os
from pathlib import Path


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def workspace_root() -> Path:
    configured = os.environ.get("CHEWSENSE_WORKSPACE")
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            raise ValueError("CHEWSENSE_WORKSPACE must be an absolute path")
        return root.resolve()
    return repository_root() / "workspace"


def raw_sessions_root() -> Path:
    return workspace_root() / "sessions" / "raw"


def derived_vision_root() -> Path:
    return workspace_root() / "derived" / "vision"
