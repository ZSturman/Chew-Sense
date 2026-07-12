#!/usr/bin/env python3
"""Move legacy local artifacts into the role-based ChewSense workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(
    os.environ.get("CHEWSENSE_WORKSPACE", REPO_ROOT / "workspace")
).expanduser().resolve()

MAPPINGS = (
    ("pipelines/motion/data/sample", "fixtures/sessions"),
    ("pipelines/motion/data/raw_sessions", "workspace/sessions/raw"),
    ("pipelines/motion/data/user/field_tests", "workspace/sessions/field-tests"),
    (
        "pipelines/motion/data/curated/accepted_sessions",
        "workspace/sessions/curated",
    ),
    (
        "pipelines/motion/data/curated/rejected_sessions",
        "workspace/sessions/rejected",
    ),
    (
        "pipelines/motion/data/derived/video_labels",
        "workspace/derived/vision",
    ),
    ("pipelines/motion/data/derived", "workspace/derived/motion"),
    ("pipelines/motion/data/manifests", "workspace/manifests"),
    ("pipelines/motion/model_registry/runs", "workspace/models/registry"),
    ("pipelines/motion/models", "workspace/models/exports/checkpoints"),
    ("pipelines/motion/exports", "workspace/models/exports"),
    ("tools/evaluation/reports", "workspace/reports/evaluation"),
    ("pipelines/vision/out", "workspace/derived/vision/legacy-runs"),
    ("pipelines/vision/data", "workspace/archive-assets/vision-inputs"),
    ("pipelines/vision/logs", "workspace/reports/vision-logs"),
    ("pipelines/motion/logs", "workspace/reports/motion-logs"),
    ("pipelines/motion/wandb", "workspace/reports/wandb"),
    ("pipelines/motion/data", "workspace/archive-assets/legacy-motion-data"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_inventory(root: Path) -> dict[str, tuple[int, str]]:
    if not root.exists():
        return {}
    if root.is_file():
        return {root.name: (root.stat().st_size, sha256(root))}
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, sha256(path))
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def resolve_destination(relative: str) -> Path:
    if relative.startswith("workspace/"):
        return WORKSPACE_ROOT / relative.removeprefix("workspace/")
    return REPO_ROOT / relative


def merge_move(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        target = destination / source.name
        if target.exists():
            raise FileExistsError(target)
        shutil.move(str(source), str(target))
        return

    for child in list(source.iterdir()):
        target = destination / child.name
        if target.exists():
            if child.is_dir() and target.is_dir():
                merge_move(child, target)
                if child.exists() and not any(child.iterdir()):
                    child.rmdir()
                continue
            if child.is_file() and target.is_file() and sha256(child) == sha256(target):
                child.unlink()
                continue
            raise FileExistsError(target)
        shutil.move(str(child), str(target))
    if source.exists() and not any(source.iterdir()):
        source.rmdir()


def repair_legacy_links() -> list[dict[str, str]]:
    old_prefix = str(
        REPO_ROOT / "02-DataPipeline" / "data" / "user" / "field_tests"
    )
    new_prefix = str(WORKSPACE_ROOT / "sessions" / "field-tests")
    repairs = []
    for link in (WORKSPACE_ROOT / "sessions" / "raw").rglob("*"):
        if not link.is_symlink():
            continue
        target = os.readlink(link)
        if not target.startswith(old_prefix):
            continue
        new_target = Path(target.replace(old_prefix, new_prefix, 1))
        relative_target = os.path.relpath(new_target, link.parent)
        link.unlink()
        link.symlink_to(relative_target)
        repairs.append({"link": str(link), "target": relative_target})
    return repairs


def migrate() -> Path:
    records = []
    for source_rel, destination_rel in MAPPINGS:
        source = REPO_ROOT / source_rel
        destination = resolve_destination(destination_rel)
        before = file_inventory(source)
        if not before:
            continue
        merge_move(source, destination)
        after = file_inventory(destination)
        missing = {
            relative: expected
            for relative, expected in before.items()
            if after.get(relative) != expected
        }
        if missing:
            raise RuntimeError(
                f"checksum verification failed for {source_rel}: {len(missing)} files"
            )
        records.append(
            {
                "source": source_rel,
                "destination": str(destination),
                "file_count": len(before),
                "total_bytes": sum(size for size, _ in before.values()),
                "files": [
                    {"path": path, "size": size, "sha256": digest}
                    for path, (size, digest) in before.items()
                ],
            }
        )

    manifest = {
        "schema_version": "1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": str(REPO_ROOT),
        "workspace": str(WORKSPACE_ROOT),
        "migrations": records,
        "repaired_symlinks": repair_legacy_links(),
    }
    output = WORKSPACE_ROOT / "manifests" / "reorg-2026-06-15.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print-workspace",
        action="store_true",
        help="Print the resolved workspace without moving files.",
    )
    args = parser.parse_args()
    if args.print_workspace:
        print(WORKSPACE_ROOT)
        return 0
    print(migrate())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
