"""Video ingest. Reads a video with OpenCV at native FPS and yields frames in
the original time domain. Audio is extracted side-channel via soundfile if a
WAV companion exists, or via ffmpeg if available.

We do NOT re-encode in v1: keeping the original timeline simpler than building
a re-encode + timestamp-mapping subsystem. The pipeline normalizes signals in
time-units (seconds), not frame units, so variable FPS is handled naturally.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class VideoMeta:
    path: Path
    fps: float
    n_frames: int
    width: int
    height: int
    duration_s: float


def presentation_timeline(path: str | Path) -> tuple[np.ndarray, float]:
    """Decoded display PTS in asset/player seconds, including any initial offset.

    No nominal-FPS fallback: uncertain timestamps must fail visibly.
    ffprobe applies the container edit list, as playback does.
    """
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe is required for presentation-timestamp validation; install ffmpeg")
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time,duration_time,pkt_duration_time:stream=start_time,duration",
        "-of", "json", str(path),
    ], capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    frames = payload.get("frames", [])
    ts = np.array([float(f.get("best_effort_timestamp_time", "nan")) for f in frames])
    if not len(ts) or not np.all(np.isfinite(ts)) or np.any(np.diff(ts) <= 0) or ts[0] < 0:
        raise ValueError("video requires finite, nonnegative, strictly increasing display PTS")
    last_duration = float(frames[-1].get("duration_time", frames[-1].get("pkt_duration_time", 0)))
    if last_duration > 0:
        duration = float(ts[-1] + last_duration)
    else:
        stream = payload.get("streams", [{}])[0]
        if "duration" in stream:
            duration = max(float(ts[-1]), float(stream.get("start_time", ts[0])) + float(stream["duration"]))
        else:
            # No evidence for the last frame's extent: conservatively stop at its PTS.
            duration = float(ts[-1])
    return ts, duration


def probe(path: str | Path) -> VideoMeta:
    p = Path(path)
    ts, duration = presentation_timeline(p)
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {p}")
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    fps = 1 / float(np.median(np.diff(ts))) if len(ts) > 1 else 30.0
    return VideoMeta(path=p, fps=fps, n_frames=len(ts), width=w, height=h, duration_s=duration)


def iter_frames(path: str | Path):
    """Yield (decoded display index, original asset PTS seconds, BGR)."""
    timestamps, _ = presentation_timeline(path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx >= len(timestamps):
                raise ValueError("decoded frame count exceeds presentation-timestamp count")
            yield idx, float(timestamps[idx]), frame
            idx += 1
        if idx != len(timestamps):
            raise ValueError(f"decoded frame/PTS mismatch: {idx} != {len(timestamps)}")
    finally:
        cap.release()


def analysis_grid(timestamps: np.ndarray, fps: float = 30.0):
    """Regularize existing frame-based rules without compressing missing time.

    Nearest source features are reused only within half a 100 ms gap. The source
    index map and validity mask are exported so repeated/missing evidence is visible.
    """
    grid = timestamps[0] + np.arange(int(np.floor((timestamps[-1] - timestamps[0]) * fps)) + 1) / fps
    right = np.clip(np.searchsorted(timestamps, grid), 0, len(timestamps) - 1)
    left = np.maximum(0, right - 1)
    indices = np.where(abs(timestamps[left] - grid) <= abs(timestamps[right] - grid), left, right)
    valid = abs(timestamps[indices] - grid) <= 0.05
    return grid, indices, valid


def extract_audio(path: str | Path, sample_rate: int = 16000) -> tuple[np.ndarray, int] | None:
    """Extract audio to mono float32 at sample_rate. Returns None if ffmpeg is
    missing or video has no audio."""
    if shutil.which("ffmpeg") is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_path = tf.name
    try:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-copyts",
            "-i", str(path),
            "-af", "aresample=async=1:first_pts=0",
            "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-f", "wav", wav_path,
        ]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            return None
        import soundfile as sf
        data, sr = sf.read(wav_path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        return data.astype(np.float32), sr
    finally:
        try:
            Path(wav_path).unlink()
        except OSError:
            pass
