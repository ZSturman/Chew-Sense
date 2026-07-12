from __future__ import annotations

from pathlib import Path

import pytest

from chewsense.paths import repository_root, workspace_root


def test_default_workspace_is_repository_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHEWSENSE_WORKSPACE", raising=False)
    assert workspace_root() == repository_root() / "workspace"


def test_external_workspace_must_be_absolute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CHEWSENSE_WORKSPACE", str(tmp_path))
    assert workspace_root() == tmp_path.resolve()
    monkeypatch.setenv("CHEWSENSE_WORKSPACE", "relative/workspace")
    with pytest.raises(ValueError, match="absolute"):
        workspace_root()
