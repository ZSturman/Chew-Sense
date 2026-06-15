"""Video ingest. Reads a video with OpenCV at native FPS and yields frames in
the original time domain. Audio is extracted side-channel via soundfile if a
WAV companion exists, or via ffmpeg if available.

We do NOT re-encode in v1: keeping the original timeline simpler than building
a re-encode + timestamp-mapping subsystem. The pipeline normalizes signals in
time-units (seconds), not frame units, so variable FPS is handled naturally.
"""
from __future__ import annotations

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


def probe(path: str | Path) -> VideoMeta:
    p = Path(path)
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {p}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    duration = n_frames / fps if fps > 0 else 0.0
    return VideoMeta(path=p, fps=float(fps), n_frames=n_frames, width=w, height=h, duration_s=duration)


def iter_frames(path: str | Path):
    """Yield (frame_idx, ts_seconds, bgr_frame). Timestamp is in original time."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            ts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if ts <= 0:
                ts = idx / fps
            yield idx, ts, frame
            idx += 1
    finally:
        cap.release()


def extract_audio(path: str | Path, sample_rate: int = 16000) -> tuple[np.ndarray, int] | None:
    """Extract audio to mono float32 at sample_rate. Returns None if ffmpeg is
    missing or video has no audio."""
    if shutil.which("ffmpeg") is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_path = tf.name
    try:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(path),
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
