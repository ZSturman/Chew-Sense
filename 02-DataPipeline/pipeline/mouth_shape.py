"""Legacy mouth-shape feature extractor.

The production mouth-state and chew-cycle path is
``02b-VideoAssistedLabeling``. Keep this module only for compatibility and
fixture-level comparison while old datasets are migrated.

Processes .mov files frame-by-frame to compute 4 continuous mouth shape
parameters from face landmarks:
  1. mouth_openness  — vertical lip gap normalized by face height
  2. mouth_width     — lip corner distance normalized by face width
  3. jaw_displacement — chin-to-nose distance change from baseline
  4. lip_compression  — inner lip height / outer lip height ratio

These parameters serve as regression labels for training ChewNet
to predict mouth state from AirPods motion data alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pandas as pd

try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False


# MediaPipe Face Mesh landmark indices for mouth/jaw
# Reference: https://github.com/google/mediapipe/blob/master/mediapipe/modules/face_geometry/data/canonical_face_model_uv_visualization.png
_UPPER_LIP_TOP = 13       # Top of upper lip (inner)
_LOWER_LIP_BOTTOM = 14    # Bottom of lower lip (inner)
_LEFT_LIP_CORNER = 61     # Left mouth corner
_RIGHT_LIP_CORNER = 291   # Right mouth corner
_CHIN = 152               # Bottom of chin
_NOSE_TIP = 1             # Tip of nose
_FOREHEAD = 10            # Top of forehead (for face height)
_LEFT_EAR = 234           # Left ear tragion (for face width)
_RIGHT_EAR = 454          # Right ear tragion (for face width)

# Inner lip landmarks for compression ratio
_UPPER_LIP_INNER_TOP = 13
_UPPER_LIP_INNER_BOTTOM = 14
_UPPER_LIP_OUTER_TOP = 0     # Center of upper lip outer edge
_LOWER_LIP_OUTER_BOTTOM = 17 # Center of lower lip outer edge


@dataclass
class MouthShapeFrame:
    """Mouth shape parameters for a single video frame."""
    frame_idx: int
    timestamp_sec: float
    mouth_openness: float
    mouth_width: float
    jaw_displacement: float
    lip_compression: float


@dataclass
class FacePresenceFrame:
    """Per-frame face-detection flag, for face-presence track."""
    frame_idx: int
    timestamp_sec: float
    face_present: bool


def _landmark_dist(landmarks, idx_a: int, idx_b: int) -> float:
    """Euclidean distance between two landmarks in normalized coordinates."""
    a = landmarks[idx_a]
    b = landmarks[idx_b]
    return float(np.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2))


def _compute_mouth_params(landmarks) -> tuple[float, float, float, float]:
    """
    Compute 4 mouth shape parameters from MediaPipe face landmarks.

    All values are normalized by face dimensions to be scale-invariant.

    Returns:
        (mouth_openness, mouth_width, jaw_displacement, lip_compression)
    """
    # Face reference distances for normalization
    face_height = _landmark_dist(landmarks, _FOREHEAD, _CHIN)
    face_width = _landmark_dist(landmarks, _LEFT_EAR, _RIGHT_EAR)

    if face_height < 1e-6 or face_width < 1e-6:
        return (0.0, 0.0, 0.0, 0.0)

    # 1. Mouth openness: vertical gap between inner lips / face height
    lip_gap = _landmark_dist(landmarks, _UPPER_LIP_TOP, _LOWER_LIP_BOTTOM)
    mouth_openness = lip_gap / face_height

    # 2. Mouth width: horizontal lip corner distance / face width
    lip_width = _landmark_dist(landmarks, _LEFT_LIP_CORNER, _RIGHT_LIP_CORNER)
    mouth_width = lip_width / face_width

    # 3. Jaw displacement: chin-to-nose distance / face height
    #    Higher values = jaw dropped more
    chin_nose = _landmark_dist(landmarks, _CHIN, _NOSE_TIP)
    jaw_displacement = chin_nose / face_height

    # 4. Lip compression: inner lip vertical span / outer lip vertical span
    #    Low values = lips pressed tightly together
    inner_lip_height = _landmark_dist(landmarks, _UPPER_LIP_INNER_TOP, _UPPER_LIP_INNER_BOTTOM)
    outer_lip_height = _landmark_dist(landmarks, _UPPER_LIP_OUTER_TOP, _LOWER_LIP_OUTER_BOTTOM)

    if outer_lip_height < 1e-6:
        lip_compression = 0.0
    else:
        lip_compression = inner_lip_height / outer_lip_height

    return (mouth_openness, mouth_width, jaw_displacement, lip_compression)


MOUTH_SHAPE_COLUMNS = [
    "mouth_openness",
    "mouth_width",
    "jaw_displacement",
    "lip_compression",
]


def extract_mouth_shape_from_video(
    video_path: Path,
    max_faces: int = 1,
    min_detection_confidence: float = 0.5,
    min_tracking_confidence: float = 0.5,
    return_presence: bool = False,
):
    """
    Extract mouth shape parameters from every frame of a video.

    Args:
        video_path: Path to .mov or .mp4 video file
        max_faces: Maximum number of faces to detect (uses first)
        min_detection_confidence: MediaPipe detection confidence threshold
        min_tracking_confidence: MediaPipe tracking confidence threshold
        return_presence: When True, returns
            (mouth_frames, presence_frames, fps) so the caller can build a
            face-presence track in the same single pass through the video.

    Returns:
        By default, list of MouthShapeFrame, one per video frame with a
        detected face. Frames where no face is detected are skipped.
        When `return_presence=True`, returns
        (list[MouthShapeFrame], list[FacePresenceFrame], fps).
    """
    if not MEDIAPIPE_AVAILABLE:
        raise RuntimeError(
            "mediapipe is required for mouth shape extraction.\n"
            "Install with: pip install mediapipe"
        )

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0  # Fallback

    face_mesh = mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False,
        max_num_faces=max_faces,
        refine_landmarks=True,
        min_detection_confidence=min_detection_confidence,
        min_tracking_confidence=min_tracking_confidence,
    )

    results: list[MouthShapeFrame] = []
    presence: list[FacePresenceFrame] = []
    frame_idx = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # MediaPipe expects RGB
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_result = face_mesh.process(rgb)

            ts = frame_idx / fps
            face_present = bool(mp_result.multi_face_landmarks)
            if face_present:
                landmarks = mp_result.multi_face_landmarks[0].landmark
                openness, width, jaw, compression = _compute_mouth_params(landmarks)

                results.append(MouthShapeFrame(
                    frame_idx=frame_idx,
                    timestamp_sec=ts,
                    mouth_openness=openness,
                    mouth_width=width,
                    jaw_displacement=jaw,
                    lip_compression=compression,
                ))

            if return_presence:
                presence.append(FacePresenceFrame(
                    frame_idx=frame_idx,
                    timestamp_sec=ts,
                    face_present=face_present,
                ))

            frame_idx += 1
    finally:
        cap.release()
        face_mesh.close()

    print(f"  Extracted {len(results)} / {frame_idx} frames with face detected")
    if return_presence:
        return results, presence, fps
    return results


def mouth_shape_to_dataframe(frames: list[MouthShapeFrame]) -> pd.DataFrame:
    """Convert list of MouthShapeFrame to a DataFrame."""
    return pd.DataFrame([
        {
            "frame_idx": f.frame_idx,
            "timestamp_sec": f.timestamp_sec,
            "mouth_openness": f.mouth_openness,
            "mouth_width": f.mouth_width,
            "jaw_displacement": f.jaw_displacement,
            "lip_compression": f.lip_compression,
        }
        for f in frames
    ])


def extract_and_save(
    video_path: Path,
    output_csv: Path,
    **kwargs,
) -> Path:
    """
    Extract mouth shape from video and save to CSV.

    Args:
        video_path: Path to video file
        output_csv: Path for output CSV
        **kwargs: Passed to extract_mouth_shape_from_video

    Returns:
        Path to the saved CSV
    """
    print(f"Extracting mouth shape: {video_path.name}")
    frames = extract_mouth_shape_from_video(video_path, **kwargs)

    if not frames:
        raise ValueError(f"No faces detected in video: {video_path}")

    df = mouth_shape_to_dataframe(frames)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    print(f"  Saved: {output_csv} ({len(df)} rows)")
    return output_csv


# ----------------------------------------------------------------------
# Face-presence track
# ----------------------------------------------------------------------

def presence_frames_to_intervals(
    presence: list[FacePresenceFrame],
    fps: float,
    smoothing_frames: int = 3,
) -> list[dict]:
    """
    Collapse a per-frame face-presence flag into time intervals.

    Short flips (< smoothing_frames consecutive frames) are absorbed into
    the surrounding state so brief MediaPipe drop-outs don't create
    spurious "face missing" bands in the review UI.
    """
    if not presence:
        return []

    flags = [p.face_present for p in presence]

    # Smooth: any run shorter than `smoothing_frames` adopts its neighbours.
    if smoothing_frames > 1 and len(flags) > smoothing_frames:
        smoothed = list(flags)
        run_start = 0
        for i in range(1, len(smoothed) + 1):
            if i == len(smoothed) or smoothed[i] != smoothed[run_start]:
                run_len = i - run_start
                if run_len < smoothing_frames and run_start > 0 and i < len(smoothed):
                    fill = smoothed[run_start - 1]
                    for j in range(run_start, i):
                        smoothed[j] = fill
                run_start = i
        flags = smoothed

    intervals: list[dict] = []
    run_start = 0
    for i in range(1, len(flags) + 1):
        if i == len(flags) or flags[i] != flags[run_start]:
            start_t = presence[run_start].timestamp_sec
            end_t = presence[i - 1].timestamp_sec + (1.0 / fps if fps > 0 else 0.0)
            intervals.append({
                "start": float(start_t),
                "end": float(end_t),
                "present": bool(flags[run_start]),
            })
            run_start = i
    return intervals


def write_face_presence_json(
    bundle_dir: Path,
    presence: list[FacePresenceFrame],
    fps: float,
    smoothing_frames: int = 3,
) -> Path:
    """Write `face_presence.json` consumed by both the review UI and
    the pipeline's training/eval masking layer."""
    import json
    bundle_dir = Path(bundle_dir)
    intervals = presence_frames_to_intervals(presence, fps, smoothing_frames=smoothing_frames)
    payload = {
        "frame_rate": float(fps),
        "intervals": intervals,
    }
    out = bundle_dir / "face_presence.json"
    out.write_text(json.dumps(payload, indent=2))
    return out
