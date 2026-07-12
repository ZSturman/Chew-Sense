"""Exporter: writes the JSON/CSV/parquet/manifest/plots/review_payload bundle."""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .events.schema import FrameRecord, VideoEvents


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

    # events.json
    with open(out_dir / "events.json", "w") as f:
        json.dump(events.model_dump(), f, indent=2)

    # flat events.csv
    rows = []
    for ep in events.episodes:
        rows.append({
            "kind": "episode", "id": ep.id, "start_ts": ep.start_ts, "end_ts": ep.end_ts,
            "confidence": ep.confidence, "extra": json.dumps({
                "start_reason": ep.start_reason, "end_reason": ep.end_reason,
                "chew_count": ep.chew_count, "bite_count": ep.bite_count,
            })
        })
        for b in ep.bites:
            rows.append({"kind": "bite", "id": b.id, "start_ts": b.ts, "end_ts": b.ts,
                         "confidence": b.confidence, "extra": json.dumps({"episode": ep.id})})
        for c in ep.chews:
            rows.append({"kind": "chew", "id": c.id, "start_ts": c.start_ts, "end_ts": c.end_ts,
                         "confidence": c.confidence, "extra": json.dumps({
                             "episode": ep.id, "peak_ts": c.peak_ts,
                             "amplitude": c.amplitude, "relative_amplitude": c.relative_amplitude,
                         })})
    pd.DataFrame(rows).to_csv(out_dir / "events.csv", index=False)

    # per-table CSVs
    pd.DataFrame([{
        "id": ep.id, "start_ts": ep.start_ts, "end_ts": ep.end_ts,
        "duration_s": ep.duration_s, "start_reason": ep.start_reason,
        "end_reason": ep.end_reason, "confidence": ep.confidence,
        "chew_count": ep.chew_count, "bite_count": ep.bite_count,
    } for ep in events.episodes]).to_csv(out_dir / "episodes.csv", index=False)

    pd.DataFrame([{
        "id": c.id, "episode": ep.id,
        "start_ts": c.start_ts, "peak_ts": c.peak_ts, "end_ts": c.end_ts,
        "duration_s": c.duration_s,
        "amplitude": c.amplitude, "relative_amplitude": c.relative_amplitude,
        "confidence": c.confidence,
    } for ep in events.episodes for c in ep.chews]).to_csv(out_dir / "chews.csv", index=False)

    pd.DataFrame([{
        "id": b.id, "episode": ep.id, "ts": b.ts, "confidence": b.confidence,
    } for ep in events.episodes for b in ep.bites]).to_csv(out_dir / "bites.csv", index=False)

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

    # frame_predictions
    # Always write CSV (consumed by the Swift CycleReviewer, which has no
    # parquet reader). Parquet is written too when enabled, for downstream
    # Python tooling.
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
        "python": sys.version,
        "platform": platform.platform(),
        "video_meta": video_meta,
        "config": config,
        "n_frames_recorded": len(frame_records),
        "n_episodes": len(events.episodes),
        "n_chews": sum(ep.chew_count for ep in events.episodes),
        "n_bites": sum(ep.bite_count for ep in events.episodes),
    }
    with open(out_dir / "debug" / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    # review_payload (slim, app-friendly)
    review = {
        "schema_version": events.schema_version,
        "video_id": events.video_id,
        "duration_s": events.duration_s,
        "fps": events.fps,
        "episodes": events.model_dump()["episodes"],
        "non_eating_mouth_spans": events.model_dump()["non_eating_mouth_spans"],
        "occlusion_spans": events.model_dump()["occlusion_spans"],
        "drinking_spans": events.model_dump()["drinking_spans"],
        "mouth_state_segments": events.model_dump()["mouth_state_segments"],
        "mouth_cycles": events.model_dump()["mouth_cycles"],
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
        for ep in events.episodes:
            ax.axvspan(ep.start_ts, ep.end_ts, alpha=0.15, color="green")
            for c in ep.chews:
                ax.axvline(c.peak_ts, color="orange", lw=0.5, alpha=0.5)
            for b in ep.bites:
                ax.axvline(b.ts, color="purple", lw=1.0, alpha=0.7)
        ax.set_xlabel("time (s)")
        ax.set_ylabel("jaw open (norm)")
        ax.legend(loc="upper right", fontsize=8)
        fig.tight_layout()
        fig.savefig(plot_dir / "jaw_signal.png", dpi=120)
        plt.close(fig)

    # episode amplitudes
    if events.episodes:
        fig, ax = plt.subplots(figsize=(10, 3))
        for ep in events.episodes:
            amps = [c.amplitude for c in ep.chews]
            ax.plot(range(len(amps)), amps, marker="o", label=ep.id, ms=3, lw=0.8)
        ax.set_xlabel("chew index in episode")
        ax.set_ylabel("amplitude (norm)")
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(plot_dir / "episode_amplitudes.png", dpi=120)
        plt.close(fig)
