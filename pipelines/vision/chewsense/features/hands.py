"""Hand features via MediaPipe Tasks `HandLandmarker` (modern API)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ._models import get_model
from .offline import require_offline

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    _HAS_MP = True
except Exception:
    _HAS_MP = False


@dataclass
class HandFrameFeatures:
    n_hands: int = 0
    min_distance: float = float("inf")
    hand_points_xy: list[tuple[float, float]] = field(default_factory=list)
    hand_bboxes: list[tuple[int, int, int, int]] = field(default_factory=list)


class HandsExtractor:
    def __init__(self, cfg: dict):
        if not _HAS_MP:
            raise RuntimeError("mediapipe is not installed.")
        require_offline("HandLandmarker")
        self.cfg = cfg
        model_path = get_model("hand_landmarker.task")
        opts = mp_vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=cfg["hands"]["max_num_hands"],
            min_hand_detection_confidence=cfg["hands"]["min_detection_confidence"],
            min_hand_presence_confidence=cfg["hands"]["min_detection_confidence"],
            min_tracking_confidence=cfg["hands"]["min_tracking_confidence"],
        )
        self._lm = mp_vision.HandLandmarker.create_from_options(opts)
        self._last_timestamp_ms = -1

    def close(self):
        try:
            self._lm.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def process(
        self, bgr, mouth_xy: tuple[float, float] | None, face_scale: float, timestamp_s: float
    ) -> HandFrameFeatures:
        h, w = bgr.shape[:2]
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        ts_ms = max(self._last_timestamp_ms + 1, round(timestamp_s * 1000))
        self._last_timestamp_ms = ts_ms
        out = HandFrameFeatures()
        try:
            res = self._lm.detect_for_video(mp_image, ts_ms)
        except Exception:
            return out
        if not res.hand_landmarks:
            return out
        out.n_hands = len(res.hand_landmarks)
        for hand in res.hand_landmarks:
            xs = np.array([lm.x * w for lm in hand], dtype=np.float32)
            ys = np.array([lm.y * h for lm in hand], dtype=np.float32)
            tip = (float(xs[8]), float(ys[8]))  # index-finger tip
            out.hand_points_xy.append(tip)
            x0, x1 = int(max(0, xs.min())), int(min(w - 1, xs.max()))
            y0, y1 = int(max(0, ys.min())), int(min(h - 1, ys.max()))
            out.hand_bboxes.append((x0, y0, x1 - x0, y1 - y0))

            if mouth_xy is not None and face_scale > 0:
                d = float(np.hypot(tip[0] - mouth_xy[0], tip[1] - mouth_xy[1])) / face_scale
                if d < out.min_distance:
                    out.min_distance = d
        return out
