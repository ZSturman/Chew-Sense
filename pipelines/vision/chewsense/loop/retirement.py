"""Recording-level retirement.

A retired recording leaves the blind/assisted review queues, loop evaluation,
training datasets and the AirPods eating experiment. Nothing is deleted or
rewritten: sources, archives, working copies, drafts, Vision outputs, the
session record and the locked split keep their bytes. Retirement is one
write-once record per recording, ``state/retired/<session_id>.json``, holding
the reason, a research category and a hash inventory of every preserved file,
so the recording stays usable as experimental evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..canonical import utc_now
from .store import LoopPaths, _safe_id, read_json, write_once

SCHEMA_VERSION = 1

#: Research categories a retired recording can be preserved under.
CATEGORIES = {
    "mimed_chewing_no_food": "Mimed chewing / chewing-like movement without food",
    "protocol_mismatch": "Recording does not match its labelled activity",
    "capture_defect": "Capture defect (video, motion or timing unusable)",
    "reviewer_excluded": "Left out by the reviewer",
}

#: Everything a retirement removes the recording from (reported verbatim).
EXCLUDED_FROM = [
    "blind review queue",
    "assisted review queue",
    "loop evaluation and training datasets",
    "experiment progress counts",
    "AirPods eating experiment (development and locked test)",
    "shared project status (Collect, Review, Live)",
]


def retired_dir(root: Path) -> Path:
    """``<loop>/state/retired``; ``root`` is the loop folder."""
    return Path(root) / "state" / "retired"


def retirement_path(root: Path, session_id: str) -> Path:
    return retired_dir(root) / f"{_safe_id(session_id)}.json"


def load_retirements(root: Path | None) -> dict[str, dict[str, Any]]:
    """All retirement records under a loop folder, keyed by session ID."""
    if root is None:
        return {}
    folder = retired_dir(root)
    if not folder.is_dir():
        return {}
    records = {}
    for path in sorted(folder.glob("*.json")):
        record = read_json(path, {}) or {}
        if record.get("session_id"):
            records[str(record["session_id"])] = record
    return records


def retired_ids(root: Path | None) -> set[str]:
    return set(load_retirements(root))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def preserved_locations(paths: LoopPaths, session_id: str) -> list[Path]:
    """Every loop folder that holds this recording or something derived from it."""
    candidates = [
        paths.source(session_id), paths.archives / session_id,
        paths.assisted / "sessions/raw" / session_id, paths.assisted / "derived/vision" / session_id,
        paths.assisted / "derived/assist" / session_id,
        paths.blind / "sessions/raw" / session_id, paths.blind / "derived/vision" / session_id,
        paths.session_reports(session_id),
    ]
    return [p for p in candidates if p.exists()]


def inventory(paths: LoopPaths, session_id: str) -> dict[str, str]:
    """SHA-256 of every preserved file, relative to the loop folder."""
    files: dict[str, str] = {}
    for location in preserved_locations(paths, session_id) + [paths.session_record(session_id)]:
        items = [location] if location.is_file() else sorted(p for p in location.rglob("*") if p.is_file())
        for item in items:
            if item.exists():
                files[str(item.relative_to(paths.root))] = _sha256(item)
    return files


def retire_recording(paths: LoopPaths, session_id: str, *, category: str, reason: str,
                     evidence: list[str] | None = None, by: str = "cli",
                     split_root: Path | None = None) -> dict[str, Any]:
    """Write the retirement record. Refuses unknown recordings and a second retirement."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}; choose one of {sorted(CATEGORIES)}")
    if not reason.strip():
        raise ValueError("a retirement needs a reason")
    record = read_json(paths.session_record(session_id), None)
    if not record:
        raise ValueError(f"{session_id} is not a loop recording")
    path = retirement_path(paths.root, session_id)
    if path.exists():
        raise FileExistsError(f"{session_id} is already retired ({path})")
    split = None
    if split_root is not None:
        split = ((read_json(Path(split_root) / "splits.json", {}) or {}).get("assignments") or {}).get(session_id)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "recording_retirement",
        "session_id": session_id,
        "retired_at": utc_now(),
        "retired_by": by,
        "category": category,
        "category_title": CATEGORIES[category],
        "reason": reason.strip(),
        "evidence": list(evidence or []),
        "excluded_from": EXCLUDED_FROM,
        "role_at_retirement": record.get("role"),
        "activity_at_retirement": record.get("activity"),
        "locked_split_at_retirement": split,
        "data_policy": "Nothing deleted or modified. The locked split keeps its assignment; "
                       "historical results are unchanged. Experiments list the recording as rejected.",
        "preserved_files_sha256": inventory(paths, session_id),
    }
    write_once(path, payload)
    return payload


def verify_preserved(paths: LoopPaths, session_id: str) -> list[str]:
    """Files whose bytes changed or disappeared since retirement (empty when intact)."""
    record = read_json(retirement_path(paths.root, session_id), None)
    if not record:
        raise ValueError(f"{session_id} is not retired")
    changed = []
    for relative, digest in record["preserved_files_sha256"].items():
        target = paths.root / relative
        if relative.startswith("state/sessions/"):
            continue  # the session record is mutable service state (e.g. attention notes)
        if not target.exists() or _sha256(target) != digest:
            changed.append(relative)
    return changed
