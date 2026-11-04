"""Verify an uploaded Collect archive and import it exactly once.

The ZIP is retained unchanged. Its extracted session becomes an immutable
source (files made read-only). Re-sending the same recording is reported as a
duplicate; the same session ID with different media is quarantined and never
overwrites anything.
"""
from __future__ import annotations

import hashlib
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..canonical import file_sha256, utc_now
from ..session_bundle import load_session_manifest
from .guided import write_json_atomic
from .store import LoopPaths, read_json

MAX_ARCHIVE_BYTES = 4 * 1024**3
MAX_MEMBERS = 2000
MAX_EXPANSION_RATIO = 50


class ArchiveError(ValueError):
    """The archive is unusable; nothing was imported."""


@dataclass
class ImportResult:
    session_id: str
    state: str  # imported | duplicate | conflict
    source_dir: Path | None
    archive_path: Path | None
    message: str


def sha256_file(path: Path) -> str:
    return file_sha256(path)


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise ArchiveError("archive has too many entries")
    total = 0
    for info in members:
        name = PurePosixPath(info.filename)
        if name.is_absolute() or ".." in name.parts or "\\" in info.filename:
            raise ArchiveError(f"unsafe path in archive: {info.filename!r}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise ArchiveError(f"symbolic links are not allowed: {info.filename!r}")
        if info.flag_bits & 0x1:
            raise ArchiveError("encrypted archives are not supported")
        total += info.file_size
    compressed = sum(i.compress_size for i in members) or 1
    if total > MAX_ARCHIVE_BYTES or total / compressed > MAX_EXPANSION_RATIO:
        raise ArchiveError("archive expands beyond the allowed size")
    return members


def _session_root(extracted: Path) -> Path:
    """Collect zips the session folder itself, so session.json is one level down."""
    candidates = [p.parent for p in extracted.rglob("session.json") if "__MACOSX" not in p.parts]
    if len(candidates) != 1:
        raise ArchiveError("archive must contain exactly one session.json")
    root = candidates[0]
    if root != extracted and root.parent != extracted:
        raise ArchiveError("session.json must be at the archive root or in one top folder")
    return root


def _media_hashes(session_dir: Path) -> dict[str, str]:
    manifest = load_session_manifest(session_dir)
    hashes = {}
    references = [manifest.video, manifest.motion]
    if manifest.audio and manifest.audio.enabled and manifest.audio.path:
        references.append(manifest.audio)  # type: ignore[arg-type]
    for reference in references:
        if reference is None:
            continue
        relative = PurePosixPath(reference.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ArchiveError(f"media reference escapes the session: {reference.path}")
        path = session_dir / reference.path
        if not path.is_file() or path.is_symlink():
            raise ArchiveError(f"missing media file: {reference.path}")
        actual = file_sha256(path)
        if reference.sha256 and reference.sha256 != actual:
            raise ArchiveError(f"media hash mismatch: {reference.path}")
        hashes[reference.path] = actual
    if manifest.video is None:
        raise ArchiveError("guided collection sessions require video")
    return hashes


def _tree_hashes(session_dir: Path) -> dict[str, str]:
    return {
        str(p.relative_to(session_dir)): file_sha256(p)
        for p in sorted(session_dir.rglob("*"))
        if p.is_file() and not p.name.startswith(".")
    }


def _make_read_only(folder: Path) -> None:
    for path in folder.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    for path in sorted((p for p in folder.rglob("*") if p.is_dir()), reverse=True):
        path.chmod(0o555)
    folder.chmod(0o555)


def import_archive(
    archive_path: Path,
    paths: LoopPaths,
    *,
    expected_sha256: str | None = None,
    received_from: str | None = None,
) -> ImportResult:
    """Validate and import one archive. Safe to call again for the same archive."""
    archive_path = Path(archive_path)
    archive_sha = sha256_file(archive_path)
    if expected_sha256 and archive_sha != expected_sha256:
        raise ArchiveError("archive checksum does not match the announced checksum")
    if not zipfile.is_zipfile(archive_path):
        raise ArchiveError("upload is not a ZIP archive")

    with tempfile.TemporaryDirectory(dir=paths.uploads) as temporary:
        extracted = Path(temporary) / "x"
        extracted.mkdir()
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = _safe_members(archive)
                bad = archive.testzip()
                if bad is not None:
                    raise ArchiveError(f"corrupted archive member: {bad}")
                for info in members:
                    archive.extract(info, extracted)
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"corrupted archive: {exc}") from exc
        session_dir = _session_root(extracted)
        try:
            manifest = load_session_manifest(session_dir)
        except Exception as exc:  # noqa: BLE001 - surface any manifest failure
            raise ArchiveError(f"invalid session.json: {exc}") from exc
        session_id = manifest.session_id
        if session_dir != extracted and session_dir.name != session_id:
            raise ArchiveError("session folder name does not match session_id")
        media = _media_hashes(session_dir)
        tree = _tree_hashes(session_dir)

        source = paths.source(session_id)
        receipt_path = source.parent / f".{session_id}.receipt.json"
        if source.exists():
            existing = read_json(receipt_path, {}) or {}
            if existing.get("media_sha256") == media:
                if existing.get("tree_sha256") == tree:
                    return ImportResult(session_id, "duplicate", source, None,
                                        "already imported; identical content")
                state, message = "conflict", (
                    "already imported with identical media but different annotations or "
                    "metadata; kept the new archive in quarantine for a decision"
                )
            else:
                state, message = "conflict", (
                    "a different recording already uses this session_id; quarantined"
                )
            quarantined = paths.quarantine / f"{utc_now().replace(':', '')}-{session_id}.zip"
            shutil.copy2(archive_path, quarantined)
            return ImportResult(session_id, state, None, quarantined, message)

        archive_dest = paths.archives / session_id / f"{archive_sha}.zip"
        archive_dest.parent.mkdir(parents=True, exist_ok=True)
        if not archive_dest.exists():
            staged = archive_dest.with_suffix(".part")
            shutil.copy2(archive_path, staged)
            if sha256_file(staged) != archive_sha:
                staged.unlink()
                raise ArchiveError("retained archive copy failed verification")
            staged.rename(archive_dest)
            archive_dest.chmod(0o444)

        staging = paths.sources / f".{session_id}.importing"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(session_dir, staging, symlinks=False)
        if _tree_hashes(staging) != tree:
            shutil.rmtree(staging)
            raise ArchiveError("imported copy failed verification")
        receipt = {
            "session_id": session_id,
            "archive_sha256": archive_sha,
            "archive_path": str(archive_dest),
            "media_sha256": media,
            "tree_sha256": tree,
            "received_from": received_from,
            "imported_at": utc_now(),
        }
        # The receipt describes the source, so it must exist before the source
        # becomes visible. A crash before the rename is retried from scratch.
        write_json_atomic(receipt_path, receipt)
        staging.rename(source)
        _make_read_only(source)
        return ImportResult(session_id, "imported", source, archive_dest, "imported")


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
