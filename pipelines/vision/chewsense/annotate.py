"""Render fused mouth-cycle and food-entry proposal telemetry on video."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .events.schema import FrameRecord, VideoEvents
from .ingest import iter_frames

C_GREEN = (60, 200, 60)
C_YELLOW = (40, 220, 240)
C_RED = (60, 60, 230)
C_BLUE = (230, 160, 60)
C_ORANGE = (40, 140, 240)
C_GRAY = (160, 160, 160)
C_WHITE = (250, 250, 250)
C_PINK = (180, 100, 230)
C_CYAN = (220, 220, 80)
C_AMBER = (40, 180, 230)
C_SLATE = (110, 90, 60)


def _state_color(state: str) -> tuple[int, int, int]:
    return {
        "closed": C_SLATE,
        "opening": C_AMBER,
        "open": C_GREEN,
        "closing": C_ORANGE,
        "unknown": C_GRAY,
    }.get(state, C_GRAY)


def _cycle_color(kind: str) -> tuple[int, int, int]:
    return {
        "visible_open": C_GREEN,
        "closed_lip": C_CYAN,
        "ambiguous": C_YELLOW,
    }.get(kind, C_WHITE)


def _label_color(label: str) -> tuple[int, int, int]:
    return {
        "chewing": C_GREEN,
        "hand_to_mouth": C_BLUE,
        "food_entry": C_PINK,
        "drinking": C_ORANGE,
        "talking": C_YELLOW,
        "occluded": C_GRAY,
    }.get(label, C_WHITE)


def render_annotated_video(
    video_path: Path,
    out_path: Path,
    frame_records: list[FrameRecord],
    overlay_meta: dict[int, dict],
    events: VideoEvents,
    cfg: dict,
    width: int,
    height: int,
    fps: float,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ann_cfg = cfg.get("annotate", {})
    clean = bool(ann_cfg.get("clean", False))
    # In clean mode, default the noisy toggles to False; explicit values still win.
    def _flag(name: str, default_normal: bool, default_clean: bool) -> bool:
        return bool(ann_cfg.get(name, default_clean if clean else default_normal))
    show_strip = _flag("show_signal_strip", True, True)
    show_proxy_strip = _flag("show_jaw_proxy_strip", True, False)
    show_bbox = _flag("show_bbox", True, False)
    show_hand_lines = _flag("show_hand_lines", True, False)
    show_chew_marks = _flag("show_chew_marks", True, False)
    show_cycle_flash = _flag("show_cycle_flash", True, False)
    show_food_entry_pulse = _flag("show_food_entry_pulse", True, False)
    show_chin_nose_ref = _flag("show_chin_nose_ref", True, False)
    strip_h = 78 if show_strip else 0
    proxy_strip_h = 36 if (show_strip and show_proxy_strip) else 0
    out_w = width
    out_h = height + strip_h + proxy_strip_h
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, fps, (out_w, out_h))
    if not writer.isOpened():
        raise RuntimeError(f"failed to open output writer at {out_path}")

    rec_by_idx = {r.frame_idx: r for r in frame_records}
    chew_marks: dict[int, str] = {}
    food_entry_origin_frames: set[int] = set()
    pulse_frames = max(1, int(0.5 * fps))

    for cycle in events.mouth_cycles:
        for kind, key in (
            ("start", "start_frame"),
            ("peak", "peak_frame"),
            ("end", "end_frame"),
        ):
            frame = int(cycle.evidence.get(key, -1))
            if frame >= 0:
                chew_marks[frame] = kind
    if frame_records:
        frame_times = np.asarray([record.ts for record in frame_records])
        for proposal in events.food_entry_proposals:
            frame = int(np.argmin(np.abs(frame_times - proposal.food_entry_ts)))
            food_entry_origin_frames.add(frame_records[frame].frame_idx)

    # Phase 1: per-frame counters + cycle-completion flashes
    show_hud = cfg["annotate"].get("show_mouth_state_hud", True)
    flash_frames = max(1, int(cfg["annotate"].get("cycle_flash_seconds", 0.5) * fps))
    cycles_sorted = sorted(events.mouth_cycles, key=lambda c: c.end_ts)
    cycle_end_by_frame: dict[int, tuple[str, str]] = {}
    running_visible = 0
    running_closed_lip = 0
    running_ambiguous = 0
    counter_at_frame: dict[int, tuple[int, int, int]] = {}
    for c in cycles_sorted:
        end_frame = int(c.evidence.get("end_frame", -1))
        if end_frame < 0:
            continue
        cycle_end_by_frame[end_frame] = (c.kind, c.id)
        if c.kind == "visible_open":
            running_visible += 1
        elif c.kind == "closed_lip":
            running_closed_lip += 1
        else:
            running_ambiguous += 1
        counter_at_frame[end_frame] = (running_visible, running_closed_lip, running_ambiguous)

    def _counters_at(idx: int) -> tuple[int, int, int]:
        # find highest end_frame <= idx
        v = cl = am = 0
        for ef, counts in counter_at_frame.items():
            if ef <= idx:
                v, cl, am = counts
            else:
                break
        # dict iteration order is insertion order; counter_at_frame inserted in
        # ascending end_frame so above is safe.
        return v, cl, am

    flash_state: list[tuple[int, str]] = []  # (remaining_frames, kind)

    pulse_state: list[tuple[int, int]] = []
    n_total = len(frame_records)

    # Phase 1.5: precompute a per-frame mouth-state color row (10px tall) for
    # the bottom strip so reviewers can scrub and see the state over time.
    state_row_h = 10 if show_strip else 0
    state_row: np.ndarray | None = None
    if show_strip and n_total > 0:
        duration_s = max(events.duration_s, 1e-3)
        state_row = np.zeros((state_row_h, width, 3), dtype=np.uint8)
        for r in frame_records:
            x = int(r.ts / duration_s * width)
            if x < 0 or x >= width:
                continue
            col = _state_color(getattr(r, "mouth_state", "unknown"))
            state_row[:, x, 0] = col[0]
            state_row[:, x, 1] = col[1]
            state_row[:, x, 2] = col[2]

    for idx, ts, frame in iter_frames(video_path):
        if idx >= n_total + 5:
            break
        rec = rec_by_idx.get(idx)
        meta = overlay_meta.get(idx, {})
        canvas = np.zeros((out_h, out_w, 3), dtype=np.uint8)
        canvas[:height, :width] = frame

        if rec is not None:
            _draw_face_overlay(canvas, rec, meta, show_bbox=show_bbox,
                               show_chin_nose=show_chin_nose_ref)
            _draw_hands(canvas, meta, show_lines=show_hand_lines)
            _draw_status(canvas, rec, ts, clean=clean)

        if show_chew_marks and idx in chew_marks and meta.get("mouth_xy"):
            kind = chew_marks[idx]
            color = {"start": C_GREEN, "peak": C_YELLOW, "end": C_RED}[kind]
            cx, cy = meta["mouth_xy"]
            cv2.circle(canvas, (int(cx), int(cy)), 14, color, 3)
            cv2.putText(canvas, f"chew_{kind}", (int(cx) + 18, int(cy)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

        # Phase 1: cycle-completion flash + HUD
        if show_cycle_flash and idx in cycle_end_by_frame:
            flash_state.append((flash_frames, cycle_end_by_frame[idx][0]))
        new_flash: list[tuple[int, str]] = []
        for remain, kind in flash_state:
            if remain <= 0:
                continue
            _draw_cycle_flash(canvas, kind, remain, flash_frames)
            new_flash.append((remain - 1, kind))
        flash_state = new_flash

        if show_hud and rec is not None:
            v, cl, am = _counters_at(idx)
            _draw_mouth_state_hud(canvas, rec, v, cl, am)

        if show_food_entry_pulse and idx in food_entry_origin_frames:
            pulse_state.append((pulse_frames, idx))
        new_pulse: list[tuple[int, int]] = []
        for remain, origin in pulse_state:
            if remain <= 0:
                continue
            mxy = meta.get("mouth_xy")
            if mxy is None:
                new_pulse.append((remain - 1, origin))
                continue
            r = int(20 + (pulse_frames - remain) * 2)
            alpha = remain / pulse_frames
            color = tuple(int(c * alpha) for c in C_PINK)
            cv2.circle(canvas, (int(mxy[0]), int(mxy[1])), r, color, 2)
            new_pulse.append((remain - 1, origin))
        pulse_state = new_pulse

        if show_strip:
            _draw_strip(canvas, ts, events, height, strip_h, width)
            if state_row is not None:
                # Paint state-color row inside the strip (just below the episode bar).
                y_state = height + 62
                canvas[y_state:y_state + state_row_h, :width] = state_row
        if show_strip and show_proxy_strip:
            _draw_proxy_strip(canvas, ts, events, height + strip_h, proxy_strip_h, width)

        writer.write(canvas)
    writer.release()


def _draw_face_overlay(canvas, rec, meta, *, show_bbox: bool = True, show_chin_nose: bool = True):
    if not rec.face_detected:
        cv2.putText(canvas, "no face", (10, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, C_RED, 2, cv2.LINE_AA)
        return
    if show_bbox:
        bbox = meta.get("bbox")
        if bbox:
            x, y, w, h = bbox
            cv2.rectangle(canvas, (x, y), (x + w, y + h), C_GRAY, 1)
    a = meta.get("lip_a"); b = meta.get("lip_b")
    if a and b:
        lip_color = _state_color(getattr(rec, "mouth_state", "unknown"))
        # Phase 1.5: thicker stroke so the state-coded lip is the dominant cue.
        cv2.line(canvas, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), lip_color, 3)
    mxy = meta.get("mouth_xy")
    if mxy:
        cv2.circle(canvas, (int(mxy[0]), int(mxy[1])), 3, C_GREEN, -1)
    if show_chin_nose:
        chin = meta.get("chin_xy"); nose = meta.get("nose_xy")
        if chin and nose:
            cv2.line(canvas, (int(nose[0]), int(nose[1])),
                     (int(chin[0]), int(chin[1])), C_CYAN, 1, cv2.LINE_AA)


def _draw_mouth_state_hud(canvas, rec, n_visible: int, n_closed_lip: int, n_ambiguous: int):
    state = getattr(rec, "mouth_state", "unknown")
    pose = getattr(rec, "pose_class", "other")
    color = _state_color(state)
    pad = 8
    x0, y0 = pad, 40
    w, h = 340, 110
    overlay = canvas.copy()
    cv2.rectangle(overlay, (x0, y0), (x0 + w, y0 + h), (10, 10, 10), -1)
    cv2.rectangle(overlay, (x0, y0), (x0 + 10, y0 + h), color, -1)
    cv2.addWeighted(overlay, 0.72, canvas, 0.28, 0, dst=canvas)
    font = cv2.FONT_HERSHEY_SIMPLEX
    # Big state line.
    cv2.putText(canvas, f"STATE: {state.upper()}", (x0 + 16, y0 + 28),
                font, 0.78, color, 2, cv2.LINE_AA)
    pose_badge = {"frontal": "F", "profile": "P", "tilted_down": "D", "other": "?"}.get(pose, "?")
    speaking = "S" if rec.is_speaking else "-"
    occluded = "X" if rec.occluded else "-"
    cv2.putText(canvas, f"pose:{pose_badge}  spk:{speaking}  occ:{occluded}",
                (x0 + 16, y0 + 52), font, 0.48, C_WHITE, 1, cv2.LINE_AA)
    # Phase 1.5: show the raw blendshape value + z so reviewers can sanity-check
    # the state-machine output against the signal driving it.
    jbs = float(getattr(rec, "jaw_open_bs", 0.0) or 0.0)
    jbsz = float(getattr(rec, "jaw_open_bs_z", 0.0) or 0.0)
    fc = float(getattr(rec, "face_confidence", 0.0) or 0.0)
    cv2.putText(canvas, f"jawBS:{jbs:.3f}  z:{jbsz:+.2f}  fc:{fc:.2f}",
                (x0 + 16, y0 + 74), font, 0.48, C_WHITE, 1, cv2.LINE_AA)
    total = n_visible + n_closed_lip + n_ambiguous
    cv2.putText(canvas,
                f"OPEN:{n_visible}  CLOSED-LIP:{n_closed_lip}  AMB:{n_ambiguous}  TOT:{total}",
                (x0 + 16, y0 + 96), font, 0.45, C_WHITE, 1, cv2.LINE_AA)


def _draw_cycle_flash(canvas, kind: str, remain: int, total: int):
    h, w = canvas.shape[:2]
    alpha = max(0.0, remain / max(total, 1))
    color = tuple(int(c * alpha) for c in _cycle_color(kind))
    bar_w = 18
    cv2.rectangle(canvas, (w - bar_w, 0), (w, h), color, -1)
    label = {"visible_open": "OPEN", "closed_lip": "CLOSED-LIP", "ambiguous": "AMB"}.get(kind, "CYCLE")
    cv2.putText(canvas, label, (w - 140, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, _cycle_color(kind), 2, cv2.LINE_AA)


def _draw_hands(canvas, meta, *, show_lines: bool = True):
    mxy = meta.get("mouth_xy")
    for hxy in meta.get("hands_xy", []):
        cv2.circle(canvas, (int(hxy[0]), int(hxy[1])), 6, C_BLUE, 2)
        if show_lines and mxy:
            cv2.line(canvas, (int(hxy[0]), int(hxy[1])),
                     (int(mxy[0]), int(mxy[1])), C_BLUE, 1, cv2.LINE_AA)


def _draw_status(canvas, rec, ts, *, clean: bool = False):
    label = rec.label
    color = _label_color(label)
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 32), (0, 0, 0), -1)
    if clean:
        state = getattr(rec, "mouth_state", "unknown")
        pose = getattr(rec, "pose_class", "other")
        txt = (f"t={ts:6.2f}s  label={label}  conf={rec.label_confidence:.2f}  "
               f"state={state}  pose={pose}")
    else:
        h2m = rec.hand_to_mouth_min
        h2m_str = "inf" if not (h2m < 99) else f"{h2m:.2f}"
        txt = (f"t={ts:6.2f}s  label={label}  conf={rec.label_confidence:.2f}  "
               f"jaw={rec.jaw_open_smooth:.3f}  h2m={h2m_str}")
    cv2.putText(canvas, txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


def _draw_strip(canvas, ts, events, height, strip_h, width):
    duration = max(events.duration_s, 1e-3)
    y0 = height
    cv2.rectangle(canvas, (0, y0), (width, y0 + strip_h), (20, 20, 20), -1)
    for cycle in events.mouth_cycles:
        x0 = int(cycle.start_ts / duration * width)
        x1 = max(x0 + 1, int(cycle.end_ts / duration * width))
        cv2.rectangle(
            canvas,
            (x0, y0 + 10),
            (x1, y0 + 30),
            _cycle_color(cycle.kind),
            -1,
        )
        peak_x = int(cycle.peak_ts / duration * width)
        cv2.line(canvas, (peak_x, y0 + 30), (peak_x, y0 + 50), C_YELLOW, 1)
    for proposal in events.food_entry_proposals:
        x = int(proposal.food_entry_ts / duration * width)
        cv2.circle(canvas, (x, y0 + 40), 4, C_PINK, -1)
    for sp in events.non_eating_mouth_spans:
        x0 = int(sp.start_ts / duration * width)
        x1 = int(sp.end_ts / duration * width)
        cv2.rectangle(canvas, (x0, y0 + 50), (x1, y0 + 60), (60, 60, 200), -1)
    px = int(ts / duration * width)
    cv2.line(canvas, (px, y0), (px, y0 + strip_h), C_WHITE, 1)


def _draw_proxy_strip(canvas, ts, events, y_top, strip_h, width):
    """Bottom strip: mouth-cycle timeline color-coded by kind."""
    duration = max(events.duration_s, 1e-3)
    y0 = y_top
    cv2.rectangle(canvas, (0, y0), (width, y0 + strip_h), (12, 12, 12), -1)
    cv2.putText(canvas, "cycles", (4, y0 + 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, C_WHITE, 1, cv2.LINE_AA)
    for c in events.mouth_cycles:
        x0 = int(c.start_ts / duration * width)
        x1 = max(x0 + 1, int(c.end_ts / duration * width))
        cv2.rectangle(canvas, (x0, y0 + 18), (x1, y0 + 30), _cycle_color(c.kind), -1)
    px = int(ts / duration * width)
    cv2.line(canvas, (px, y0), (px, y0 + strip_h), C_WHITE, 1)
