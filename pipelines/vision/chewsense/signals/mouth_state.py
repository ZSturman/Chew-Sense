"""Per-frame mouth-state FSM + cycle detection.

Inputs are the already-smoothed inner-lip and jaw-translation-proxy signals
plus per-pose baselines from :mod:`signals.mouth_calibration`. We build a
pose-aware *unified open-magnitude* (z-scored against the per-pose resting
baseline), then run a small finite-state machine over it:

    closed -> opening -> open -> closing -> closed

The FSM uses hysteretic z-score thresholds plus a sign check on the derivative
so brief jitter cannot ping-pong between states. While the face is occluded or
the pose is unclassifiable, state collapses to ``unknown`` but the FSM keeps
ticking so timing is continuous.

A *cycle* is a contiguous run of ``closed -> opening -> {open,} -> closing ->
closed``. Each cycle is classified as one of:

- ``visible_open`` — lip aperture clearly opened (high inner/outer lip ratio
  AND outer-lip signal rose above baseline);
- ``closed_lip`` — lips stayed mostly sealed but the chin-to-nose proxy peaked
  above baseline (teeth/jaw separated behind closed lips);
- ``ambiguous`` — neither test is decisive; still emitted but flagged.

Cycles are rejected if a large fraction of their frames overlap with talking,
occlusion, or unclassified pose. A small "ML refinement" hook
(:mod:`models.cycle_classifier`) is consulted but currently echoes the
heuristic — it is a drop-in seam for Phase 2 training.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..events.schema import MouthCycle, MouthCycleKind, MouthState, MouthStateSegment
from ..models.cycle_classifier import predict_kind
from .head_pose import trust_channels
from .jaw_signal import bandpass, build_jaw_proxy, smooth
from .mouth_calibration import CalibrationResult


@dataclass
class MouthStateOutputs:
    state_per_frame: np.ndarray            # int codes; see _STATE_CODES
    magnitude: np.ndarray                  # unified open magnitude (z-score)
    inner_z: np.ndarray                    # z-score of inner_lip vs per-pose rest
    jaw_proxy_residual: np.ndarray         # signed residual (smoothed)
    jaw_proxy_bp: np.ndarray               # bandpassed proxy
    jaw_proxy_z: np.ndarray                # z-score of residual vs per-pose rest
    jaw_open_bs_z: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    used_channels: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint8))
    segments: list[MouthStateSegment] = field(default_factory=list)
    cycles: list[MouthCycle] = field(default_factory=list)


_STATE_CODES = {
    "closed": 0,
    "opening": 1,
    "open": 2,
    "closing": 3,
    "unknown": 4,
}
_CODE_TO_STATE: dict[int, MouthState] = {v: k for k, v in _STATE_CODES.items()}  # type: ignore[misc]


def _pose_rest(cal: CalibrationResult, pose_class: str, channel: str) -> tuple[float, float]:
    pb = cal.get(pose_class)
    ch = getattr(pb, channel)
    if ch.n_samples == 0:
        # fall back to global
        fb = getattr(cal.fallback_global, channel)
        return fb.median, max(fb.mad, 1e-4)
    return ch.median, max(ch.mad, 1e-4)


def _compute_channel_z(
    signal: np.ndarray,
    pose_class: list[str],
    cal: CalibrationResult,
    channel: str,
) -> np.ndarray:
    n = len(signal)
    out = np.zeros(n, dtype=np.float32)
    if n == 0:
        return out
    # Cache per-pose so we don't dict-lookup on every frame
    cache: dict[str, tuple[float, float]] = {}
    for i in range(n):
        pc = pose_class[i] if i < len(pose_class) else "other"
        if pc not in cache:
            cache[pc] = _pose_rest(cal, pc, channel)
        med, mad = cache[pc]
        out[i] = (signal[i] - med) / (1.4826 * mad + 1e-6)
    return out


def _derivative(x: np.ndarray) -> np.ndarray:
    if len(x) < 2:
        return np.zeros_like(x)
    d = np.zeros_like(x)
    d[1:-1] = (x[2:] - x[:-2]) / 2.0
    d[0] = x[1] - x[0]
    d[-1] = x[-1] - x[-2]
    return d


def _run_fsm(
    magnitude: np.ndarray,
    derivative: np.ndarray,
    pose_class: list[str],
    face_detected: np.ndarray,
    occluded: np.ndarray,
    cfg: dict,
    channels_present: np.ndarray | None = None,
    occlusion_hold_frames: int = 0,
) -> np.ndarray:
    """Per-frame mouth-state FSM.

    A frame is "usable" if the face is visible AND at least one trusted
    aperture channel produced a finite value. ``channels_present`` is a bitmask
    per frame (1=jaw_open_bs, 2=inner_lip, 4=jaw_proxy). If any of those bits
    overlap with the pose-trust set, the frame contributes to the FSM. This is
    what stops "other"-pose frames from collapsing the entire state stream to
    ``unknown`` — the blendshape channel is trusted at every pose.

    When ``occlusion_hold_frames > 0``, an unusable run shorter than that many
    frames keeps emitting the most recent committed state (hand-over-mouth and
    similar transient drops no longer flicker to ``unknown``).
    """
    ms = cfg["mouth_state"]
    open_enter = float(ms["open_enter_z"])
    open_hold = float(ms["open_hold_z"])
    close_enter = float(ms["close_enter_z"])
    deriv_open = float(ms["deriv_open_per_frame"])
    deriv_close = -float(ms["deriv_open_per_frame"])
    deriv_quiet = float(ms["deriv_quiet_per_frame"])
    min_state_frames = max(1, int(ms.get("min_state_frames", 2)))

    n = len(magnitude)
    out = np.full(n, _STATE_CODES["unknown"], dtype=np.int8)
    state: str = "closed"
    state_frames = 0
    last_committed = state
    last_committed_frames = 0
    # Phase B: occlusion hold — keep prior state across brief gaps.
    last_usable_code: int | None = None
    hold_run = 0

    for i in range(n):
        pc = pose_class[i] if i < len(pose_class) else "other"
        trust = trust_channels(pc)
        if channels_present is not None:
            ch = int(channels_present[i])
            has_trusted = (
                (bool(ch & 1) and trust.get("jaw_open_bs", False))
                or (bool(ch & 2) and trust.get("inner_lip", False))
                or (bool(ch & 4) and trust.get("jaw_proxy", False))
            )
        else:
            has_trusted = trust.get("inner_lip", False) or trust.get("jaw_proxy", False)
        usable = bool(face_detected[i]) and not bool(occluded[i]) and has_trusted
        if not usable:
            hold_run += 1
            if (
                occlusion_hold_frames > 0
                and hold_run <= occlusion_hold_frames
                and last_usable_code is not None
            ):
                # Brief occlusion: hold previous committed state.
                out[i] = last_usable_code
            else:
                state = "unknown"
                state_frames = 0
                out[i] = _STATE_CODES["unknown"]
            continue
        hold_run = 0

        mag = float(magnitude[i])
        d = float(derivative[i])
        next_state = state

        if state == "closed":
            if d > deriv_open and mag > open_enter * 0.5:
                next_state = "opening"
        elif state == "opening":
            if mag >= open_hold and abs(d) <= deriv_quiet:
                next_state = "open"
            elif d < deriv_close and mag < open_enter:
                # peak collapsed early -> closing without sustained open
                next_state = "closing"
        elif state == "open":
            if d < deriv_close:
                next_state = "closing"
        elif state == "closing":
            if mag <= close_enter and abs(d) <= deriv_quiet:
                next_state = "closed"
            elif d > deriv_open and mag > open_enter:
                # bounce back up
                next_state = "opening"
        else:  # unknown
            next_state = "closed" if mag <= open_enter else "opening"

        if next_state == state:
            state_frames += 1
        else:
            # Honour minimum-duration only when leaving the *committed* state
            # to a brand-new one (this prevents single-frame flicker).
            if state_frames < min_state_frames and last_committed == next_state:
                # collapse back to previous committed state
                state = last_committed
                state_frames = last_committed_frames + 1
            else:
                last_committed = state
                last_committed_frames = state_frames
                state = next_state
                state_frames = 1
        out[i] = _STATE_CODES[state]
        last_usable_code = int(out[i])
    return out


def _segments_from_codes(codes: np.ndarray, ts: np.ndarray, pose_class: list[str]) -> list[MouthStateSegment]:
    n = len(codes)
    out: list[MouthStateSegment] = []
    if n == 0:
        return out
    i = 0
    while i < n:
        j = i
        c = int(codes[i])
        while j < n and int(codes[j]) == c:
            j += 1
        start_ts = float(ts[i])
        end_ts = float(ts[min(j, n - 1)])
        out.append(MouthStateSegment(
            state=_CODE_TO_STATE[c],
            start_ts=start_ts,
            end_ts=end_ts,
            duration_s=max(0.0, end_ts - start_ts),
            pose_class=pose_class[i] if i < len(pose_class) else "other",
        ))
        i = j
    return out


def _detect_cycles(
    codes: np.ndarray,
    ts: np.ndarray,
    magnitude: np.ndarray,
    inner_z: np.ndarray,
    proxy_z: np.ndarray,
    outer_lip_open: np.ndarray,
    outer_lip_rest: float,
    outer_lip_mad: float,
    lip_aperture_ratio: np.ndarray,
    pose_class: list[str],
    talking_flags: np.ndarray,
    occluded_flags: np.ndarray,
    cfg: dict,
    bs_z: np.ndarray | None = None,
) -> list[MouthCycle]:
    """Walk the FSM trace and emit one ``MouthCycle`` per closed->...->closed run."""
    n = len(codes)
    if n == 0:
        return []
    cyc_cfg = cfg["cycle"]
    min_dur_s = float(cyc_cfg["min_duration_s"])
    max_dur_s = float(cyc_cfg["max_duration_s"])
    talking_reject = float(cyc_cfg["talking_reject_overlap"])
    occluded_reject = float(cyc_cfg["occluded_reject_overlap"])
    pose_other_reject = float(cyc_cfg["pose_other_reject_overlap"])
    visible_ratio_min = float(cyc_cfg["visible_open_ratio_min"])
    visible_outer_z_min = float(cyc_cfg["visible_outer_lip_z_min"])
    closed_lip_z_min = float(cyc_cfg["closed_lip_jaw_proxy_z_min"])
    min_peak_z = float(cyc_cfg["min_peak_magnitude_z"])

    closed_code = _STATE_CODES["closed"]
    opening_code = _STATE_CODES["opening"]
    closing_code = _STATE_CODES["closing"]
    open_code = _STATE_CODES["open"]
    unknown_code = _STATE_CODES["unknown"]

    cycles: list[MouthCycle] = []
    cycle_id = 0
    i = 0
    while i < n:
        # find an 'opening' that follows a 'closed' frame
        if int(codes[i]) != opening_code or (i > 0 and int(codes[i - 1]) not in (closed_code, unknown_code)):
            i += 1
            continue
        start_idx = i
        # walk until we return to 'closed' or hit 'unknown' / EOF
        j = i + 1
        saw_open_or_closing = False
        while j < n:
            c = int(codes[j])
            if c == closed_code:
                break
            if c == unknown_code:
                break
            if c in (open_code, closing_code):
                saw_open_or_closing = True
            j += 1
        if not saw_open_or_closing:
            i = max(i + 1, j)
            continue
        end_idx = min(j, n - 1)
        if end_idx <= start_idx + 1:
            i = end_idx + 1
            continue

        # peak frame = argmax magnitude inside [start, end]
        peak_local = int(np.argmax(magnitude[start_idx:end_idx + 1]))
        peak_idx = start_idx + peak_local
        peak_mag = float(magnitude[peak_idx])

        dur_s = float(ts[end_idx] - ts[start_idx])
        if dur_s < min_dur_s or dur_s > max_dur_s or peak_mag < min_peak_z:
            i = end_idx + 1
            continue

        seg = slice(start_idx, end_idx + 1)
        talking_overlap = float(np.mean(talking_flags[seg].astype(np.float32))) if (end_idx + 1) > start_idx else 0.0
        occluded_overlap = float(np.mean(occluded_flags[seg].astype(np.float32))) if (end_idx + 1) > start_idx else 0.0
        pc_arr = np.array(pose_class[start_idx:end_idx + 1], dtype=object)
        pose_other_overlap = float(np.mean(pc_arr == "other")) if pc_arr.size else 0.0

        if (talking_overlap >= talking_reject
                or occluded_overlap >= occluded_reject
                or pose_other_overlap >= pose_other_reject):
            i = end_idx + 1
            continue

        # peak pose = most common pose during cycle
        pose_at_peak = pose_class[peak_idx] if peak_idx < len(pose_class) else "other"

        # Outer-lip z at peak
        outer_z_peak = float(
            (outer_lip_open[peak_idx] - outer_lip_rest) / (1.4826 * max(outer_lip_mad, 1e-4) + 1e-6)
        )
        aperture_peak = float(lip_aperture_ratio[peak_idx])
        proxy_z_peak = float(proxy_z[peak_idx])
        bs_z_peak = float(bs_z[peak_idx]) if (bs_z is not None and peak_idx < len(bs_z)) else 0.0

        # Heuristic kind. The MediaPipe `jawOpen` blendshape disambiguates
        # closed-lip chewing (high bs_z, low aperture) from talking/visible-open
        # (high bs_z AND high aperture).
        if aperture_peak >= visible_ratio_min and outer_z_peak >= visible_outer_z_min:
            kind: MouthCycleKind = "visible_open"
            confidence = 0.8
        elif bs_z_peak >= closed_lip_z_min and aperture_peak < visible_ratio_min:
            # Blendshape says the jaw clearly articulated open, but the 2D lip
            # aperture stayed low → sealed-lip chew. This is the case the
            # legacy chin_to_nose proxy was *trying* to catch but often missed.
            kind = "closed_lip"
            confidence = 0.75
        elif proxy_z_peak >= closed_lip_z_min and aperture_peak < visible_ratio_min:
            kind = "closed_lip"
            confidence = 0.6
        else:
            kind = "ambiguous"
            confidence = 0.4

        evidence = {
            "start_frame": int(start_idx),
            "peak_frame": int(peak_idx),
            "end_frame": int(end_idx),
            "peak_magnitude_z": peak_mag,
            "inner_lip_z_peak": float(inner_z[peak_idx]),
            "jaw_proxy_z_peak": proxy_z_peak,
            "jaw_open_bs_z_peak": bs_z_peak,
            "outer_lip_z_peak": outer_z_peak,
            "lip_aperture_ratio_peak": aperture_peak,
            "talking_overlap": talking_overlap,
            "occluded_overlap": occluded_overlap,
            "pose_other_overlap": pose_other_overlap,
            "heuristic_kind": kind,
        }

        # ML refinement hook (stub today)
        refined_kind, refined_conf = predict_kind(evidence, cfg)
        if refined_kind is not None:
            kind = refined_kind
            confidence = refined_conf if refined_conf is not None else confidence

        cycle_id += 1
        cycles.append(MouthCycle(
            id=f"cycle_{cycle_id:04d}",
            start_ts=float(ts[start_idx]),
            peak_ts=float(ts[peak_idx]),
            end_ts=float(ts[end_idx]),
            duration_s=dur_s,
            kind=kind,
            peak_magnitude=peak_mag,
            pose_class=pose_at_peak,
            confidence=float(confidence),
            evidence=evidence,
        ))
        i = end_idx + 1
    return cycles


def detect_mouth_states(
    *,
    fps: float,
    timestamps: np.ndarray,
    inner_lip_open: np.ndarray,
    outer_lip_open: np.ndarray,
    lip_aperture_ratio: np.ndarray,
    chin_to_nose: np.ndarray,
    pose_class: list[str],
    face_detected: np.ndarray,
    occluded_flags: np.ndarray,
    talking_flags: np.ndarray,
    calibration: CalibrationResult,
    cfg: dict,
    jaw_open_bs: np.ndarray | None = None,
) -> MouthStateOutputs:
    """Run the full Phase 1 mouth-state + cycle pipeline.

    ``jaw_open_bs``: optional MediaPipe `jawOpen` blendshape stream in [0, 1].
    When provided, it becomes the *primary* mouth-open channel — it is robust
    to head pose AND to lip closure (the blendshape is fit from the 3D mesh,
    not 2D lip distance). The legacy ``inner_lip_open`` and ``chin_to_nose``
    channels continue to serve as corroborating sources, which gives the FSM
    a graceful degradation path when the blendshape stream is unavailable.
    """

    n = len(timestamps)
    if n == 0:
        return MouthStateOutputs(
            state_per_frame=np.zeros(0, dtype=np.int8),
            magnitude=np.zeros(0, dtype=np.float32),
            inner_z=np.zeros(0, dtype=np.float32),
            jaw_proxy_residual=np.zeros(0, dtype=np.float32),
            jaw_proxy_bp=np.zeros(0, dtype=np.float32),
            jaw_proxy_z=np.zeros(0, dtype=np.float32),
        )

    # Smooth raw channels (use existing config)
    inner_lip_s = smooth(inner_lip_open.astype(np.float32), cfg)

    # Per-pose z-scores
    inner_z = _compute_channel_z(inner_lip_s, pose_class, calibration, "inner_lip")

    # Jaw proxy: residual against the per-pose chin->nose rest (use frontal as the
    # general fallback). Build a per-frame residual then smooth + bandpass it.
    rest_med = np.zeros(n, dtype=np.float32)
    rest_mad = np.zeros(n, dtype=np.float32)
    for i in range(n):
        med, mad = _pose_rest(calibration, pose_class[i] if i < len(pose_class) else "other", "chin_to_nose")
        rest_med[i] = med
        rest_mad[i] = mad
    proxy_residual = (chin_to_nose.astype(np.float32) - rest_med).astype(np.float32)
    proxy_residual_s = smooth(proxy_residual, cfg)
    proxy_bp = bandpass(proxy_residual_s, fps, cfg)
    proxy_z = (proxy_residual_s / (1.4826 * np.maximum(rest_mad, 1e-4) + 1e-6)).astype(np.float32)

    # ---- Phase 1.5: blendshape `jawOpen` z-score channel (primary signal) ----
    if jaw_open_bs is None:
        bs_arr = np.full(n, np.nan, dtype=np.float32)
        bs_finite = np.zeros(n, dtype=bool)
        bs_z = np.zeros(n, dtype=np.float32)
    else:
        bs_arr = np.asarray(jaw_open_bs, dtype=np.float32)
        bs_finite = np.isfinite(bs_arr)
        # NaN-safe smoothing: forward/backward-fill, smooth, re-mask
        if bs_finite.any():
            filled = bs_arr.copy()
            # forward fill, then backward fill — preserves shape; gaps stay
            # small because face detection has its own gap-filling upstream.
            last = 0.0
            for i in range(n):
                if bs_finite[i]:
                    last = filled[i]
                else:
                    filled[i] = last
            # back-fill leading NaNs (no prior value yet)
            first = next((float(bs_arr[i]) for i in range(n) if bs_finite[i]), 0.0)
            for i in range(n):
                if not bs_finite[i] and filled[i] == 0.0:
                    filled[i] = first
                else:
                    break
            bs_smooth = smooth(filled.astype(np.float32), cfg)
            bs_z = _compute_channel_z(bs_smooth, pose_class, calibration, "jaw_open_bs")
            # Zero-out where blendshape was truly absent so downstream
            # consensus doesn't pick up a stale value.
            bs_z = np.where(bs_finite, bs_z, 0.0).astype(np.float32)
        else:
            bs_z = np.zeros(n, dtype=np.float32)

    # Per-frame channel-availability bitmask: 1=blendshape, 2=inner_lip, 4=jaw_proxy.
    # A channel is "available" only when it produced a finite, calibrated value.
    inner_finite = np.isfinite(inner_z) & (inner_lip_open > 1e-6)
    proxy_finite = np.isfinite(proxy_z)
    channels_present = (
        bs_finite.astype(np.uint8) * 1
        + inner_finite.astype(np.uint8) * 2
        + proxy_finite.astype(np.uint8) * 4
    )

    # Unified open-magnitude: pose-aware weighted consensus of trusted channels.
    # Weights: jaw_open_bs=1.0 (most trustworthy), inner_lip=0.6, jaw_proxy=0.4.
    # The weighted mean is more stable than max() under signal-specific noise.
    magnitude = np.zeros(n, dtype=np.float32)
    for i in range(n):
        pc = pose_class[i] if i < len(pose_class) else "other"
        trust = trust_channels(pc)
        vals: list[float] = []
        weights: list[float] = []
        if trust.get("jaw_open_bs", False) and bs_finite[i]:
            vals.append(float(bs_z[i])); weights.append(1.0)
        if trust.get("inner_lip", False) and inner_finite[i]:
            vals.append(float(inner_z[i])); weights.append(0.6)
        if trust.get("jaw_proxy", False) and proxy_finite[i]:
            vals.append(float(proxy_z[i])); weights.append(0.4)
        if vals:
            w = np.asarray(weights, dtype=np.float32)
            magnitude[i] = float(np.dot(vals, w) / w.sum())
        else:
            magnitude[i] = 0.0

    derivative = _derivative(magnitude)
    hold_s = float(cfg.get("occlusion", {}).get("hold_s", 0.5))
    occlusion_hold_frames = max(0, int(round(hold_s * fps)))
    codes = _run_fsm(
        magnitude, derivative, pose_class, face_detected, occluded_flags, cfg,
        channels_present=channels_present,
        occlusion_hold_frames=occlusion_hold_frames,
    )
    segments = _segments_from_codes(codes, timestamps, pose_class)

    # Outer-lip baseline for cycle classification (frontal pose; fall back to global)
    fb = calibration.get("frontal")
    outer_rest = fb.outer_lip.median if fb.outer_lip.n_samples else calibration.fallback_global.outer_lip.median
    outer_mad = fb.outer_lip.mad if fb.outer_lip.n_samples else calibration.fallback_global.outer_lip.mad

    cycles = _detect_cycles(
        codes=codes,
        ts=timestamps,
        magnitude=magnitude,
        inner_z=inner_z,
        proxy_z=proxy_z,
        outer_lip_open=outer_lip_open.astype(np.float32),
        outer_lip_rest=float(outer_rest),
        outer_lip_mad=float(outer_mad),
        lip_aperture_ratio=lip_aperture_ratio.astype(np.float32),
        pose_class=pose_class,
        talking_flags=talking_flags,
        occluded_flags=occluded_flags,
        cfg=cfg,
        bs_z=bs_z,
    )

    return MouthStateOutputs(
        state_per_frame=codes,
        magnitude=magnitude,
        inner_z=inner_z,
        jaw_proxy_residual=proxy_residual_s,
        jaw_proxy_bp=proxy_bp,
        jaw_proxy_z=proxy_z,
        jaw_open_bs_z=bs_z,
        used_channels=channels_present,
        segments=segments,
        cycles=cycles,
    )


def state_name(code: int) -> MouthState:
    return _CODE_TO_STATE.get(int(code), "unknown")


__all__ = [
    "MouthStateOutputs",
    "detect_mouth_states",
    "state_name",
    "_STATE_CODES",
]
