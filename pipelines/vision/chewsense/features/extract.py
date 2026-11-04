"""Feature extraction without detection.

Writes a per-frame feature parquet from a raw video. Used to build training
sets for the chew-head classifier independent of the detector.

Feature vector per frame (all face-scale-normalized where applicable):
- jaw_open, jaw_open_smooth, jaw_open_bp
- mouth_width, head_yaw, head_pitch
- hand_to_mouth_min, n_hands
- audio_rms, is_speaking
- face_detected, face_scale
- d_jaw_open: temporal first-difference of jaw_open_smooth
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

from .audio import compute_audio_frame_features
from .face import FaceExtractor
from .hands import HandsExtractor
from ..ingest import analysis_grid, extract_audio, iter_frames, probe
from ..signals.jaw_signal import bandpass, smooth

# Column order used by trainer + inference
FEATURE_COLS = [
    "jaw_open",
    "jaw_open_smooth",
    "jaw_open_bp",
    "d_jaw_open",
    "mouth_width",
    "head_yaw",
    "head_pitch",
    "hand_to_mouth_min",
    "n_hands",
    "audio_rms",
    "is_speaking",
    "face_detected",
    "face_scale",
]


def extract(video_path: str | Path, cfg: dict) -> pd.DataFrame:
    video_path = Path(video_path)
    meta = probe(video_path)

    timestamps: list[float] = []
    jaw_open: list[float] = []
    mouth_width: list[float] = []
    head_yaw: list[float] = []
    head_pitch: list[float] = []
    h2m: list[float] = []
    n_hands: list[int] = []
    face_detected: list[bool] = []
    face_scale: list[float] = []

    with FaceExtractor(cfg) as face_ext, HandsExtractor(cfg) as hands_ext:
        it = iter_frames(video_path)
        if meta.n_frames > 0:
            it = tqdm(it, total=meta.n_frames, desc="extract", unit="frame")
        for _, ts, frame in it:
            ff = face_ext.process(frame, ts)
            mxy = ff.mouth_center_xy if ff.face_detected else None
            hf = hands_ext.process(frame, mxy, ff.face_scale if ff.face_detected else 0.0, ts)
            timestamps.append(ts)
            face_detected.append(ff.face_detected)
            face_scale.append(ff.face_scale)
            jaw_open.append(ff.jaw_open if ff.face_detected else 0.0)
            mouth_width.append(ff.mouth_width if ff.face_detected else 0.0)
            head_yaw.append(ff.head_yaw if ff.face_detected else 0.0)
            head_pitch.append(ff.head_pitch if ff.face_detected else 0.0)
            h2m.append(hf.min_distance if hf.min_distance < 1e3 else 1e3)
            n_hands.append(hf.n_hands)

    source_pts = np.asarray(timestamps, dtype=np.float64)
    ts_arr, source_indices, coverage = analysis_grid(source_pts)
    jaw_open, mouth_width, head_yaw, head_pitch, h2m, n_hands, face_detected, face_scale = (
        np.asarray(values)[source_indices] for values in
        (jaw_open, mouth_width, head_yaw, head_pitch, h2m, n_hands, face_detected, face_scale)
    )
    face_detected = face_detected & coverage

    jaw = np.array(jaw_open, dtype=np.float32)
    js = smooth(jaw, cfg)
    jb = bandpass(js, 30.0, cfg)
    djs = np.zeros_like(js)
    djs[1:] = np.diff(js)

    audio_pair = extract_audio(video_path, sample_rate=cfg["ingest"]["audio_sample_rate"])
    if audio_pair is not None:
        audio, sr = audio_pair
    else:
        audio, sr = np.zeros(0, dtype=np.float32), cfg["ingest"]["audio_sample_rate"]
    afeats = compute_audio_frame_features(audio, sr, ts_arr, cfg)

    df = pd.DataFrame({
        "frame_idx": np.arange(len(ts_arr), dtype=np.int32),
        "ts": ts_arr,
        "source_frame_idx": source_indices,
        "frame_coverage": coverage.astype(np.int8),
        "jaw_open": jaw,
        "jaw_open_smooth": js.astype(np.float32),
        "jaw_open_bp": jb.astype(np.float32),
        "d_jaw_open": djs.astype(np.float32),
        "mouth_width": np.array(mouth_width, dtype=np.float32),
        "head_yaw": np.array(head_yaw, dtype=np.float32),
        "head_pitch": np.array(head_pitch, dtype=np.float32),
        "hand_to_mouth_min": np.array(h2m, dtype=np.float32),
        "n_hands": np.array(n_hands, dtype=np.int8),
        "audio_rms": np.array([f.rms for f in afeats], dtype=np.float32),
        "is_speaking": np.array([f.is_speaking for f in afeats], dtype=np.int8),
        "face_detected": np.array(face_detected, dtype=np.int8),
        "face_scale": np.array(face_scale, dtype=np.float32),
    })
    df.attrs["video_path"] = str(video_path)
    df.attrs["fps"] = 30.0
    df.attrs["video_id"] = video_path.stem
    return df


def write_features(video_path: str | Path, out_path: str | Path, cfg: dict) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = extract(video_path, cfg)
    df.to_parquet(out_path, index=False)
    # Companion sidecar with attrs
    side = out_path.with_suffix(".meta.json")
    import json
    side.write_text(json.dumps({k: v for k, v in df.attrs.items()}, indent=2))
    return out_path
