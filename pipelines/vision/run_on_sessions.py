#!/usr/bin/env python3
"""Batch-run the ChewSense vision pipeline over every raw session.

Iterates ``$CHEWSENSE_WORKSPACE/sessions/raw/*/`` and writes automatic
outputs to ``$CHEWSENSE_WORKSPACE/derived/vision/<session_id>/``.

This command writes detector telemetry and one immutable candidate revision.
It never writes motion truth: ``finalize-session`` materializes targets only
after a complete review of that exact candidate hash.

Usage
-----
    python run_on_sessions.py                        # all sessions
    python run_on_sessions.py --limit 1              # smoke test
    python run_on_sessions.py --only <session_id>    # one session by name
    python run_on_sessions.py --force                # re-run even if done
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from chewsense.loop.process_lifetime import watch_service_parent

from chewsense.config import load_config
from chewsense.integrations.export_to_pipeline import load_events_json
from chewsense.pipeline import run as run_pipeline
from chewsense.paths import derived_vision_root, raw_sessions_root
from chewsense.session_bundle import (
    create_candidates_from_events,
    load_coarse_intervals,
    load_session_manifest,
    LegacySchemaError,
    migrate_legacy_session,
    save_auto_predictions,
)

ROOT = Path(__file__).resolve().parents[2]
RAW_SESSIONS = raw_sessions_root()
DERIVED_VISION_OUTPUTS = derived_vision_root()

# ``events.json`` is automatic detector telemetry. A completed vision pass also
# requires at least one immutable candidate revision.
DONE_MARKER = "events.json"


def find_session_video(session_dir: Path) -> Path | None:
    """Return the first `*.mov` (or `*.mp4`) inside a raw session bundle."""
    try:
        manifest = load_session_manifest(session_dir)
    except (FileNotFoundError, LegacySchemaError):
        manifest = None
    if manifest is not None:
        path = session_dir / manifest.video.path if manifest.video else None
        return path if path is not None and path.is_file() else None
    for ext in ("*.mov", "*.MOV", "*.mp4", "*.MP4"):
        videos = sorted(session_dir.glob(ext))
        if videos:
            return videos[0]
    return None


def find_session_motion_csv(session_dir: Path) -> Path | None:
    """Return the motion CSV inside a raw session bundle, if any.

    The data-collection app writes one CSV per session whose basename
    matches the bundle. We prefer that, but fall back to any single
    ``*.csv`` (excluding obvious metadata files).
    """
    preferred = session_dir / f"{session_dir.name}.csv"
    if preferred.exists():
        return preferred
    csvs = [p for p in sorted(session_dir.glob("*.csv")) if not p.name.startswith("_")]
    if len(csvs) == 1:
        return csvs[0]
    return None


def discover_sessions(only: str | None) -> list[Path]:
    if not RAW_SESSIONS.exists():
        print(f"error: raw sessions dir not found: {RAW_SESSIONS}", file=sys.stderr)
        sys.exit(2)
    sessions = sorted(p for p in RAW_SESSIONS.iterdir() if p.is_dir())
    if only is not None:
        sessions = [p for p in sessions if p.name == only]
        if not sessions:
            print(f"error: no session matched --only {only!r}", file=sys.stderr)
            sys.exit(2)
    return sessions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most this many sessions.")
    parser.add_argument("--only", type=str, default=None,
                        help="Process only the session whose folder name matches.")
    parser.add_argument("--force", action="store_true",
                        help="Re-run even if outputs already exist.")
    parser.add_argument("--config", type=Path, default=None,
                        help="Optional chewsense config YAML to override defaults.")
    args = parser.parse_args()

    cfg = load_config(args.config)
    sessions = discover_sessions(args.only)
    if args.limit is not None:
        sessions = sessions[: args.limit]

    DERIVED_VISION_OUTPUTS.mkdir(parents=True, exist_ok=True)

    processed = 0
    skipped_no_video = 0
    skipped_done = 0
    failed = 0
    t_start = time.time()

    for session_dir in sessions:
        session_id = session_dir.name
        video = find_session_video(session_dir)
        if video is None:
            skipped_no_video += 1
            print(f"[skip] {session_id}: no .mov/.mp4 in bundle")
            continue

        out_dir = DERIVED_VISION_OUTPUTS / session_id
        already_labeled = (out_dir / DONE_MARKER).exists()
        already_candidateized = any(out_dir.glob("candidates-v*.json"))
        if not args.force and already_labeled and already_candidateized:
            skipped_done += 1
            print(f"[skip] {session_id}: telemetry + candidates exist (use --force to redo)")
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            migrate_legacy_session(
                session_dir,
                source_app="collect",
            )
            manifest = load_session_manifest(session_dir)
            video = session_dir / manifest.video.path if manifest.video else video
            audio_path = session_dir / manifest.audio.path if manifest.audio and manifest.audio.enabled and manifest.audio.path else None
            coarse_intervals = load_coarse_intervals(session_dir)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[fail-bundle] {session_id}: {exc!r}")
            continue
        if args.force or not already_labeled:
            print(f"[run]  {session_id}: {video.name} -> {out_dir}")
            t0 = time.time()
            try:
                events = run_pipeline(
                    video,
                    out_dir,
                    cfg,
                    coarse_intervals=coarse_intervals,
                    audio_path=audio_path,
                )
                save_auto_predictions(session_dir, events)
                candidate_path, candidates = create_candidates_from_events(
                    session_dir,
                    out_dir,
                    events,
                )
            except Exception as exc:  # noqa: BLE001 — surface and continue
                failed += 1
                print(f"[fail] {session_id}: {exc!r}")
                continue
            dt = time.time() - t0
            n_chews = len(events.mouth_cycles)
            n_food_entries = len(events.food_entry_proposals)
            print(
                f"[ok]   {session_id}: {n_food_entries} food-entry proposals / "
                f"{n_chews} chew-cycle proposals in {dt:.1f}s"
            )
            print(
                f"[candidates] {session_id}: {candidate_path.name} "
                f"sha256={candidates.content_sha256[:12]} "
                f"chews={len(candidates.chews)} food_entries={len(candidates.food_entries)}"
            )
        else:
            print(f"[reuse] {session_id}: reusing existing events.json for candidates")
            try:
                events = load_events_json(out_dir / DONE_MARKER)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"[fail-load] {session_id}: {exc!r}")
                continue
            if not already_candidateized:
                try:
                    candidate_path, candidates = create_candidates_from_events(
                        session_dir,
                        out_dir,
                        events,
                    )
                    print(
                        f"[candidates] {session_id}: {candidate_path.name} "
                        f"sha256={candidates.content_sha256[:12]}"
                    )
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"[fail-candidates] {session_id}: {exc!r}")
                    continue

        # Motion labels are intentionally not generated from automatic events.
        # ``finalize-session`` derives them from the exact reviewed candidate
        # revision after lineage/hash validation.

        processed += 1

    total = time.time() - t_start
    print(
        f"\nsummary: processed={processed} skipped_done={skipped_done} "
        f"skipped_no_video={skipped_no_video} failed={failed} "
        f"({total:.1f}s total)"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    watch_service_parent()
    sys.exit(main())
