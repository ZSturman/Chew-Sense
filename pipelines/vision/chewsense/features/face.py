"""Face features via MediaPipe Tasks `FaceLandmarker` (modern API).

Per-frame outputs (see `FaceFrameFeatures`):
- jaw_open: vertical inner-lip distance / face_scale
- mouth_width: mouth-corner distance / face_scale
- face_scale: inter-ocular distance in image px (normalization unit)
- mouth_center / lip endpoints / bbox: pixel coords for overlays + h2m distance
- head_yaw, head_pitch: rough geometry-only estimate

Returns face_detected=False if the model finds nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ._models import get_model

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    _HAS_MP = True
except Exception:
    _HAS_MP = False


@dataclass
class FaceFrameFeatures:
    face_detected: bool = False
    face_scale: float = 0.0
    jaw_open: float = 0.0                 # legacy alias: inner_lip_open
    mouth_width: float = 0.0
    head_yaw: float = 0.0
    head_pitch: float = 0.0
    mouth_center_xy: tuple[float, float] = (0.0, 0.0)
    upper_lip_xy: tuple[float, float] = (0.0, 0.0)
    lower_lip_xy: tuple[float, float] = (0.0, 0.0)
    bbox_xywh: tuple[int, int, int, int] = (0, 0, 0, 0)
    landmarks: np.ndarray | None = field(default=None, repr=False)
    # Phase 1 additions (mouth-state / cycle detection):
    inner_lip_open: float = 0.0           # inner-lip vertical distance / face_scale (== jaw_open)
    outer_lip_open: float = 0.0           # outer-lip vertical distance / face_scale
    lip_aperture_ratio: float = 0.0       # inner_lip_open / max(outer_lip_open, eps)
    chin_to_nose: float = 0.0             # vertical chin->nose distance / face_scale (jaw translation proxy)
    jaw_width: float = 0.0                # jaw-line corner distance / face_scale
    chin_xy: tuple[float, float] = (0.0, 0.0)
    nose_xy: tuple[float, float] = (0.0, 0.0)
    # Phase 1.5 additions (MediaPipe blendshapes + 3D facial transform matrix).
    # Blendshapes are ARKit-style activations in [0, 1] derived from the 3D mesh,
    # so they are intrinsically pose- and lip-closure-invariant — `jaw_open_bs`
    # tracks jaw articulation even when the lips stay sealed. All default to NaN
    # so consumers can detect "blendshape pipeline disabled / face missing".
    jaw_open_bs: float = float("nan")
    mouth_close_bs: float = float("nan")
    mouth_funnel_bs: float = float("nan")
    mouth_pucker_bs: float = float("nan")
    mouth_roll_lower_bs: float = float("nan")
    mouth_roll_upper_bs: float = float("nan")
    mouth_press_left_bs: float = float("nan")
    mouth_press_right_bs: float = float("nan")
    mouth_smile_left_bs: float = float("nan")
    mouth_smile_right_bs: float = float("nan")
    face_confidence: float = 0.0
    face_transform: np.ndarray | None = field(default=None, repr=False)
    head_yaw_matrix: float = float("nan")    # yaw derived from facial_transformation_matrix
    head_pitch_matrix: float = float("nan")
    head_roll_matrix: float = float("nan")


class FaceExtractor:
    """Stateful FaceLandmarker wrapper. Reuse across a whole video."""

    def __init__(self, cfg: dict):
        if not _HAS_MP:
            raise RuntimeError("mediapipe is not installed.")
        self.cfg = cfg
        model_path = get_model("face_landmarker.task")
        face_cfg = cfg["face"]
        # Phase 1.5: enable blendshapes + facial transformation matrix. Both ship
        # in the standard `face_landmarker.task` bundle so this adds no new
        # downloads. `jawOpen`/`mouthClose` blendshapes are the primary
        # mouth-open signal; the 4x4 transform yields stable head pose without
        # PnP-from-landmarks brittleness.
        opts = mp_vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=face_cfg["min_detection_confidence"],
            min_face_presence_confidence=face_cfg.get(
                "min_presence_confidence", face_cfg["min_detection_confidence"]
            ),
            min_tracking_confidence=face_cfg["min_tracking_confidence"],
            output_face_blendshapes=bool(face_cfg.get("output_blendshapes", True)),
            output_facial_transformation_matrixes=bool(
                face_cfg.get("output_transform_matrix", True)
            ),
        )
        self._lm = mp_vision.FaceLandmarker.create_from_options(opts)
        self._frame_idx = 0
        # Which blendshape category names to extract (subset of the 52 ARKit
        # blendshapes that MediaPipe outputs). Order doesn't matter; we map by
        # category_name. `jawOpen` is the headline mouth-aperture signal.
        self._bs_names = {
            "jawOpen": "jaw_open_bs",
            "mouthClose": "mouth_close_bs",
            "mouthFunnel": "mouth_funnel_bs",
            "mouthPucker": "mouth_pucker_bs",
            "mouthRollLower": "mouth_roll_lower_bs",
            "mouthRollUpper": "mouth_roll_upper_bs",
            "mouthPressLeft": "mouth_press_left_bs",
            "mouthPressRight": "mouth_press_right_bs",
            "mouthSmileLeft": "mouth_smile_left_bs",
            "mouthSmileRight": "mouth_smile_right_bs",
        }

    def close(self):
        try:
            self._lm.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def process(self, bgr) -> FaceFrameFeatures:
        h, w = bgr.shape[:2]
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        # Tasks API requires a monotonically increasing timestamp in ms
        ts_ms = self._frame_idx * 33  # arbitrary monotonic stride; not used downstream
        self._frame_idx += 1
        try:
            res = self._lm.detect_for_video(mp_image, ts_ms)
        except Exception:
            return FaceFrameFeatures(face_detected=False)
        if not res.face_landmarks:
            return FaceFrameFeatures(face_detected=False)

        lms = res.face_landmarks[0]
        pts = np.array([[lm.x * w, lm.y * h, lm.z * w] for lm in lms], dtype=np.float32)
        f = self.cfg["face"]
        upper = pts[f["upper_lip_idx"]]
        lower = pts[f["lower_lip_idx"]]
        ml = pts[f["mouth_left_idx"]]
        mr = pts[f["mouth_right_idx"]]
        eL = pts[f["left_eye_idx"]]
        eR = pts[f["right_eye_idx"]]
        # Phase 1 additions
        outer_up = pts[f.get("outer_upper_lip_idx", 0)]
        outer_lo = pts[f.get("outer_lower_lip_idx", 17)]
        chin = pts[f.get("chin_idx", 152)]
        nose = pts[f.get("nose_idx", 1)]
        jaw_left = pts[f.get("jaw_left_idx", 172)]
        jaw_right = pts[f.get("jaw_right_idx", 397)]

        face_scale = float(np.linalg.norm(eR[:2] - eL[:2]))
        if face_scale < 1e-3:
            return FaceFrameFeatures(face_detected=False)

        jaw_open_px = float(np.linalg.norm(upper[:2] - lower[:2]))
        mouth_width_px = float(np.linalg.norm(ml[:2] - mr[:2]))
        outer_lip_px = float(np.linalg.norm(outer_up[:2] - outer_lo[:2]))
        chin_to_nose_px = float(np.linalg.norm(chin[:2] - nose[:2]))
        jaw_width_px = float(np.linalg.norm(jaw_left[:2] - jaw_right[:2]))
        mouth_center = ((upper[0] + lower[0]) / 2, (upper[1] + lower[1]) / 2)

        inner_lip_open = jaw_open_px / face_scale
        outer_lip_open = outer_lip_px / face_scale
        lip_aperture_ratio = inner_lip_open / max(outer_lip_open, 1e-4)

        eye_mid = (eL + eR) / 2
        head_yaw = float(np.arctan2(nose[2] - eye_mid[2], face_scale))
        head_pitch = float(np.arctan2(nose[1] - eye_mid[1], face_scale))

        x = pts[:, 0]; y = pts[:, 1]
        x0, x1 = int(max(0, x.min())), int(min(w - 1, x.max()))
        y0, y1 = int(max(0, y.min())), int(min(h - 1, y.max()))

        # ---- Phase 1.5: blendshapes + transform-matrix-derived pose ----
        bs_values: dict[str, float] = {}
        if getattr(res, "face_blendshapes", None):
            try:
                for cat in res.face_blendshapes[0]:
                    name = getattr(cat, "category_name", None) or getattr(cat, "categoryName", None)
                    if name in self._bs_names:
                        bs_values[self._bs_names[name]] = float(cat.score)
            except Exception:
                bs_values = {}

        face_transform: np.ndarray | None = None
        yaw_m = pitch_m = roll_m = float("nan")
        if getattr(res, "facial_transformation_matrixes", None):
            try:
                mat = np.asarray(res.facial_transformation_matrixes[0], dtype=np.float32)
                if mat.shape == (4, 4):
                    face_transform = mat
                    yaw_m, pitch_m, roll_m = _euler_from_matrix(mat[:3, :3])
            except Exception:
                face_transform = None

        # Use the matrix-derived pose when available — it is far more stable
        # than the landmark-only nose/eye geometry that the legacy path uses.
        # Keep legacy values as a fallback so downstream code never sees NaN.
        head_yaw_out = yaw_m if np.isfinite(yaw_m) else head_yaw
        head_pitch_out = pitch_m if np.isfinite(pitch_m) else head_pitch

        # Face confidence proxy: MediaPipe doesn't expose a direct per-frame
        # score, but face_landmarks presence + reasonable face_scale + at least
        # one valid blendshape together signal a high-quality detection.
        face_conf = 1.0 if (face_scale > 1e-3 and lms) else 0.0
        if bs_values:
            face_conf = min(1.0, face_conf)
        else:
            # Blendshape extraction failed for this frame; treat as lower
            # confidence so downstream consensus de-weights it.
            face_conf *= 0.75

        return FaceFrameFeatures(
            face_detected=True,
            face_scale=face_scale,
            jaw_open=inner_lip_open,
            mouth_width=mouth_width_px / face_scale,
            head_yaw=float(head_yaw_out),
            head_pitch=float(head_pitch_out),
            mouth_center_xy=(float(mouth_center[0]), float(mouth_center[1])),
            upper_lip_xy=(float(upper[0]), float(upper[1])),
            lower_lip_xy=(float(lower[0]), float(lower[1])),
            bbox_xywh=(x0, y0, x1 - x0, y1 - y0),
            landmarks=pts,
            inner_lip_open=inner_lip_open,
            outer_lip_open=outer_lip_open,
            lip_aperture_ratio=lip_aperture_ratio,
            chin_to_nose=chin_to_nose_px / face_scale,
            jaw_width=jaw_width_px / face_scale,
            chin_xy=(float(chin[0]), float(chin[1])),
            nose_xy=(float(nose[0]), float(nose[1])),
            jaw_open_bs=bs_values.get("jaw_open_bs", float("nan")),
            mouth_close_bs=bs_values.get("mouth_close_bs", float("nan")),
            mouth_funnel_bs=bs_values.get("mouth_funnel_bs", float("nan")),
            mouth_pucker_bs=bs_values.get("mouth_pucker_bs", float("nan")),
            mouth_roll_lower_bs=bs_values.get("mouth_roll_lower_bs", float("nan")),
            mouth_roll_upper_bs=bs_values.get("mouth_roll_upper_bs", float("nan")),
            mouth_press_left_bs=bs_values.get("mouth_press_left_bs", float("nan")),
            mouth_press_right_bs=bs_values.get("mouth_press_right_bs", float("nan")),
            mouth_smile_left_bs=bs_values.get("mouth_smile_left_bs", float("nan")),
            mouth_smile_right_bs=bs_values.get("mouth_smile_right_bs", float("nan")),
            face_confidence=face_conf,
            face_transform=face_transform,
            head_yaw_matrix=float(yaw_m) if np.isfinite(yaw_m) else float("nan"),
            head_pitch_matrix=float(pitch_m) if np.isfinite(pitch_m) else float("nan"),
            head_roll_matrix=float(roll_m) if np.isfinite(roll_m) else float("nan"),
        )


def _euler_from_matrix(R: np.ndarray) -> tuple[float, float, float]:
    """Decompose a 3x3 rotation matrix into (yaw, pitch, roll) in radians.

    Uses the standard intrinsic Y-X-Z (yaw-pitch-roll) decomposition that
    matches the geometric `head_yaw`/`head_pitch` conventions used elsewhere
    in this codebase:
      - yaw   = rotation about world Y axis (head turning left/right)
      - pitch = rotation about world X axis (head nodding up/down; positive
                = chin down toward plate, to match the legacy convention)
      - roll  = rotation about world Z axis (head tilting ear-to-shoulder)
    """
    # Clip for numerical safety
    sy = float(np.clip(-R[2, 0], -1.0, 1.0))
    pitch = float(np.arcsin(sy))
    cos_pitch = float(np.cos(pitch))
    if abs(cos_pitch) < 1e-6:
        # Gimbal lock: fall back to alternate decomposition
        yaw = float(np.arctan2(-R[0, 1], R[1, 1]))
        roll = 0.0
    else:
        yaw = float(np.arctan2(R[1, 0], R[0, 0]))
        roll = float(np.arctan2(R[2, 1], R[2, 2]))
    return yaw, pitch, roll
