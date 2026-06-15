#!/usr/bin/env python3
"""Batch-run the ChewSense video labeller over every raw session.

Iterates `02-DataPipeline/data/raw_sessions/*/` and, for any session bundle
that contains a paired `*.mov`, invokes the `chewsense` pipeline and writes
the auto outputs to `02-DataPipeline/data/derived/video_labels/<session_id>/`.

This is the Phase 1 driver. The Phase 2 motion-alignment bridge will be
invoked from here in addition to `chewsense run` once it lands.

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

from chewsense.config import load_config
from chewsense.integrations.export_to_pipeline import export_session, load_events_json
from chewsense.pipeline import run as run_pipeline
from chewsense.session_bundle import (
    load_coarse_intervals,
    migrate_legacy_session,
    save_auto_predictions,
)

ROOT = Path(__file__).resolve().parents[1]
RAW_SESSIONS = ROOT / "02-DataPipeline" / "data" / "raw_sessions"
DERIVED_VIDEO_LABELS = ROOT / "02-DataPipeline" / "data" / "derived" / "video_labels"

# Files written by `chewsense.pipeline.run` whose presence marks a session as
# already-labeled. We check `events.json` because it's the canonical artifact.
DONE_MARKER = "events.json"


def find_session_video(session_dir: Path) -> Path | None:
    """Return the first `*.mov` (or `*.mp4`) inside a raw session bundle."""
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
    parser.add_argument("--user", type=str, default=None,
                        help="User id for per-user baselines (default: from config).")
    args = parser.parse_args()

    cfg = load_config(args.config)
    sessions = discover_sessions(args.only)
    if args.limit is not None:
        sessions = sessions[: args.limit]

    DERIVED_VIDEO_LABELS.mkdir(parents=True, exist_ok=True)

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

        out_dir = DERIVED_VIDEO_LABELS / session_id
        already_labeled = (out_dir / DONE_MARKER).exists()
        already_aligned = (out_dir / "motion_aligned_labels.csv").exists()
        if not args.force and already_labeled and already_aligned:
            skipped_done += 1
            print(f"[skip] {session_id}: already labeled + aligned (use --force to redo)")
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            manifest = migrate_legacy_session(
                session_dir,
                source_app="collect",
            )
            coarse_intervals = load_coarse_intervals(session_dir)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[fail-bundle] {session_id}: {exc!r}")
            continue
        if args.force or not already_labeled:
            print(f"[run]  {session_id}: {video.name} -> {out_dir.relative_to(ROOT)}")
            t0 = time.time()
            try:
                events = run_pipeline(
                    video,
                    out_dir,
                    cfg,
                    user_id=args.user,
                    coarse_intervals=coarse_intervals,
                )
                save_auto_predictions(session_dir, events)
            except Exception as exc:  # noqa: BLE001 — surface and continue
                failed += 1
                print(f"[fail] {session_id}: {exc!r}")
                continue
            dt = time.time() - t0
            n_chews = sum(e.chew_count for e in events.episodes)
            n_bites = sum(e.bite_count for e in events.episodes)
            print(
                f"[ok]   {session_id}: {len(events.episodes)} episodes / "
                f"{n_bites} bites / {n_chews} chews in {dt:.1f}s"
            )
        else:
            print(f"[reuse] {session_id}: reusing existing events.json for alignment")
            try:
                events = load_events_json(out_dir / DONE_MARKER)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"[fail-load] {session_id}: {exc!r}")
                continue

        # Phase 2 — motion-alignment bridge.
        motion_csv = find_session_motion_csv(session_dir)
        if motion_csv is None:
            print(f"[warn] {session_id}: no motion CSV found; skipping alignment")
        else:
            try:
                summary = export_session(
                    motion_csv,
                    events,
                    out_dir,
                    session_manifest_path=session_dir / "session.json",
                )
                print(
                    f"[align] {session_id}: {summary['n_motion_rows']} rows, "
                    f"eating={summary['n_eating_rows']}, "
                    f"chew_rows={summary['n_chew_rows']}, "
                    f"offset={summary['time_offset']:.3f}"
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"[fail-align] {session_id}: {exc!r}")
                continue

        processed += 1

    total = time.time() - t_start
    print(
        f"\nsummary: processed={processed} skipped_done={skipped_done} "
        f"skipped_no_video={skipped_no_video} failed={failed} "
        f"({total:.1f}s total)"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
