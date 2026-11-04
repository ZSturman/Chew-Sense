"""Managed loop workspace layout and small persistent records.

Everything lives under ``$CHEWSENSE_WORKSPACE/loop`` (ignored by Git). The
``assisted`` and ``blind`` folders are ordinary schema-v2 workspaces, exactly
as Phase 0 prepared them by hand, so Vision and Review need no new layout.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..paths import workspace_root
from .guided import write_json_atomic


@dataclass(frozen=True)
class LoopPaths:
    root: Path

    @classmethod
    def default(cls) -> "LoopPaths":
        return cls(workspace_root() / "loop")

    def __post_init__(self):
        object.__setattr__(self, "root", Path(self.root).resolve())

    service = property(lambda s: s.root / "service")
    experiments = property(lambda s: s.root / "experiments")
    catalog = property(lambda s: s.root / "experiments" / "catalog.json")
    uploads = property(lambda s: s.root / "uploads")
    archives = property(lambda s: s.root / "archives")
    sources = property(lambda s: s.root / "sources")
    quarantine = property(lambda s: s.root / "quarantine")
    assisted = property(lambda s: s.root / "assisted")
    blind = property(lambda s: s.root / "blind")
    jobs = property(lambda s: s.root / "jobs")
    session_state = property(lambda s: s.root / "state" / "sessions")
    reports = property(lambda s: s.root / "reports")
    status = property(lambda s: s.root / "status.json")
    review_tasks = property(lambda s: s.root / "review_tasks.json")
    control = property(lambda s: s.root / "state" / "control.json")

    def ensure(self) -> "LoopPaths":
        for folder in (
            self.service, self.experiments, self.uploads, self.archives, self.sources,
            self.quarantine, self.assisted / "sessions/raw", self.assisted / "derived/vision",
            self.blind / "sessions/raw", self.blind / "derived/vision", self.jobs,
            self.session_state, self.reports,
        ):
            folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.service, 0o700)
        return self

    def source(self, session_id: str) -> Path:
        return self.sources / _safe_id(session_id)

    def session_record(self, session_id: str) -> Path:
        return self.session_state / f"{_safe_id(session_id)}.json"

    def session_reports(self, session_id: str) -> Path:
        return self.reports / "sessions" / _safe_id(session_id)


def _safe_id(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\0" in value:
        raise ValueError(f"unsafe identifier: {value!r}")
    if len(value) > 128:
        raise ValueError("identifier is too long")
    return value


_lock = threading.RLock()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def update_record(path: Path, **changes: Any) -> dict[str, Any]:
    """Merge top-level fields into a small mutable JSON record atomically."""
    with _lock:
        record = read_json(path, {}) or {}
        record.update(changes)
        write_json_atomic(path, record)
        return record


def write_once(path: Path, value: Any) -> Path:
    """Create an immutable report; refuse replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    return path


def next_numbered(folder: Path, stem: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    numbers = [
        int(p.stem.rsplit("-v", 1)[1])
        for p in folder.glob(f"{stem}-v[0-9][0-9][0-9][0-9].json")
    ]
    return folder / f"{stem}-v{(max(numbers) + 1) if numbers else 1:04d}.json"
