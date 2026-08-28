#!/usr/bin/env python3
"""Create a reproducible checksum and deduplication inventory of local data.

The output belongs in the ignored ``workspace/`` tree.  It proves that media
removed from the active Git checkout is still present in local cold storage
without putting participant data or large generated files back in Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


CHUNK_BYTES = 4 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def iter_regular_files(root: Path, excluded: set[Path]) -> Iterable[Path]:
    for directory, names, filenames in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in {".git", ".DS_Store"})
        for filename in sorted(filenames):
            path = Path(directory, filename)
            if filename == ".DS_Store" or path.resolve() in excluded or path.is_symlink():
                continue
            if path.is_file():
                yield path


def build_inventory(repository: Path, roots: list[Path], output: Path) -> dict[str, object]:
    excluded = {output.resolve()}
    files: list[dict[str, object]] = []
    hashes: dict[str, list[str]] = defaultdict(list)

    for root in roots:
        resolved_root = root.resolve()
        if not resolved_root.is_dir():
            continue
        for path in iter_regular_files(resolved_root, excluded):
            size = path.stat().st_size
            checksum = sha256_file(path)
            try:
                display_path = path.relative_to(repository).as_posix()
            except ValueError:
                display_path = path.as_posix()
            files.append({"path": display_path, "bytes": size, "sha256": checksum})
            hashes[checksum].append(display_path)

    files.sort(key=lambda item: str(item["path"]))
    duplicates = [
        {
            "sha256": checksum,
            "bytes_each": next(
                int(item["bytes"]) for item in files if item["sha256"] == checksum
            ),
            "paths": sorted(paths),
        }
        for checksum, paths in sorted(hashes.items())
        if len(paths) > 1
    ]
    total_bytes = sum(int(item["bytes"]) for item in files)
    duplicate_bytes = sum(
        int(group["bytes_each"]) * (len(group["paths"]) - 1) for group in duplicates
    )

    return {
        "schema_version": "1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": repository.as_posix(),
        "roots": [
            root.resolve().relative_to(repository).as_posix()
            if root.resolve().is_relative_to(repository)
            else root.resolve().as_posix()
            for root in roots
        ],
        "summary": {
            "file_count": len(files),
            "total_bytes": total_bytes,
            "unique_content_bytes": total_bytes - duplicate_bytes,
            "duplicate_content_bytes": duplicate_bytes,
            "duplicate_groups": len(duplicates),
        },
        "duplicates": duplicates,
        "files": files,
    }


def write_json_atomic(value: object, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, output)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def parse_args() -> argparse.Namespace:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        dest="roots",
        help="Directory to inventory; repeat for multiple roots.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repository / "workspace/manifests/local-data-inventory-v1.json",
        help="Ignored JSON destination.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repository = Path(__file__).resolve().parents[1]
    roots = args.roots or [repository / "workspace", repository / "archive"]
    inventory = build_inventory(repository, roots, args.output)
    write_json_atomic(inventory, args.output)
    summary = inventory["summary"]
    print(
        f"Inventoried {summary['file_count']} files / {summary['total_bytes']} bytes; "
        f"{summary['duplicate_groups']} duplicate groups."
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
