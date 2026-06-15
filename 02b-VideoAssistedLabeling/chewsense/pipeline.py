"""End-to-end pipeline. Runs feature extraction over a video, derives signals,
detects chews and bites, builds episodes, and writes outputs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
from tqdm import tqdm

from .annotate import render_annotated_video
from .events.schema import FrameRecord, SessionCounters, Span, VideoEvents
from .events.state_machine import _BiteIn, _ChewIn, build_episodes
from .export import write_outputs
from .features.audio import compute_audio_frame_features
from .features.face import FaceExtractor
from .features.hands import HandsExtractor
from .ingest import extract_audio, iter_frames, probe
from .quality.baselines import Baseline, update_baseline_from_report
from .quality.metrics import compute_quality_report
from .signals.bite_detector import detect_bites
from .signals.head_pose import classify_pose
from .signals.jaw_signal import bandpass, detect_chews, smooth
from .signals.mouth_calibration import calibrate, write_baselines
from .signals.mouth_state import detect_mouth_states, state_name


def _safe(x: float) -> float:
    """Coerce NaN/inf blendshape values to 0.0 for the serialized record."""
    return float(x) if np.isfinite(x) else 0.0


def run(
    video_path: str | Path,
    out_dir: str | Path,
    cfg: dict,
    *,
    user_id: str | None = None,
    coarse_intervals: Iterable[object] | None = None,
) -> VideoEvents:
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    if user_id is None and cfg.get("baselines", {}).get("enabled", False):
        user_id = cfg["baselines"]["default_user"]
    meta = probe(video_path)
    fps = meta.fps

    # Pass 1: feature extraction
    n = meta.n_frames if meta.n_frames > 0 else None

    timestamps: list[float] = []
    jaw_open: list[float] = []
    mouth_width: list[float] = []
    head_yaw: list[float] = []
    head_pitch: list[float] = []
    h2m: list[float] = []
    n_hands: list[int] = []
    face_detected: list[bool] = []
    face_scale: list[float] = []
    inner_lip_open_l: list[float] = []
    outer_lip_open_l: list[float] = []
    lip_aperture_ratio_l: list[float] = []
    chin_to_nose_l: list[float] = []
    # Phase 1.5: blendshape + face-confidence streams
    jaw_open_bs_l: list[float] = []
    mouth_close_bs_l: list[float] = []
    mouth_funnel_bs_l: list[float] = []
    mouth_pucker_bs_l: list[float] = []
    mouth_roll_lower_bs_l: list[float] = []
    mouth_roll_upper_bs_l: list[float] = []
    mouth_press_left_bs_l: list[float] = []
    mouth_press_right_bs_l: list[float] = []
    mouth_smile_left_bs_l: list[float] = []
    mouth_smile_right_bs_l: list[float] = []
    face_confidence_l: list[float] = []
    overlay_meta: dict[int, dict] = {}

    with FaceExtractor(cfg) as face_ext, HandsExtractor(cfg) as hands_ext:
        iterator = iter_frames(video_path)
        if n is not None:
            iterator = tqdm(iterator, total=n, desc="features", unit="frame")
        for idx, ts, frame in iterator:
            ff = face_ext.process(frame)
            if ff.face_detected:
                hf = hands_ext.process(frame, ff.mouth_center_xy, ff.face_scale)
            else:
                hf = hands_ext.process(frame, None, 0.0)

            timestamps.append(ts)
            face_detected.append(ff.face_detected)
            face_scale.append(ff.face_scale)
            jaw_open.append(ff.jaw_open if ff.face_detected else 0.0)
            mouth_width.append(ff.mouth_width if ff.face_detected else 0.0)
            head_yaw.append(ff.head_yaw if ff.face_detected else 0.0)
            head_pitch.append(ff.head_pitch if ff.face_detected else 0.0)
            h2m.append(hf.min_distance)
            n_hands.append(hf.n_hands)
            inner_lip_open_l.append(ff.inner_lip_open if ff.face_detected else 0.0)
            outer_lip_open_l.append(ff.outer_lip_open if ff.face_detected else 0.0)
            lip_aperture_ratio_l.append(ff.lip_aperture_ratio if ff.face_detected else 0.0)
            chin_to_nose_l.append(ff.chin_to_nose if ff.face_detected else 0.0)
            # Blendshapes: NaN when face missing (consensus path is NaN-aware)
            nan = float("nan")
            jaw_open_bs_l.append(ff.jaw_open_bs if ff.face_detected else nan)
            mouth_close_bs_l.append(ff.mouth_close_bs if ff.face_detected else nan)
            mouth_funnel_bs_l.append(ff.mouth_funnel_bs if ff.face_detected else nan)
            mouth_pucker_bs_l.append(ff.mouth_pucker_bs if ff.face_detected else nan)
            mouth_roll_lower_bs_l.append(ff.mouth_roll_lower_bs if ff.face_detected else nan)
            mouth_roll_upper_bs_l.append(ff.mouth_roll_upper_bs if ff.face_detected else nan)
            mouth_press_left_bs_l.append(ff.mouth_press_left_bs if ff.face_detected else nan)
            mouth_press_right_bs_l.append(ff.mouth_press_right_bs if ff.face_detected else nan)
            mouth_smile_left_bs_l.append(ff.mouth_smile_left_bs if ff.face_detected else nan)
            mouth_smile_right_bs_l.append(ff.mouth_smile_right_bs if ff.face_detected else nan)
            face_confidence_l.append(ff.face_confidence if ff.face_detected else 0.0)

            overlay_meta[idx] = {
                "mouth_xy": ff.mouth_center_xy if ff.face_detected else None,
                "lip_a": ff.upper_lip_xy if ff.face_detected else None,
                "lip_b": ff.lower_lip_xy if ff.face_detected else None,
                "bbox": ff.bbox_xywh if ff.face_detected else None,
                "hands_xy": hf.hand_points_xy,
                "chin_xy": ff.chin_xy if ff.face_detected else None,
                "nose_xy": ff.nose_xy if ff.face_detected else None,
            }

    ts_arr = np.array(timestamps, dtype=np.float64)
    jaw = np.array(jaw_open, dtype=np.float32)
    h2m_arr = np.array(h2m, dtype=np.float32)
    h2m_arr[~np.isfinite(h2m_arr)] = 1e3  # sentinel
    face_det = np.array(face_detected, dtype=bool)
    face_scale_arr = np.array(face_scale, dtype=np.float32)

    # Audio
    audio_pair = extract_audio(video_path, sample_rate=cfg["ingest"]["audio_sample_rate"])
    if audio_pair is not None:
        audio, sr = audio_pair
        audio_present = True
    else:
        audio = np.zeros(0, dtype=np.float32)
        sr = cfg["ingest"]["audio_sample_rate"]
        audio_present = False
    audio_feats = compute_audio_frame_features(audio, sr, ts_arr, cfg)
    is_speaking = np.array([f.is_speaking for f in audio_feats], dtype=bool)
    audio_rms = np.array([f.rms for f in audio_feats], dtype=np.float32)

    # Signals
    jaw_smooth = smooth(jaw, cfg)
    jaw_bp = bandpass(jaw_smooth, fps, cfg)

    # Detect chews
    chew_cands = detect_chews(jaw_smooth, jaw_bp, fps, cfg)

    # Detect bites
    bite_cands = detect_bites(h2m_arr, jaw_smooth, ts_arr, fps, cfg)

    # Human coarse chewing intervals are the authority for event generation.
    # Mouth-state inference still runs across every usable frame, but chew and
    # food-entry candidates outside the reviewed chewing spans are discarded.
    coarse = [
        interval for interval in (coarse_intervals or [])
        if getattr(interval, "label", None) == "chewing"
    ]
    if coarse:
        padding = float(cfg.get("coarse_label_gate", {}).get("padding_s", 0.15))

        def inside_coarse(ts: float) -> bool:
            return any(
                float(interval.start_ts) - padding <= ts <= float(interval.end_ts) + padding
                for interval in coarse
            )

        chew_cands = [
            candidate for candidate in chew_cands
            if inside_coarse(float(ts_arr[candidate.peak_idx]))
        ]
        bite_cands = [
            candidate for candidate in bite_cands
            if inside_coarse(float(ts_arr[candidate.frame_idx]))
        ]

    # Optional gate: chew-head classifier P(chewing) per frame
    chew_prob: np.ndarray | None = None
    if cfg.get("chew_head", {}).get("enabled", False):
        try:
            from .training.infer import is_available, predict
            from .features.extract import FEATURE_COLS
            if is_available(cfg):
                d_jaw = np.zeros_like(jaw_smooth); d_jaw[1:] = np.diff(jaw_smooth)
                feature_table = {
                    "jaw_open": jaw,
                    "jaw_open_smooth": jaw_smooth,
                    "jaw_open_bp": jaw_bp,
                    "d_jaw_open": d_jaw,
                    "mouth_width": np.array(mouth_width, dtype=np.float32),
                    "head_yaw": np.array(head_yaw, dtype=np.float32),
                    "head_pitch": np.array(head_pitch, dtype=np.float32),
                    "hand_to_mouth_min": h2m_arr,
                    "n_hands": np.array(n_hands, dtype=np.float32),
                    "audio_rms": audio_rms,
                    "is_speaking": is_speaking.astype(np.float32),
                    "face_detected": face_det.astype(np.float32),
                    "face_scale": face_scale_arr,
                }
                X = np.stack([feature_table[c] for c in FEATURE_COLS], axis=1)
                chew_prob = predict(X, cfg)
                # Filter chew candidates whose peak prob is below threshold
                thr = float(cfg["chew_head"]["threshold"])
                chew_cands = [
                    c for c in chew_cands
                    if chew_prob[c.peak_idx] >= thr
                ]
        except Exception as e:
            print(f"[chew_head] gating skipped: {e}")

    # Occlusion: face not detected for >0.3s = occluded
    occ_min_frames = max(1, int(0.3 * fps))
    occluded_flags = np.zeros(len(ts_arr), dtype=bool)
    run_start = None
    for i, ok in enumerate(face_det):
        if not ok:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None and i - run_start >= occ_min_frames:
                occluded_flags[run_start:i] = True
            run_start = None
    if run_start is not None and len(face_det) - run_start >= occ_min_frames:
        occluded_flags[run_start:] = True

    # Phase B: hand-over-mouth + low-confidence + blendshape-missing occlusion.
    # OR'd into the existing face-not-detected mask. The FSM hold (downstream
    # in detect_mouth_states) bridges short occlusions so we don't flicker to
    # ``unknown`` mid-chew.
    from .signals.occlusion import detect_mouth_occlusion
    jaw_open_bs_arr = np.array(jaw_open_bs_l, dtype=np.float32)
    face_conf_arr = np.array(face_confidence_l, dtype=np.float32)
    mouth_occluded = detect_mouth_occlusion(
        fps=fps,
        face_detected=face_det,
        face_confidence=face_conf_arr,
        hand_to_mouth_min=h2m_arr,
        jaw_open_bs=jaw_open_bs_arr,
        cfg=cfg,
    )
    occluded_flags = occluded_flags | mouth_occluded

    talking_flags = is_speaking

    # ---------------- Phase 1: pose + calibration + mouth-state FSM ----------
    head_yaw_arr = np.array(head_yaw, dtype=np.float32)
    head_pitch_arr = np.array(head_pitch, dtype=np.float32)
    inner_lip_arr = np.array(inner_lip_open_l, dtype=np.float32)
    outer_lip_arr = np.array(outer_lip_open_l, dtype=np.float32)
    aperture_arr = np.array(lip_aperture_ratio_l, dtype=np.float32)
    chin_to_nose_arr = np.array(chin_to_nose_l, dtype=np.float32)
    # jaw_open_bs_arr / face_conf_arr were already built above for the
    # occlusion detector; reuse them here.

    pose_class = classify_pose(head_yaw_arr, head_pitch_arr, face_det, cfg)
    calibration = calibrate(
        fps=fps,
        inner_lip=inner_lip_arr,
        outer_lip=outer_lip_arr,
        chin_to_nose=chin_to_nose_arr,
        pose_class=pose_class,
        face_detected=face_det,
        occluded=occluded_flags,
        is_speaking=talking_flags,
        h2m=h2m_arr,
        cfg=cfg,
        jaw_open_bs=jaw_open_bs_arr,
    )
    mouth_out = detect_mouth_states(
        fps=fps,
        timestamps=ts_arr,
        inner_lip_open=inner_lip_arr,
        outer_lip_open=outer_lip_arr,
        lip_aperture_ratio=aperture_arr,
        chin_to_nose=chin_to_nose_arr,
        pose_class=pose_class,
        face_detected=face_det,
        occluded_flags=occluded_flags,
        talking_flags=talking_flags,
        calibration=calibration,
        cfg=cfg,
        jaw_open_bs=jaw_open_bs_arr,
    )
    cycle_counts = SessionCounters()
    for c in mouth_out.cycles:
        cycle_counts.total += 1
        if c.kind == "visible_open":
            cycle_counts.visible_open += 1
        elif c.kind == "closed_lip":
            cycle_counts.closed_lip += 1
        else:
            cycle_counts.ambiguous += 1
    # ------------------------------------------------------------------------

    # Convert chew candidates with overlap stats
    def _overlap_frac(a, b, flags):
        a = max(0, a); b = min(len(flags), b)
        if b <= a:
            return 0.0
        return float(np.mean(flags[a:b].astype(np.float32)))

    chews_in: list[_ChewIn] = []
    for c in chew_cands:
        chews_in.append(_ChewIn(
            start_idx=c.start_idx,
            peak_idx=c.peak_idx,
            end_idx=c.end_idx,
            start_ts=float(ts_arr[c.start_idx]),
            peak_ts=float(ts_arr[c.peak_idx]),
            end_ts=float(ts_arr[c.end_idx]),
            amplitude=c.amplitude,
            prominence=c.prominence,
            talking_overlap=_overlap_frac(c.start_idx, c.end_idx + 1, talking_flags),
            occluded_overlap=_overlap_frac(c.start_idx, c.end_idx + 1, occluded_flags),
        ))

    bites_in: list[_BiteIn] = []
    for b in bite_cands:
        bites_in.append(_BiteIn(
            frame_idx=b.frame_idx,
            ts=float(ts_arr[b.frame_idx]),
            confidence=b.confidence,
            evidence=b.evidence,
        ))

    episodes, talking_spans = build_episodes(
        chews=chews_in,
        bites=bites_in,
        timestamps=ts_arr,
        talking_flags=talking_flags,
        occluded_flags=occluded_flags,
        cfg=cfg,
    )

    # Build per-frame label with face-confidence weighting. The legacy version
    # emitted hardcoded confidences (0.5/0.6/0.8) regardless of how trustworthy
    # the underlying face detection was. We now scale by `face_conf_arr` and
    # de-rate when the multi-channel consensus had no trusted source.
    labels: list[str] = []
    label_conf: list[float] = []
    enter = cfg["hands"]["proximity_enter"]
    in_ep = np.zeros(len(ts_arr), dtype=bool)
    for ep in episodes:
        a = int(np.searchsorted(ts_arr, ep.start_ts))
        b = int(np.searchsorted(ts_arr, ep.end_ts))
        in_ep[a:b] = True
    used_ch_arr = mouth_out.used_channels if mouth_out.used_channels.size else np.zeros(len(ts_arr), dtype=np.uint8)
    for i in range(len(ts_arr)):
        fc = float(face_conf_arr[i]) if i < len(face_conf_arr) else 0.0
        # Frames with no trusted aperture channel get a confidence floor of
        # 0.3 so downstream consumers can filter them out.
        consensus_ok = int(used_ch_arr[i]) > 0
        base_scale = fc if consensus_ok else max(fc * 0.5, 0.3)
        if occluded_flags[i]:
            labels.append("occluded"); label_conf.append(0.5); continue
        if in_ep[i]:
            labels.append("chewing"); label_conf.append(min(1.0, 0.7 + 0.3 * base_scale)); continue
        if h2m_arr[i] < enter:
            labels.append("hand_to_mouth"); label_conf.append(min(1.0, 0.55 + 0.4 * base_scale)); continue
        if talking_flags[i]:
            labels.append("talking"); label_conf.append(min(1.0, 0.5 + 0.4 * base_scale)); continue
        labels.append("idle"); label_conf.append(min(1.0, 0.4 + 0.5 * base_scale))

    # FrameRecords
    frame_records: list[FrameRecord] = []
    for i in range(len(ts_arr)):
        bs_val = float(jaw_open_bs_arr[i]) if i < len(jaw_open_bs_arr) else float("nan")
        bs_val = bs_val if np.isfinite(bs_val) else 0.0
        bs_z_val = float(mouth_out.jaw_open_bs_z[i]) if i < len(mouth_out.jaw_open_bs_z) else 0.0
        used_ch = int(used_ch_arr[i]) if i < len(used_ch_arr) else 0
        frame_records.append(FrameRecord(
            frame_idx=i,
            ts=float(ts_arr[i]),
            face_detected=bool(face_det[i]),
            face_scale=float(face_scale_arr[i]),
            jaw_open=float(jaw[i]),
            jaw_open_smooth=float(jaw_smooth[i]),
            jaw_open_bp=float(jaw_bp[i]) if i < len(jaw_bp) else 0.0,
            mouth_width=float(mouth_width[i]),
            head_yaw=float(head_yaw[i]),
            head_pitch=float(head_pitch[i]),
            hand_to_mouth_min=float(h2m_arr[i]) if h2m_arr[i] < 1e3 else 1e3,
            n_hands=int(n_hands[i]),
            is_speaking=bool(is_speaking[i]),
            audio_rms=float(audio_rms[i]),
            occluded=bool(occluded_flags[i]),
            label=labels[i],     # type: ignore[arg-type]
            label_confidence=float(label_conf[i]),
            mouth_state=state_name(mouth_out.state_per_frame[i]),
            pose_class=pose_class[i] if i < len(pose_class) else "other",  # type: ignore[arg-type]
            inner_lip_open=float(inner_lip_arr[i]),
            outer_lip_open=float(outer_lip_arr[i]),
            lip_aperture_ratio=float(aperture_arr[i]),
            chin_to_nose=float(chin_to_nose_arr[i]),
            jaw_proxy_residual=float(mouth_out.jaw_proxy_residual[i]),
            open_magnitude_z=float(mouth_out.magnitude[i]),
            jaw_open_bs=bs_val,
            mouth_close_bs=_safe(mouth_close_bs_l[i]),
            mouth_funnel_bs=_safe(mouth_funnel_bs_l[i]),
            mouth_pucker_bs=_safe(mouth_pucker_bs_l[i]),
            mouth_roll_lower_bs=_safe(mouth_roll_lower_bs_l[i]),
            mouth_roll_upper_bs=_safe(mouth_roll_upper_bs_l[i]),
            mouth_press_left_bs=_safe(mouth_press_left_bs_l[i]),
            mouth_press_right_bs=_safe(mouth_press_right_bs_l[i]),
            mouth_smile_left_bs=_safe(mouth_smile_left_bs_l[i]),
            mouth_smile_right_bs=_safe(mouth_smile_right_bs_l[i]),
            jaw_open_bs_z=bs_z_val,
            face_confidence=float(face_conf_arr[i]) if i < len(face_conf_arr) else 0.0,
            used_channels=used_ch,
        ))

    # Spans (occlusion)
    occlusion_spans: list[Span] = []
    i = 0
    while i < len(occluded_flags):
        if occluded_flags[i]:
            j = i
            while j < len(occluded_flags) and occluded_flags[j]:
                j += 1
            occlusion_spans.append(Span(
                start_ts=float(ts_arr[i]),
                end_ts=float(ts_arr[min(j, len(ts_arr) - 1)]),
                kind="face_not_detected",
            ))
            i = j
        else:
            i += 1

    events = VideoEvents(
        video_id=video_path.stem,
        duration_s=meta.duration_s,
        fps=meta.fps,
        width=meta.width,
        height=meta.height,
        episodes=episodes,
        non_eating_mouth_spans=talking_spans,
        occlusion_spans=occlusion_spans,
        drinking_spans=[],
        mouth_state_segments=mouth_out.segments,
        mouth_cycles=mouth_out.cycles,
        cycle_counts=cycle_counts,
    )

    # Quality metrics + per-user baseline
    baseline = Baseline.load_or_new(user_id) if user_id else None
    face_det_frac = float(np.mean(face_det.astype(np.float32))) if len(face_det) else 0.0
    quality = compute_quality_report(
        events,
        timestamps=ts_arr,
        jaw_open_smooth=jaw_smooth,
        face_detected_fraction=face_det_frac,
        cfg=cfg,
        baseline=baseline,
        user_id=user_id,
    )
    events.quality = quality
    if user_id:
        update_baseline_from_report(user_id, quality, cfg)

    # Phase B: signal-health summary so reviewers can tell at a glance whether
    # the labels are trustworthy or the inputs were degraded.
    from .events.schema import SignalQuality
    n_frames = len(face_det)
    if n_frames > 0:
        used_ch_arr_q = mouth_out.used_channels
        unknown_frac = float(
            np.mean(mouth_out.state_per_frame == 4)
        )  # 4 = unknown code
        sq = SignalQuality(
            n_frames=int(n_frames),
            mean_face_confidence=float(np.mean(face_conf_arr)),
            median_face_confidence=float(np.median(face_conf_arr)),
            face_detected_fraction=face_det_frac,
            unknown_state_fraction=unknown_frac,
            occluded_fraction=float(np.mean(occluded_flags.astype(np.float32))),
            no_channel_fraction=float(np.mean(used_ch_arr_q == 0)),
            channel_coverage={
                "jaw_open_bs": float(np.mean((used_ch_arr_q & 1) > 0)),
                "inner_lip": float(np.mean((used_ch_arr_q & 2) > 0)),
                "jaw_proxy": float(np.mean((used_ch_arr_q & 4) > 0)),
            },
            talking_fraction=float(np.mean(is_speaking.astype(np.float32))),
            hand_to_mouth_fraction=float(
                np.mean((h2m_arr < float(cfg.get("occlusion", {}).get("h2m_close", 0.06)))
                        .astype(np.float32))
            ),
        )
        events.signal_quality = sq

    signals = {
        "ts": ts_arr.astype(np.float32),
        "jaw_open": jaw,
        "jaw_open_smooth": jaw_smooth,
        "jaw_open_bp": jaw_bp,
        "hand_to_mouth": h2m_arr,
        "is_speaking": is_speaking.astype(np.uint8),
        "audio_rms": audio_rms,
        "occluded": occluded_flags.astype(np.uint8),
        "inner_lip_open": inner_lip_arr,
        "outer_lip_open": outer_lip_arr,
        "lip_aperture_ratio": aperture_arr,
        "chin_to_nose": chin_to_nose_arr,
        "jaw_proxy_residual": mouth_out.jaw_proxy_residual,
        "jaw_proxy_bp": mouth_out.jaw_proxy_bp,
        "jaw_proxy_z": mouth_out.jaw_proxy_z,
        "open_magnitude_z": mouth_out.magnitude,
        "mouth_state": mouth_out.state_per_frame,
        # Phase 1.5: blendshape + consensus telemetry
        "jaw_open_bs": jaw_open_bs_arr,
        "jaw_open_bs_z": mouth_out.jaw_open_bs_z,
        "face_confidence": face_conf_arr,
        "used_channels": used_ch_arr,
    }

    # Persist baselines + calibration warnings under signals/
    write_baselines(out_dir / "signals", calibration)

    write_outputs(
        out_dir=out_dir,
        events=events,
        frame_records=frame_records,
        signals=signals,
        config=cfg,
        video_meta={
            "path": str(video_path),
            "fps": meta.fps,
            "n_frames": meta.n_frames,
            "width": meta.width,
            "height": meta.height,
            "duration_s": meta.duration_s,
            "audio_present": audio_present,
            "audio_sample_rate": sr,
        },
    )

    # Annotated video (Pass 2)
    render_annotated_video(
        video_path=video_path,
        out_path=out_dir / "annotated_video.mp4",
        frame_records=frame_records,
        overlay_meta=overlay_meta,
        events=events,
        cfg=cfg,
        width=meta.width,
        height=meta.height,
        fps=meta.fps,
    )

    return events
