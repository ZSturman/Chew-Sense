"""Auto-calibrate resting baselines for mouth-aperture signals.

The mouth-state FSM and cycle classifier need a *per-pose* "resting closed
mouth" reference for each signal channel (inner-lip distance, outer-lip
distance, chin->nose distance). They are estimated automatically from the
first ``calibration.window_s`` seconds of the video: any frame in that window
that looks like quiet, unoccluded, hand-free rest contributes to the median.

If no stable rest sample is found per pose, we fall back to the median over
the entire clip and emit a warning string that the pipeline includes in the
debug manifest. Baselines are also written to ``signals/baselines.json``.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class ChannelBaseline:
    median: float = 0.0
    mad: float = 1e-3        # >0 so z-scores are finite
    n_samples: int = 0
    source: str = "fallback_full_clip"


@dataclass
class PoseBaselines:
    pose_class: str = "frontal"
    inner_lip: ChannelBaseline = field(default_factory=ChannelBaseline)
    outer_lip: ChannelBaseline = field(default_factory=ChannelBaseline)
    chin_to_nose: ChannelBaseline = field(default_factory=ChannelBaseline)
    # Phase 1.5: MediaPipe `jawOpen` blendshape baseline. Optional — if the
    # blendshape stream is unavailable for a clip, n_samples stays 0 and the
    # mouth-state FSM falls back to the legacy 2D channels.
    jaw_open_bs: ChannelBaseline = field(default_factory=ChannelBaseline)


@dataclass
class CalibrationResult:
    per_pose: dict[str, PoseBaselines] = field(default_factory=dict)
    fallback_global: PoseBaselines = field(default_factory=PoseBaselines)
    warnings: list[str] = field(default_factory=list)

    def get(self, pose_class: str) -> PoseBaselines:
        return self.per_pose.get(pose_class, self.fallback_global)

    def to_json(self) -> dict:
        return {
            "per_pose": {k: _pose_to_dict(v) for k, v in self.per_pose.items()},
            "fallback_global": _pose_to_dict(self.fallback_global),
            "warnings": list(self.warnings),
        }


def _pose_to_dict(pb: PoseBaselines) -> dict:
    return {
        "pose_class": pb.pose_class,
        "inner_lip": asdict(pb.inner_lip),
        "outer_lip": asdict(pb.outer_lip),
        "chin_to_nose": asdict(pb.chin_to_nose),
        "jaw_open_bs": asdict(pb.jaw_open_bs),
    }


def _channel(values: np.ndarray, source: str, min_mad: float = 0.0) -> ChannelBaseline:
    if values.size == 0:
        return ChannelBaseline(source=source)
    med = float(np.median(values))
    mad = float(max(np.median(np.abs(values - med)), min_mad, 1e-4))
    return ChannelBaseline(median=med, mad=mad, n_samples=int(values.size), source=source)


def calibrate(
    *,
    fps: float,
    inner_lip: np.ndarray,
    outer_lip: np.ndarray,
    chin_to_nose: np.ndarray,
    pose_class: list[str],
    face_detected: np.ndarray,
    occluded: np.ndarray,
    is_speaking: np.ndarray,
    h2m: np.ndarray,
    cfg: dict,
    jaw_open_bs: np.ndarray | None = None,
) -> CalibrationResult:
    n = len(inner_lip)
    res = CalibrationResult()
    if n == 0 or fps <= 0:
        res.warnings.append("calibration: empty input")
        return res

    cal = cfg["calibration"]
    window_n = max(1, int(cal["window_s"] * fps))
    window_n = min(window_n, n)
    enter = cfg["hands"]["proximity_enter"]
    mad_floors = cal.get("min_mad", {}) or {}
    mf_inner = float(mad_floors.get("inner_lip", 0.0))
    mf_outer = float(mad_floors.get("outer_lip", 0.0))
    mf_chin = float(mad_floors.get("chin_to_nose", 0.0))
    mf_bs = float(mad_floors.get("jaw_open_bs", 0.01))

    pose_arr = np.array(pose_class, dtype=object)

    # Blendshape channel is optional. If unavailable, use NaN-filled placeholder
    # so the mask machinery below still works without branching.
    if jaw_open_bs is None:
        bs_arr = np.full(n, np.nan, dtype=np.float32)
        bs_available = False
    else:
        bs_arr = np.asarray(jaw_open_bs, dtype=np.float32)
        bs_available = bool(np.isfinite(bs_arr).any())

    def _stable_mask(within_window: bool) -> np.ndarray:
        m = face_detected.astype(bool) & ~occluded.astype(bool) & ~is_speaking.astype(bool)
        m &= (np.nan_to_num(h2m, nan=1e3, posinf=1e3) >= enter)
        if within_window:
            idx = np.zeros(n, dtype=bool)
            idx[:window_n] = True
            m &= idx
        return m

    # Global fallback first (full clip, no window)
    global_mask = _stable_mask(within_window=False)
    if global_mask.sum() == 0:
        global_mask = face_detected.astype(bool)
    bs_global_mask = global_mask & np.isfinite(bs_arr)
    res.fallback_global = PoseBaselines(
        pose_class="any",
        inner_lip=_channel(inner_lip[global_mask], "fallback_full_clip", mf_inner),
        outer_lip=_channel(outer_lip[global_mask], "fallback_full_clip", mf_outer),
        chin_to_nose=_channel(chin_to_nose[global_mask], "fallback_full_clip", mf_chin),
        jaw_open_bs=(
            _channel(bs_arr[bs_global_mask], "fallback_full_clip", mf_bs)
            if bs_available and bs_global_mask.any()
            else ChannelBaseline(median=0.0, mad=max(mf_bs, 0.01), source="unavailable")
        ),
    )

    min_samples = max(int(cal.get("min_samples_per_pose", int(0.5 * fps))), 5)
    window_mask = _stable_mask(within_window=True)

    for pc in ("frontal", "profile", "tilted_down"):
        pose_mask = window_mask & (pose_arr == pc)
        if int(pose_mask.sum()) < min_samples:
            # widen to full clip for this pose
            wide = _stable_mask(within_window=False) & (pose_arr == pc)
            if int(wide.sum()) >= min_samples:
                res.warnings.append(
                    f"calibration: pose '{pc}' not stable in first "
                    f"{cal['window_s']:.1f}s; using full-clip median"
                )
                wide_bs = wide & np.isfinite(bs_arr)
                pb = PoseBaselines(
                    pose_class=pc,
                    inner_lip=_channel(inner_lip[wide], "full_clip_stable", mf_inner),
                    outer_lip=_channel(outer_lip[wide], "full_clip_stable", mf_outer),
                    chin_to_nose=_channel(chin_to_nose[wide], "full_clip_stable", mf_chin),
                    jaw_open_bs=(
                        _channel(bs_arr[wide_bs], "full_clip_stable", mf_bs)
                        if bs_available and wide_bs.any()
                        else ChannelBaseline(median=0.0, mad=max(mf_bs, 0.01), source="unavailable")
                    ),
                )
                res.per_pose[pc] = pb
            else:
                res.warnings.append(
                    f"calibration: pose '{pc}' has no stable samples; falling back to global"
                )
            continue
        pose_mask_bs = pose_mask & np.isfinite(bs_arr)
        pb = PoseBaselines(
            pose_class=pc,
            inner_lip=_channel(inner_lip[pose_mask], "calibration_window", mf_inner),
            outer_lip=_channel(outer_lip[pose_mask], "calibration_window", mf_outer),
            chin_to_nose=_channel(chin_to_nose[pose_mask], "calibration_window", mf_chin),
            jaw_open_bs=(
                _channel(bs_arr[pose_mask_bs], "calibration_window", mf_bs)
                if bs_available and pose_mask_bs.any()
                else ChannelBaseline(median=0.0, mad=max(mf_bs, 0.01), source="unavailable")
            ),
        )
        res.per_pose[pc] = pb

    return res


def write_baselines(out_dir: Path, result: CalibrationResult) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "baselines.json", "w") as f:
        json.dump(result.to_json(), f, indent=2)
