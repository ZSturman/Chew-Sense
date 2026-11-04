"""Loop policy loading: checked-in defaults deep-merged with a workspace override."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..config import _deep_merge

DEFAULT_LOOP_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "loop.yaml"


def load_loop_config(override: Path | None = None) -> dict[str, Any]:
    config = yaml.safe_load(DEFAULT_LOOP_CONFIG.read_text())
    if override is not None and Path(override).exists():
        config = _deep_merge(config, yaml.safe_load(Path(override).read_text()) or {})
    return config
