from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from chewsense.canonical import file_sha256
from chewsense.session_bundle import (
    audit_session,
    finalize_session,
    load_session_manifest,
    purge_review_audio,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "fixtures" / "golden" / "session-v2"


def _with_review_audio(root: Path) -> tuple[Path, Path, Path]:
    shutil.copytree(GOLDEN, root)
    session = root / "session"
    derived = root / "derived"
    audio = session / "review-audio.m4a"
    audio.write_bytes(b"privacy-safe-review-audio-fixture")
    manifest_path = session / "session.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["audio"] = {
        "enabled": True,
        "permission": "granted",
        "path": audio.name,
        "media_type": "audio_m4a",
        "sha256": file_sha256(audio),
        "purpose": "vad_and_human_review_only",
        "shipping_model_eligible": False,
        "safe_to_delete_after_finalization": True,
    }
    manifest["quality"]["review_audio_present"] = True
    manifest_path.write_text(json.dumps(manifest))
    return session, derived, audio


def test_review_audio_purge_requires_finalization(tmp_path: Path):
    session, derived, audio = _with_review_audio(tmp_path / "not-finalized")

    with pytest.raises(ValueError, match="only after verified finalization"):
        purge_review_audio(session, derived)

    assert audio.exists()


def test_review_audio_purge_updates_manifest_and_preserves_audit(tmp_path: Path):
    session, derived, audio = _with_review_audio(tmp_path / "finalized")
    finalize_session(session, derived)

    deleted = purge_review_audio(session, derived)

    assert deleted == audio.resolve()
    assert not audio.exists()
    manifest = load_session_manifest(session)
    assert manifest.audio is not None
    assert not manifest.audio.enabled
    assert manifest.audio.path is None
    assert manifest.audio.sha256 is None
    assert manifest.audio.purged_at is not None
    assert not manifest.quality.review_audio_present
    report = audit_session(session, derived)
    assert report.ok, report.model_dump()
