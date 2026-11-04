"""Exporter: writes the JSON/CSV/parquet/manifest/plots/review_payload bundle."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .events.schema import FrameRecord, VideoEvents
from .features._models import model_provenance


def _git_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parent,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "unknown"


def write_outputs(
    out_dir: Path,
    events: VideoEvents,
    frame_records: list[FrameRecord],
    signals: dict[str, np.ndarray],
    config: dict,
    video_meta: dict,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "signals").mkdir(exist_ok=True)
    (out_dir / "debug").mkdir(exist_ok=True)
    if config["export"].get("write_plots", True):
        (out_dir / "plots").mkdir(exist_ok=True)

    # A rerun must not leave obsolete episode/bite truth-looking files beside
    # the fused telemetry contract.
    for legacy_name in (
        "episodes.csv",
        "chews.csv",
        "bites.csv",
        "motion_aligned_labels.csv",
        "plots/episode_amplitudes.png",
    ):
        (out_dir / legacy_name).unlink(missing_ok=True)

    # events.json
    with open(out_dir / "events.json", "w") as f:
        json.dump(events.model_dump(), f, indent=2)

    # flat events.csv
    rows = []
    for cycle in events.mouth_cycles:
        rows.append({
            "kind": "chew_cycle_proposal",
            "id": cycle.id,
            "start_ts": cycle.start_ts,
            "end_ts": cycle.end_ts,
            "confidence": cycle.confidence,
            "extra": json.dumps({
                "open_peak_ts": cycle.peak_ts,
                "cycle_kind": cycle.kind,
                "peak_magnitude": cycle.peak_magnitude,
                "pose_class": cycle.pose_class,
            }),
        })
    for proposal in events.food_entry_proposals:
        rows.append({
            "kind": "food_entry_proposal",
            "id": proposal.id,
            "start_ts": proposal.food_entry_ts,
            "end_ts": proposal.food_entry_ts,
            "confidence": proposal.confidence,
            "extra": json.dumps(proposal.evidence),
        })
    pd.DataFrame(
        rows,
        columns=["kind", "id", "start_ts", "end_ts", "confidence", "extra"],
    ).to_csv(out_dir / "events.csv", index=False)

    # Phase 1: mouth-state segments + cycles + counts
    pd.DataFrame([{
        "state": seg.state,
        "start_ts": seg.start_ts,
        "end_ts": seg.end_ts,
        "duration_s": seg.duration_s,
        "pose_class": seg.pose_class,
    } for seg in events.mouth_state_segments]).to_csv(out_dir / "mouth_states.csv", index=False)

    cycle_rows = [{
        "id": c.id,
        "kind": c.kind,
        "start_ts": c.start_ts,
        "peak_ts": c.peak_ts,
        "end_ts": c.end_ts,
        "duration_s": c.duration_s,
        "peak_magnitude": c.peak_magnitude,
        "pose_class": c.pose_class,
        "confidence": c.confidence,
    } for c in events.mouth_cycles]
    pd.DataFrame(cycle_rows).to_csv(out_dir / "mouth_cycles.csv", index=False)
    with open(out_dir / "mouth_cycles.json", "w") as f:
        json.dump([c.model_dump() for c in events.mouth_cycles], f, indent=2)
    with open(out_dir / "cycle_counts.json", "w") as f:
        json.dump(events.cycle_counts.model_dump(), f, indent=2)

    food_entry_rows = [proposal.model_dump() for proposal in events.food_entry_proposals]
    pd.DataFrame(
        food_entry_rows,
        columns=["id", "food_entry_ts", "confidence", "evidence"],
    ).to_csv(out_dir / "food_entry_proposals.csv", index=False)
    with open(out_dir / "food_entry_proposals.json", "w") as f:
        json.dump(food_entry_rows, f, indent=2)

    # frame_predictions
    # CSV remains the portable synchronized evidence stream for review.
    # Parquet is also written when enabled for downstream Python tooling.
    fr_df = pd.DataFrame([r.model_dump() for r in frame_records])
    fr_df.to_csv(out_dir / "frame_predictions.csv", index=False)
    if config["export"].get("write_parquet", True):
        try:
            fr_df.to_parquet(out_dir / "frame_predictions.parquet", index=False)
        except Exception:
            pass

    # signals
    for name, arr in signals.items():
        np.save(out_dir / "signals" / f"{name}.npy", arr)

    # plots
    if config["export"].get("write_plots", True):
        try:
            _write_plots(out_dir / "plots", signals, events)
        except Exception as e:
            (out_dir / "plots" / "ERROR.txt").write_text(f"plot error: {e}\n")

    # debug manifest
    manifest = {
        "schema_version": events.schema_version,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "pipeline_source_sha256": hashlib.sha256(b"".join(
            str(p.relative_to(Path(__file__).parent)).encode() + b"\0" + p.read_bytes()
            for p in sorted(Path(__file__).parent.rglob("*.py"))
        )).hexdigest(),
        "python": sys.version,
        "platform": platform.platform(),
        "video_meta": video_meta,
        "config": config,
        "model_assets": model_provenance(),
        "n_frames_recorded": len(frame_records),
        "n_chew_cycle_proposals": len(events.mouth_cycles),
        "n_food_entry_proposals": len(events.food_entry_proposals),
    }
    with open(out_dir / "debug" / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    # review_payload (slim, app-friendly)
    review = {
        "schema_version": events.schema_version,
        "video_id": events.video_id,
        "duration_s": events.duration_s,
        "fps": events.fps,
        "non_eating_mouth_spans": events.model_dump()["non_eating_mouth_spans"],
        "occlusion_spans": events.model_dump()["occlusion_spans"],
        "drinking_spans": events.model_dump()["drinking_spans"],
        "mouth_state_segments": events.model_dump()["mouth_state_segments"],
        "mouth_cycles": events.model_dump()["mouth_cycles"],
        "food_entry_proposals": events.model_dump()["food_entry_proposals"],
        "cycle_counts": events.model_dump()["cycle_counts"],
        "signal_quality": events.signal_quality.model_dump() if events.signal_quality else None,
    }
    with open(out_dir / "review_payload.json", "w") as f:
        json.dump(review, f, indent=2)


def _write_plots(plot_dir: Path, signals: dict, events: VideoEvents) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if "ts" in signals and "jaw_open_smooth" in signals:
        fig, ax = plt.subplots(figsize=(12, 3))
        ax.plot(signals["ts"], signals["jaw_open_smooth"], label="jaw_open_smooth", lw=0.8)
        if "jaw_open_bp" in signals:
            ax.plot(signals["ts"], signals["jaw_open_bp"], label="jaw_open_bp", lw=0.6, alpha=0.7)
        for cycle in events.mouth_cycles:
            ax.axvspan(cycle.start_ts, cycle.end_ts, alpha=0.08, color="green")
            ax.axvline(cycle.peak_ts, color="orange", lw=0.5, alpha=0.5)
        for proposal in events.food_entry_proposals:
            ax.axvline(
                proposal.food_entry_ts,
                color="purple",
                lw=1.0,
                alpha=0.7,
            )
        ax.set_xlabel("time (s)")
        ax.set_ylabel("jaw open (norm)")
        ax.legend(loc="upper right", fontsize=8)
        fig.tight_layout()
        fig.savefig(plot_dir / "jaw_signal.png", dpi=120)
        plt.close(fig)
