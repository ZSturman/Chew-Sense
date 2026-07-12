"""Repository and local workspace path resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path

    @classmethod
    def current(cls) -> "WorkspacePaths":
        return cls(workspace_root())

    @property
    def raw_sessions(self) -> Path:
        return self.root / "sessions" / "raw"

    @property
    def field_tests(self) -> Path:
        return self.root / "sessions" / "field-tests"

    @property
    def curated_sessions(self) -> Path:
        return self.root / "sessions" / "curated"

    @property
    def rejected_sessions(self) -> Path:
        return self.root / "sessions" / "rejected"

    @property
    def motion_derived(self) -> Path:
        return self.root / "derived" / "motion"

    @property
    def vision_derived(self) -> Path:
        return self.root / "derived" / "vision"

    @property
    def manifests(self) -> Path:
        return self.root / "manifests"

    @property
    def registry_runs(self) -> Path:
        return self.root / "models" / "registry"

    @property
    def model_exports(self) -> Path:
        return self.root / "models" / "exports"

    @property
    def reports(self) -> Path:
        return self.root / "reports"
