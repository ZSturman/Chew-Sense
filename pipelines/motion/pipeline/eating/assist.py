"""Assisted coarse labeling: chewing-span proposals from two independent sources.

Video (ungated Vision mouth-cycle proposals over the whole recording) and
AirPods motion (the exported eating detector) each judge every 3 s window
independently.  Where they agree, a span is *proposed* as chewing or not
chewing and may be accepted in one batch; everywhere else (disagreement, no
visible face, no motion) the wearer decides.  Spans where both sources agree
against an existing human label are flagged as possibly mislabelled.

Proposals are never labels.  They are written as immutable, hashed
``coarse-proposals-vNNNN.json`` artifacts.  Only a human decision recorded by
Review (``coarse-review-vNNNN.json``) turns a span into a label, and it keeps
its origin: individually reviewed spans become ``human_assisted`` and
batch-accepted agreement spans ``human_batch_accepted``.  Both are
training-only tiers; evaluation keeps using unassisted human labels.

The decision rules are fixed in advance (``AssistConfig``): the video rule is
the pilot's "at least two proposed chew cycles in a 3 s window with the face
visible", and the motion rule is the exported detector's development
threshold.  Nothing is fitted to the recordings used to measure the rules.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .dataset import CHEWING, NOT_CHEWING, EatingSession, _label_runs
from .video_teacher import video_window_features

PROPOSAL_SCHEMA = 1
AUTO = "auto"
REVIEW = "review"


@dataclass(frozen=True)
class AssistConfig:
    window_s: float = 3.0
    stride_s: float = 0.5
    video_min_cycles: int = 2
    video_min_face_fraction: float = 0.5
    motion_threshold: float = 0.71
    min_span_s: float = 1.5
    rule_version: str = "agreement-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_hash(payload: Mapping[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "content_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def window_ends(duration_s: float, config: AssistConfig) -> np.ndarray:
    return np.arange(config.window_s, duration_s + 1e-9, config.stride_s)


def video_states(vision_dir: Path, ends: np.ndarray, config: AssistConfig) -> pd.DataFrame:
    """Per-window video evidence: chewing, not_chewing or unavailable (face not visible)."""

    frame = video_window_features(Path(vision_dir), ends, config.window_s)
    cycles = np.rint(frame["video_cycles_per_s"].to_numpy() * config.window_s).astype(int)
    visible = frame["video_face_fraction"].to_numpy() >= config.video_min_face_fraction
    frame["video_cycles"] = cycles
    frame["video_state"] = np.where(
        ~visible, "unavailable", np.where(cycles >= config.video_min_cycles, CHEWING, NOT_CHEWING)
    )
    return frame


def motion_states(probability: np.ndarray, config: AssistConfig) -> np.ndarray:
    p = np.asarray(probability, dtype=float)
    return np.where(np.isnan(p), "unavailable", np.where(p >= config.motion_threshold, CHEWING, NOT_CHEWING))


def fuse(video_state: np.ndarray, motion_state: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return per-window (state, proposal, reason)."""

    video_state = np.asarray(video_state).astype(str)
    motion_state = np.asarray(motion_state).astype(str)
    agree = (video_state == motion_state) & (video_state != "unavailable")
    state = np.where(agree, AUTO, REVIEW)
    proposal = np.where(agree, video_state, "")
    reason = np.select(
        [
            agree,
            (video_state == "unavailable") & (motion_state == "unavailable"),
            video_state == "unavailable",
            motion_state == "unavailable",
        ],
        ["video and motion agree", "no face and no motion", "face not visible", "motion missing"],
        default="video and motion disagree",
    )
    return state, proposal, reason


def _existing_label_runs(intervals) -> list[tuple[float, float, str]]:
    return [(r[0], r[1], r[2]) for r in _label_runs(intervals)] if intervals else []


def _label_at(runs: Sequence[tuple[float, float, str]], lo: float, hi: float) -> str:
    """Human label covering ``[lo, hi]`` entirely, 'mixed' across a boundary, '' when unlabelled."""

    for start, end, label in runs:
        if start <= lo and hi <= end:
            return label
    return "mixed" if any(start < hi and lo < end for start, end, _ in runs) else ""


def build_spans(
    windows: pd.DataFrame,
    duration_s: float,
    config: AssistConfig,
    existing_runs: Sequence[tuple[float, float, str]] = (),
) -> list[dict[str, Any]]:
    """Group per-window states into review spans on the video timeline.

    Each window's evidence is placed at its centre (``t_end - window/2``), so
    a proposed boundary is uncertain by about half a window (±1.5 s).  Runs
    shorter than ``min_span_s`` are absorbed into the review state rather than
    proposed on their own.
    """

    if windows.empty:
        return [{
            "start": 0.0, "end": float(duration_s), "state": REVIEW, "proposal": None,
            "reason": "recording shorter than one window", "windows": 0,
        }]
    centres = windows["t_end"].to_numpy() - config.window_s / 2
    keys = list(zip(windows["state"], windows["proposal"], windows["reason"]))
    half = config.stride_s / 2
    runs: list[dict[str, Any]] = []
    for index, key in enumerate(keys):
        start = 0.0 if index == 0 else centres[index] - half
        end = float(duration_s) if index == len(keys) - 1 else centres[index] + half
        if runs and runs[-1]["key"] == key:
            runs[-1]["end"] = end
            runs[-1]["rows"].append(index)
        else:
            runs.append({"key": key, "start": start, "end": end, "rows": [index]})
    # Very short agreement runs inside review time are not proposed alone.
    for run in runs:
        if run["key"][0] == AUTO and run["end"] - run["start"] < config.min_span_s:
            run["key"] = (REVIEW, "", "short agreement")
    # Neighbouring runs with the same state and proposal form one span; a
    # review span lists every reason it contains.
    merged: list[dict[str, Any]] = []
    for run in runs:
        if merged and merged[-1]["key"][:2] == run["key"][:2]:
            merged[-1]["end"] = run["end"]
            merged[-1]["rows"].extend(run["rows"])
            merged[-1]["reasons"].add(run["key"][2])
            continue
        merged.append({**run, "reasons": {run["key"][2]}})

    spans = []
    for index, run in enumerate(merged):
        rows = windows.iloc[run["rows"]]
        state, proposal, _ = run["key"]
        existing = _label_at(existing_runs, run["start"], run["end"]) if existing_runs else ""
        conflict = bool(state == AUTO and existing in {CHEWING, NOT_CHEWING} and existing != proposal)
        motion = rows["p_motion"].to_numpy(dtype=float)
        span = {
            "id": f"cp_{index + 1:04d}",
            "start": round(float(run["start"]), 3),
            "end": round(float(run["end"]), 3),
            "state": state,
            "proposal": proposal or None,
            "reason": "; ".join(sorted(run["reasons"])),
            "windows": int(len(rows)),
            "evidence": {
                "video_chewing_windows": int((rows["video_state"] == CHEWING).sum()),
                "video_not_chewing_windows": int((rows["video_state"] == NOT_CHEWING).sum()),
                "video_unavailable_windows": int((rows["video_state"] == "unavailable").sum()),
                "motion_chewing_windows": int((rows["motion_state"] == CHEWING).sum()),
                "motion_mean_probability": None if np.isnan(motion).all() else round(float(np.nanmean(motion)), 4),
                "video_cycles_per_s": round(float(rows["video_cycles_per_s"].mean()), 3),
            },
            "existing_label": existing or None,
            "conflict_with_existing_label": conflict,
        }
        duration = span["end"] - span["start"]
        # Review the most informative spans first: conflicts, then long disagreements.
        span["priority"] = round((3.0 if conflict else 1.0 if state == REVIEW else 0.0) * max(duration, 0.5), 3)
        spans.append(span)
    return spans


def summarize_spans(spans: Sequence[Mapping[str, Any]], duration_s: float) -> dict[str, Any]:
    def seconds(predicate) -> float:
        return round(sum(s["end"] - s["start"] for s in spans if predicate(s)), 3)

    auto_chewing = seconds(lambda s: s["state"] == AUTO and s["proposal"] == CHEWING)
    auto_not = seconds(lambda s: s["state"] == AUTO and s["proposal"] == NOT_CHEWING)
    review = seconds(lambda s: s["state"] == REVIEW)
    return {
        "duration_s": round(float(duration_s), 3),
        "auto_chewing_s": auto_chewing,
        "auto_not_chewing_s": auto_not,
        "review_s": review,
        "coverage": round((auto_chewing + auto_not) / duration_s, 4) if duration_s else 0.0,
        "review_items": sum(1 for s in spans if s["state"] == REVIEW),
        "batch_items": sum(1 for s in spans if s["state"] == AUTO),
        "label_conflicts": sum(1 for s in spans if s["conflict_with_existing_label"]),
    }


def propose_session(
    *,
    session_id: str,
    duration_s: float,
    vision_dir: Path,
    motion_probability: pd.DataFrame,
    config: AssistConfig,
    existing_intervals=None,
    sources: Mapping[str, Any] | None = None,
    empirical: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Proposal payload for one recording.

    ``motion_probability`` has ``t_end`` and ``probability`` (NaN when the
    window had too little measured motion) on the same window grid.
    """

    ends = window_ends(duration_s, config)
    video = video_states(vision_dir, ends, config)
    motion = motion_probability.set_index(np.round(motion_probability["t_end"], 3))["probability"]
    p_motion = motion.reindex(np.round(ends, 3)).to_numpy(dtype=float)
    frame = video.assign(p_motion=p_motion, motion_state=motion_states(p_motion, config))
    frame["state"], frame["proposal"], frame["reason"] = fuse(frame["video_state"], frame["motion_state"])
    runs = _existing_label_runs(existing_intervals)
    spans = build_spans(frame, duration_s, config, runs)
    payload = {
        "artifact_type": "coarse_label_proposals",
        "schema_version": PROPOSAL_SCHEMA,
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "proposed_not_labels",
        "note": "Agreement spans may be accepted in batch; review spans need a human decision. "
                "Nothing here is ground truth until Review records a decision.",
        "config": asdict(config),
        "sources": dict(sources or {}),
        "empirical_agreement": dict(empirical or {}),
        "summary": summarize_spans(spans, duration_s),
        "existing_labels": [
            {"start": round(s, 3), "end": round(e, 3), "label": label} for s, e, label in runs
        ],
        "windows": [
            {
                "t_end": round(float(row.t_end), 3),
                "video_cycles": int(row.video_cycles),
                "video_face_fraction": round(float(row.video_face_fraction), 3),
                "video_state": row.video_state,
                "p_motion": None if math.isnan(row.p_motion) else round(float(row.p_motion), 5),
                "motion_state": row.motion_state,
                "state": row.state,
            }
            for row in frame.itertuples()
        ],
        "spans": spans,
    }
    payload["content_sha256"] = content_hash(payload)
    return payload


def write_proposals(directory: Path, payload: Mapping[str, Any]) -> Path:
    """Write the next immutable ``coarse-proposals-vNNNN.json``; identical content is not rewritten."""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    existing = sorted(directory.glob("coarse-proposals-v*.json"))
    if existing:
        latest = json.loads(existing[-1].read_text())
        same = {k: v for k, v in latest.items() if k not in {"created_at", "content_sha256"}}
        new = {k: v for k, v in payload.items() if k not in {"created_at", "content_sha256"}}
        if same == new:
            return existing[-1]
    revision = len(existing) + 1
    path = directory / f"coarse-proposals-v{revision:04d}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    tmp.rename(path)
    path.chmod(0o444)
    return path


# ---------------------------------------------------------------- evaluation


def wilson(successes: int, total: int, z: float = 1.96) -> dict[str, float | None]:
    if total <= 0:
        return {"rate": None, "low": None, "high": None}
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return {"rate": p, "low": max(0.0, centre - half), "high": min(1.0, centre + half)}


def evaluate_assist(
    sessions: Sequence[EatingSession],
    vision_dirs: Mapping[str, Path],
    motion_predictions: pd.DataFrame,
    config: AssistConfig,
    *,
    guard_s: float = 1.0,
    manual_seconds_per_boundary: float = 5.0,
    review_seconds_per_item: float = 6.0,
) -> dict[str, Any]:
    """Compare fixed-rule proposals with existing human labels.

    ``motion_predictions`` must contain, for each session, probabilities
    from a model that never trained on that session (out-of-fold or locked
    test predictions), columns ``session_id``, ``t_end``, ``probability``.
    Only windows lying inside one human label run (± ``guard_s``) are scored.

    Review effort is a *model*, not a measurement: watching the whole video
    plus a fixed cost per marked boundary (manual), versus watching review
    spans plus a fixed cost per decision (assisted).  Review records real
    active seconds once the wearer uses it.
    """

    per_session = []
    totals = {k: 0 for k in (
        "labelled", "auto", "auto_chewing", "auto_chewing_correct", "auto_not", "auto_not_correct",
        "chewing_labelled", "chewing_auto_recovered", "review", "video_unavailable",
    )}
    effort_manual = effort_assisted = 0.0
    for session in sessions:
        vision_dir = vision_dirs.get(session.session_id)
        if vision_dir is None:
            continue
        rows = motion_predictions[motion_predictions["session_id"] == session.session_id]
        payload = propose_session(
            session_id=session.session_id, duration_s=session.duration_s, vision_dir=vision_dir,
            motion_probability=rows[["t_end", "probability"]], config=config,
            existing_intervals=session.intervals,
        )
        frame = pd.DataFrame(payload["windows"])
        runs = _existing_label_runs(session.intervals)
        labels = np.array([
            _label_at(runs, t - config.window_s - guard_s, t + guard_s) for t in frame["t_end"]
        ])
        scored = np.isin(labels, [CHEWING, NOT_CHEWING])
        f = frame[scored].assign(label=labels[scored])
        auto = f["state"] == AUTO
        stats = {
            "labelled": int(len(f)),
            "auto": int(auto.sum()),
            "auto_chewing": int((auto & (f["video_state"] == CHEWING)).sum()),
            "auto_chewing_correct": int((auto & (f["video_state"] == CHEWING) & (f["label"] == CHEWING)).sum()),
            "auto_not": int((auto & (f["video_state"] == NOT_CHEWING)).sum()),
            "auto_not_correct": int((auto & (f["video_state"] == NOT_CHEWING) & (f["label"] == NOT_CHEWING)).sum()),
            "chewing_labelled": int((f["label"] == CHEWING).sum()),
            "chewing_auto_recovered": int((auto & (f["video_state"] == CHEWING) & (f["label"] == CHEWING)).sum()),
            "review": int((~auto).sum()),
            "video_unavailable": int((f["video_state"] == "unavailable").sum()),
        }
        for key, value in stats.items():
            totals[key] += value
        summary = payload["summary"]
        boundaries = sum(1 for start, end, label in runs if label == CHEWING) * 2
        manual = session.duration_s + manual_seconds_per_boundary * boundaries
        assisted = summary["review_s"] + review_seconds_per_item * summary["review_items"] + review_seconds_per_item
        effort_manual += manual
        effort_assisted += assisted
        per_session.append({
            "session_id": session.session_id,
            "clock": session.clock,
            **stats,
            "false_positive_windows": stats["auto_chewing"] - stats["auto_chewing_correct"],
            "false_negative_windows": stats["auto_not"] - stats["auto_not_correct"],
            "proposal_summary": summary,
            "effort_model_s": {"manual": round(manual, 1), "assisted": round(assisted, 1)},
            "conflict_spans": [
                {k: s[k] for k in ("start", "end", "proposal", "existing_label")}
                for s in payload["spans"] if s["conflict_with_existing_label"]
            ],
        })
    false_positive = totals["auto_chewing"] - totals["auto_chewing_correct"]
    false_negative = totals["auto_not"] - totals["auto_not_correct"]
    return {
        "artifact_type": "assisted_labeling_evaluation",
        "config": asdict(config),
        "guard_s": guard_s,
        "recordings": len(per_session),
        "windows_scored": totals["labelled"],
        "coverage": wilson(totals["auto"], totals["labelled"]),
        "auto_chewing_precision": wilson(totals["auto_chewing_correct"], totals["auto_chewing"]),
        "auto_not_chewing_precision": wilson(totals["auto_not_correct"], totals["auto_not"]),
        "chewing_recovered_automatically": wilson(totals["chewing_auto_recovered"], totals["chewing_labelled"]),
        "false_positive_windows": false_positive,
        "false_negative_windows": false_negative,
        "review_windows": totals["review"],
        "video_unavailable_windows": totals["video_unavailable"],
        "totals": totals,
        "effort_model": {
            "manual_s": round(effort_manual, 1),
            "assisted_s": round(effort_assisted, 1),
            "saving_fraction": round(1 - effort_assisted / effort_manual, 3) if effort_manual else None,
            "assumptions": {
                "manual": f"watch the whole recording + {manual_seconds_per_boundary:g} s per chewing boundary",
                "assisted": f"watch review spans + {review_seconds_per_item:g} s per review decision "
                            f"+ one batch acceptance",
            },
            "status": "modelled, not measured",
        },
        "per_session": per_session,
    }


# ---------------------------------------------------------------- one recording


def empirical_summary(evaluation: Mapping[str, Any] | None) -> dict[str, Any]:
    """The measured agreement precision shown beside proposals, if an evaluation exists."""

    if not evaluation:
        return {"status": "no assisted-labeling evaluation has been run"}
    return {
        "evaluation": evaluation.get("evaluation_id"),
        "recordings": evaluation.get("recordings"),
        "windows_scored": evaluation.get("windows_scored"),
        "auto_chewing_precision": evaluation.get("auto_chewing_precision"),
        "auto_not_chewing_precision": evaluation.get("auto_not_chewing_precision"),
        "coverage": evaluation.get("coverage"),
        "caveat": "one wearer, few recordings; precision is a pilot estimate",
    }


def propose_for_session_dir(
    session_dir: Path,
    vision_dir: Path,
    spec: Mapping[str, Any],
    output_dir: Path,
    *,
    spec_path: Path | None = None,
    empirical: Mapping[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Propose chewing spans for one canonical recording and write them immutably."""

    from .dataset import SessionRejected, load_canonical_motion, load_canonical_session
    from .runtime import run_stream

    session_dir, vision_dir = Path(session_dir), Path(vision_dir)
    if not (vision_dir / "mouth_cycles.json").exists() or not (vision_dir / "signals" / "ts.npy").exists():
        raise FileNotFoundError(f"no ungated Vision output in {vision_dir}")
    motion = load_canonical_motion(session_dir)
    config = AssistConfig(motion_threshold=float(spec["decision"]["threshold"]),
                          window_s=float(spec["input"]["window_s"]),
                          stride_s=float(spec["decision"]["stride_s"]))
    ends = window_ends(motion.duration, config)
    stream = run_stream(spec, motion.video_t, motion.imu, ends)
    try:
        existing = load_canonical_session(session_dir).intervals
    except SessionRejected:
        existing = None
    sources = {
        "video": {
            "vision_dir": str(vision_dir),
            "mouth_cycles_sha256": _sha256(vision_dir / "mouth_cycles.json"),
            "rule": f">= {config.video_min_cycles} proposed chew cycles in a {config.window_s:g} s window, "
                    f"face visible in >= {config.video_min_face_fraction:.0%} of it",
        },
        "motion": {
            "model_id": spec.get("model_id"),
            "spec_path": str(spec_path) if spec_path else None,
            "spec_sha256": _sha256(spec_path) if spec_path else None,
            "rule": f"exported detector probability >= {config.motion_threshold:g} (development threshold)",
            "motion_sha256": _sha256(motion.motion_path),
            "clock": motion.method,
        },
        "existing_labels": "phone coarse labels and earlier assisted reviews" if existing else "none",
    }
    payload = propose_session(
        session_id=motion.session_id,
        duration_s=motion.duration,
        vision_dir=vision_dir,
        motion_probability=stream[["t_end", "probability"]],
        config=config,
        existing_intervals=existing,
        sources=sources,
        empirical=empirical,
    )
    return write_proposals(output_dir, payload), payload
