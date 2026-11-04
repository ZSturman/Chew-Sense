"""Synthetic, privacy-free fixtures for the guided collection loop tests."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import numpy as np

from chewsense.canonical import write_candidate_artifact
from chewsense.loop.experiments import SEED_DIR, ExperimentDefinition, definition_sha256
from chewsense.session_bundle import load_coarse_intervals

ORIGIN = 5000.0


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_video(path: Path, seconds: float = 12.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc=size=64x64:rate=30:duration={seconds}", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def seed(experiment_id: str = "baseline-90s") -> ExperimentDefinition:
    return ExperimentDefinition.model_validate_json((SEED_DIR / f"{experiment_id}.json").read_text())


def guided_sidecar(session_id: str, definition: ExperimentDefinition, *, origin: float = ORIGIN,
                   seconds_per_activity: float = 1.2, complete: bool = True,
                   repeat_of: str | None = None) -> dict:
    events, attempts, t = [], [], origin + 0.5
    for index, activity in enumerate(definition.activities):
        events.append({"type": "instruction_presented", "host_time_s": t, "activity_index": index, "attempt": 1})
        events.append({"type": "activity_started", "host_time_s": t + 0.1, "activity_index": index, "attempt": 1})
        end = t + 0.1 + seconds_per_activity
        events.append({"type": "activity_completed", "host_time_s": end, "activity_index": index, "attempt": 1})
        attempts.append({"activity_id": activity.id, "activity_index": index, "attempt": 1,
                         "presented_host_s": t, "started_host_s": t + 0.1, "ended_host_s": end,
                         "outcome": "completed", "valid_seconds": seconds_per_activity})
        t = end
    checks = [{"id": "motion_coverage", "passed": complete, "message": "ok" if complete else "gaps"}]
    return {
        "schema_version": "guided-session/1", "session_id": session_id,
        "experiment_id": definition.experiment_id, "experiment_version": definition.version,
        "definition_sha256": definition_sha256(definition),
        "definition": definition.model_dump(mode="json", exclude_none=True),
        "catalog_version": 1, "video_clock_origin_host_time_s": origin,
        "events": events, "attempts": attempts,
        "invalid_intervals": [{"start_host_s": origin + 2.0, "end_host_s": origin + 2.5,
                               "reasons": ["face_missing"]}],
        "validation": {"complete": complete, "checks": checks, "repeat_activity_ids": []},
        "repeat_of_session_id": repeat_of,
    }


def make_session(root: Path, session_id: str, *, video_seconds: float = 12.0, guided: bool = True,
                 experiment_id: str = "baseline-90s", coarse: list[dict] | None = None,
                 repeat_of: str | None = None, video: Path | None = None) -> Path:
    folder = root / session_id
    folder.mkdir(parents=True)
    movie = folder / f"{session_id}.mov"
    if video is not None:
        movie.write_bytes(video.read_bytes())
    else:
        make_video(movie, video_seconds)
    times = ORIGIN + np.arange(0, video_seconds, 0.02)
    lines = ["timestamp,dtNs,ax,ay,az,gx,gy,gz"]
    lines += [f"{t:.9f},0,0.0,0.0,0.0,0.01,0.0,0.0" for t in times]
    motion = folder / f"{session_id}.csv"
    motion.write_text("\n".join(lines) + "\n")
    manifest = {
        "schema_version": "2.0.0", "session_id": session_id, "wearer_id": "local-user",
        "source_app": "collect", "source_app_version": "test", "activity": "unknown",
        "status": "collected", "created_at": "2026-10-26T00:00:00Z", "updated_at": "2026-10-26T00:00:00Z",
        "video": {"path": movie.name, "media_type": "video", "sha256": sha(movie)},
        "motion": {"path": motion.name, "media_type": "motion_csv", "sha256": sha(motion)},
        "audio": {"enabled": False, "permission": "not_requested"},
        "clock_sync": {"time_domain": "host_monotonic", "video_clock_origin_host_time_s": ORIGIN,
                       "video_start_host_time_s": ORIGIN, "first_motion_host_time_s": ORIGIN,
                       "motion_to_video_offset_s": 0.0, "method": "shared_host_clock",
                       "confidence": "high"},
        "capture": {"fps": 30.0, "sensor_rate_hz": 50.0, "duration_s": video_seconds},
        "quality": {"video_present": True, "motion_present": True, "motion_samples": len(times)},
    }
    (folder / "session.json").write_text(json.dumps(manifest, indent=2))
    if coarse is not None:
        (folder / "annotations").mkdir()
        (folder / "annotations/coarse_labels.json").write_text(json.dumps({
            "schema_version": "2.0.0", "artifact_type": "coarse_labels", "session_id": session_id,
            "intervals": coarse}))
    if guided:
        sidecar = guided_sidecar(session_id, seed(experiment_id), repeat_of=repeat_of)
        (folder / "guided_session.json").write_text(json.dumps(sidecar, indent=2))
    return folder


def zip_session(folder: Path, out: Path, *, extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, f"{folder.name}/{path.relative_to(folder)}")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return out


def fake_vision(chews: list[tuple[float, float, float]] | None = None):
    """Stand-in for MediaPipe Vision: writes telemetry and one candidate revision."""
    from chewsense.session_bundle import load_session_manifest

    chews = [(1.0, 1.2, 1.4), (5.0, 5.2, 5.4)] if chews is None else chews
    calls = []

    def run(workspace: Path, session_id: str, log: Path) -> None:
        calls.append(session_id)
        session = workspace / "sessions/raw" / session_id
        derived = workspace / "derived/vision" / session_id
        derived.mkdir(parents=True, exist_ok=True)
        manifest = load_session_manifest(session)
        ts = np.arange(0, manifest.capture.duration_s, 1 / 30)
        rows = ["ts,head_pitch,head_yaw,face_detected"] + [f"{t:.4f},0.0,0.0,True" for t in ts]
        (derived / "frame_predictions.csv").write_text("\n".join(rows) + "\n")
        write_candidate_artifact(
            derived, session_id=session_id,
            chew_hypotheses=[dict(start_ts=a, peak_ts=b, end_ts=c, confidence=0.4 if i else 0.9)
                             for i, (a, b, c) in enumerate(chews)],
            food_entry_hypotheses=[], coarse_intervals=load_coarse_intervals(session),
            debug_telemetry={"video_sha256": sha(session / manifest.video.path)})

    run.calls = calls
    return run


def loop_config(**overrides) -> dict:
    from chewsense.loop.config import load_loop_config

    config = copy.deepcopy(load_loop_config())
    config["lmstudio"]["enabled"] = False
    for dotted, value in overrides.items():
        target = config
        *path, last = dotted.split(".")
        for key in path:
            target = target[key]
        target[last] = value
    return config
