#!/usr/bin/env python3
"""Stage exact-stem legacy video/motion pairs for explicit v2 migration.

The command never deletes or relabels source media. It uses hard links when
possible (falling back to copies) and writes an unverified recovery receipt.
Run ``chewsense-vision migrate-session`` on each staged directory afterward;
that migration deliberately masks all legacy event/phase/food-entry truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi"}


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover(source: Path) -> list[tuple[str, Path, Path]]:
    videos: dict[str, list[Path]] = {}
    motions: dict[str, list[Path]] = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            videos.setdefault(path.stem, []).append(path)
        elif path.suffix.lower() == ".csv":
            motions.setdefault(path.stem, []).append(path)
    pairs: list[tuple[str, Path, Path]] = []
    for stem in sorted(videos.keys() & motions.keys()):
        if len(videos[stem]) == 1 and len(motions[stem]) == 1:
            pairs.append((stem, videos[stem][0], motions[stem][0]))
    return pairs


def link_or_copy(source: Path, destination: Path) -> str:
    if destination.exists():
        if checksum(source) != checksum(destination):
            raise FileExistsError(f"different content already exists: {destination}")
        return "existing"
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def stage_pair(stem: str, video: Path, motion: Path, destination: Path) -> Path:
    session = destination / stem
    session.mkdir(parents=True, exist_ok=True)
    video_method = link_or_copy(video, session / video.name)
    motion_method = link_or_copy(motion, session / motion.name)
    receipt = {
        "schema_version": "1.0.0",
        "status": "unverified_recovery_staging",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_id": stem,
        "requires_explicit_v2_migration": True,
        "requires_fresh_coarse_and_event_review": True,
        "legacy_event_phase_food_entry_eligible": False,
        "files": [
            {
                "kind": "video",
                "source": str(video.resolve()),
                "path": video.name,
                "bytes": video.stat().st_size,
                "sha256": checksum(video),
                "staging_method": video_method,
            },
            {
                "kind": "motion",
                "source": str(motion.resolve()),
                "path": motion.name,
                "bytes": motion.stat().st_size,
                "sha256": checksum(motion),
                "staging_method": motion_method,
            },
        ],
    }
    (session / "legacy-import.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    )
    return session


def parse_args() -> argparse.Namespace:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--destination",
        type=Path,
        default=repository / "workspace/sessions/recovery-staging",
    )
    parser.add_argument("--apply", action="store_true", help="Create staged sessions.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pairs = discover(args.source)
    if not pairs:
        print("No unambiguous exact-stem video/motion pairs found.")
        return 0
    for stem, video, motion in pairs:
        if args.apply:
            session = stage_pair(stem, video, motion, args.destination)
            print(f"staged {stem}: {session}")
        else:
            print(f"would stage {stem}: {video} + {motion}")
    if not args.apply:
        print("Dry run only; pass --apply to create recovery staging.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
