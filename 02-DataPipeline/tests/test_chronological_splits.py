from __future__ import annotations

from pathlib import Path

from pipeline.ingest import SessionInfo
from pipeline.splits import create_or_update_split_manifest


def _session(root: Path, session_id: str) -> SessionInfo:
    path = root / session_id
    path.mkdir()
    csv_path = path / f"{session_id}.csv"
    csv_path.write_text("timestamp,ax,ay,az,label\n")
    return SessionInfo(
        path=path,
        csv_path=csv_path,
        label="eating",
        rows=100,
        duration_sec=10,
        sampling_rate_hz=100,
        hash=session_id,
        verified=True,
    )


def test_initial_locked_splits_use_newest_sessions_as_holdout(tmp_path: Path):
    sessions = [
        _session(tmp_path, "Eating-20260101-120000"),
        _session(tmp_path, "Eating-20260201-120000"),
        _session(tmp_path, "Eating-20260301-120000"),
        _session(tmp_path, "Eating-20260401-120000"),
        _session(tmp_path, "Eating-20260501-120000"),
    ]

    manifest = create_or_update_split_manifest(
        sessions,
        tmp_path / "splits.json",
        validation_ratio=0.2,
        test_ratio=0.2,
        require_verified_for_locked=True,
    )

    assert manifest["splits"]["test_locked"] == ["Eating-20260501-120000"]
    assert manifest["splits"]["validation_locked"] == ["Eating-20260401-120000"]
    assert manifest["policy"]["ordering"] == "chronological"
