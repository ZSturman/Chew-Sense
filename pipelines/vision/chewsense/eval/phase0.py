"""Bounded Phase-0 preparation, clock diagnostics and independent event evaluation.

These operations never train models and never change source recordings.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from ..canonical import (
    ReviewContext,
    ReviewDraft,
    ReviewRevision,
    file_sha256,
    load_candidate_artifact,
    materialize_annotations,
    validate_review_lineage,
    write_candidate_artifact,
    utc_now,
)
from ..ingest import presentation_timeline
from ..session_bundle import load_coarse_intervals, load_session_manifest


def _clone(source: str, destination: str):
    if sys.platform == "darwin":
        result = subprocess.run(["cp", "-c", source, destination], capture_output=True)
        if result.returncode == 0:
            return destination
    return shutil.copy2(source, destination)


def prepare_experiment(source: Path, workspace: Path, *, blind: bool = False) -> Path:
    """Copy only source media/manifest/coarse context; never previous event truth."""
    manifest = load_session_manifest(source)
    destination = workspace / "sessions" / "raw" / manifest.session_id
    if destination.exists():
        raise FileExistsError(f"experiment session already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / manifest.session_id
        staged.mkdir()
        references = [manifest.motion, manifest.video]
        if manifest.audio and manifest.audio.enabled and manifest.audio.path:
            references.append(manifest.audio)
        copied = {}
        for reference in references:
            if reference is None:
                continue
            relative = Path(reference.path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("source media references must be relative paths inside the bundle")
            # Legacy bundles may intentionally link archived source media. Dereference
            # into the working copy so writes there cannot affect original bytes.
            original = (source / relative).resolve()
            digest = file_sha256(original)
            if reference.sha256 and digest != reference.sha256:
                raise ValueError(f"source hash mismatch: {reference.path}")
            target = staged / reference.path
            target.parent.mkdir(parents=True, exist_ok=True)
            _clone(str(original), str(target))
            if file_sha256(target) != digest:
                raise ValueError("working-copy hash mismatch")
            copied[reference.path] = digest
        manifest.status = "collected"
        manifest.annotation_revision = 0
        manifest.active_model_ids = {}
        (staged / "session.json").write_text(manifest.model_dump_json(indent=2))
        coarse = source / "annotations/coarse_labels.json"
        if coarse.exists():
            (staged / "annotations").mkdir(exist_ok=True)
            shutil.copy2(coarse, staged / "annotations/coarse_labels.json")
        (staged / "pilot-provenance.json").write_text(
            json.dumps(
                {
                    "source_session": str(source.resolve()),
                    "source_manifest_sha256": file_sha256(source / "session.json"),
                    "media_sha256": copied,
                    "legacy_diagnostic_only": manifest.legacy_review_required
                    or manifest.source_app == "legacy_import",
                    "annotation_mode": "blind" if blind else "assisted",
                    "independent_truth": False,
                },
                indent=2,
            )
        )
        staged.rename(destination)
    if blind and load_coarse_intervals(destination) is not None:
        init_blind_review(destination, workspace / "derived/vision" / manifest.session_id)
    return destination


def init_blind_review(session: Path, derived: Path) -> Path:
    manifest = load_session_manifest(session)
    coarse = load_coarse_intervals(session)
    if coarse is None:
        raise ValueError("complete coarse review before initializing blind event review")
    if (
        any(derived.glob("candidates-v*.json"))
        or (session / "annotations/review-draft.json").exists()
    ):
        raise FileExistsError(
            "review/proposals already exist; use a fresh independent working copy"
        )
    path, candidate = write_candidate_artifact(
        derived,
        session_id=manifest.session_id,
        chew_hypotheses=[],
        food_entry_hypotheses=[],
        coarse_intervals=coarse,
        model_versions={"annotation_mode": "blind_no_predictions"},
    )
    draft = ReviewDraft(
        session_id=manifest.session_id,
        candidate_revision=candidate.candidate_revision,
        candidate_sha256=candidate.content_sha256,
        updated_at=utc_now(),
        review_context=ReviewContext(mode="blind"),
    )
    annotations = session / "annotations"
    annotations.mkdir(exist_ok=True)
    (annotations / "review-draft.json").write_text(draft.model_dump_json(indent=2))
    return path


def event_metrics(
    prediction: list[tuple[str, float]], gold: list[tuple[str, float]], tolerance_s: float
) -> dict:
    """Maximum-cardinality one-to-one assignment, then minimum absolute error."""
    if not np.isfinite(tolerance_s) or tolerance_s <= 0:
        raise ValueError("matching tolerance must be positive and finite")
    p = np.asarray([t for _, t in prediction], dtype=float)
    g = np.asarray([t for _, t in gold], dtype=float)
    if not np.all(np.isfinite(p)) or not np.all(np.isfinite(g)):
        raise ValueError("event times must be finite")
    matches = []
    if len(p) and len(g):
        distance = abs(p[:, None] - g[None, :])
        # One forbidden match costs more than every allowed distance combined.
        cost = np.where(distance <= tolerance_s, distance, (len(p) + len(g) + 1) * tolerance_s)
        rows, cols = linear_sum_assignment(cost)
        matches = [
            (int(i), int(j), float(distance[i, j]))
            for i, j in zip(rows, cols)
            if distance[i, j] <= tolerance_s
        ]
    used_p, used_g = {i for i, _, _ in matches}, {j for _, j, _ in matches}
    tp, fp, fn = len(matches), len(p) - len(matches), len(g) - len(matches)
    precision, recall = tp / (tp + fp) if tp + fp else 0, tp / (tp + fn) if tp + fn else 0
    errors = [d for _, _, d in matches]
    return {
        "tolerance_s": tolerance_s,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
        "predicted_count": len(p),
        "gold_count": len(g),
        "count_error": len(p) - len(g),
        "absolute_count_error": abs(len(p) - len(g)),
        "relative_count_error": abs(len(p) - len(g)) / len(g) if len(g) else None,
        "timestamp_mae_s": float(np.mean(errors)) if errors else None,
        "timestamp_p95_s": float(np.percentile(errors, 95)) if errors else None,
        "missed_gold_ids": [gold[j][0] for j in range(len(g)) if j not in used_g],
        "extra_prediction_ids": [prediction[i][0] for i in range(len(p)) if i not in used_p],
        "matches": [
            {"prediction_id": prediction[i][0], "gold_id": gold[j][0], "absolute_error_s": d}
            for i, j, d in matches
        ],
    }


def timing_report(session: Path, markers: list[dict] | None = None) -> dict:
    manifest = load_session_manifest(session)
    motion = pd.read_csv(session / manifest.motion.path)
    ts = motion["timestamp"].to_numpy(dtype=float)
    if not len(ts) or not np.all(np.isfinite(ts)) or np.any(np.diff(ts) <= 0):
        raise ValueError("motion timestamps must be finite, nonempty and strictly increasing")
    axes = motion[["ax", "ay", "az", "gx", "gy", "gz"]].to_numpy(dtype=float)
    if not np.all(np.isfinite(axes)):
        raise ValueError("motion values must be finite")
    origin = manifest.clock_sync.video_clock_origin_host_time_s
    relative = ts - origin
    pts, duration = (
        presentation_timeline(session / manifest.video.path)
        if manifest.video
        else (np.array([]), manifest.capture.duration_s or 0)
    )
    delta = np.diff(relative)
    gaps = [
        {"start_s": float(a), "end_s": float(b)}
        for a, b in zip(relative[:-1], relative[1:])
        if b - a > 0.08
    ]
    intervals = [
        (max(0, a), min(duration, b))
        for a, b in zip(relative[:-1], relative[1:])
        if b - a <= 0.08 and b > 0 and a < duration
    ]
    covered = sum(max(0, b - a) for a, b in intervals)
    result = {
        "session_id": manifest.session_id,
        "clock_method": manifest.clock_sync.method,
        "physical_sync_validated": False,
        "legacy_diagnostic_only": manifest.legacy_review_required
        or manifest.source_app == "legacy_import",
        "duration_s": duration,
        "motion_rows": len(ts),
        "motion_start_video_s": float(relative[0]),
        "motion_end_video_s": float(relative[-1]),
        "median_motion_rate_hz": float(1 / np.median(delta)) if len(delta) else None,
        "motion_gaps": gaps,
        "missing_sensor_seconds": max(0, duration - covered),
        "sensor_coverage_fraction": covered / duration if duration else 0,
        "video_frames": len(pts),
        "video_first_pts_s": float(pts[0]) if len(pts) else None,
        "video_max_frame_gap_s": float(np.diff(pts).max()) if len(pts) > 1 else None,
        "video_variable_rate": bool(np.ptp(np.diff(pts)) > 0.002) if len(pts) > 1 else None,
        "media_sha256": {
            r.path: file_sha256(session / r.path) for r in (manifest.video, manifest.motion) if r
        },
    }
    if markers:
        video = np.array([m["video_ts"] for m in markers], dtype=float)
        sensor = np.array([m["motion_host_ts"] for m in markers], dtype=float)
        if (
            len(markers) < 3
            or not np.all(np.isfinite(video))
            or not np.all(np.isfinite(sensor))
            or np.any(np.diff(video) <= 0)
            or np.any(np.diff(sensor) <= 0)
        ):
            raise ValueError("provide at least three ordered corresponding sync markers")
        if (
            np.any(video < 0)
            or np.any(video > duration)
            or np.any(sensor < ts[0])
            or np.any(sensor > ts[-1])
        ):
            raise ValueError("sync markers must lie within video and motion coverage")
        residual = video - (sensor - origin)
        slope, _ = np.polyfit(sensor - sensor[0], video - video[0], 1)
        result["sync_markers"] = {
            "residuals_s": residual.tolist(),
            "median_absolute_error_s": float(np.median(abs(residual))),
            "p95_absolute_error_s": float(np.percentile(abs(residual), 95)),
            "diagnostic_drift_ppm": float((slope - 1) * 1e6),
            "pilot_event_target_met": bool(
                np.median(abs(residual)) <= 0.05 and np.percentile(abs(residual), 95) <= 0.1
            ),
            "mapping_changed": False,
        }
    return result


def evaluate_experiment(
    prediction_path: Path,
    gold_review_path: Path,
    gold_candidate_path: Path,
    *,
    tolerance_s: float = 0.25,
    assisted_review_path: Path | None = None,
) -> dict:
    predictions = load_candidate_artifact(prediction_path)
    candidates = load_candidate_artifact(gold_candidate_path)
    review = ReviewRevision.model_validate_json(gold_review_path.read_text())
    validate_review_lineage(candidates, review)
    context = review.review_context
    if (
        not review.verified
        or not review.full_video_pass_completed
        or context is None
        or context.mode != "blind"
    ):
        raise ValueError(
            "evaluation requires a frozen verified independent blind full-video review"
        )
    if (
        candidates.chews
        or candidates.food_entries
        or review.chew_decisions
        or review.food_entry_decisions
    ):
        raise ValueError("blind gold must use empty proposals and manually added events")
    if (
        review.candidate_sha256 == predictions.content_sha256
        or review.session_id != predictions.session_id
    ):
        raise ValueError(
            "prediction/gold lineage is not independent or belongs to different sessions"
        )
    session = gold_review_path.parent.parent
    manifest = load_session_manifest(session)
    provenance_path = session / "pilot-provenance.json"
    provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else {}
    if (
        manifest.legacy_review_required
        or manifest.source_app == "legacy_import"
        or provenance.get("legacy_diagnostic_only")
    ):
        raise ValueError("legacy recordings cannot establish independent evaluation ground truth")
    # Compare exact source video bytes recorded in immutable candidate provenance.
    video_sha = file_sha256(session / manifest.video.path) if manifest.video else None
    if not video_sha or predictions.debug_telemetry.get("video_sha256") != video_sha:
        raise ValueError("prediction provenance does not match gold source video")
    annotations = materialize_annotations(candidates, review)
    spans = [(s.start_ts, s.end_ts) for s in context.excluded_spans]
    coarse = load_coarse_intervals(session)
    if coarse is None:
        raise ValueError("gold coarse review is missing")
    spans += [(s.start_ts, s.end_ts) for s in coarse if s.label == "unknown"]

    def observable(start, end):
        return not any(start <= b and a <= end for a, b in spans)

    pred = [
        (c.id, c.end_closed_ts)
        for c in predictions.chews
        if observable(c.start_closed_ts, c.end_closed_ts)
    ]
    gold = [
        (c.id, c.end_closed_ts)
        for c in annotations.chew_cycles
        if observable(c.start_closed_ts, c.end_closed_ts)
    ]
    result = event_metrics(pred, gold, tolerance_s)
    result.update(
        {
            "prediction_sha256": predictions.content_sha256,
            "gold_review_sha256": review.content_sha256,
            "video_sha256": video_sha,
            "excluded_spans": spans,
            "excluded_predictions": len(predictions.chews) - len(pred),
            "gold_active_seconds": context.active_seconds or None,
            "coarse_gate": predictions.coarse_label_state,
            "coverage": timing_report(session),
            "note": "Conditional on reviewed observability and coarse gate; timestamp error includes annotation and detector error.",
        }
    )
    if assisted_review_path:
        assisted = ReviewRevision.model_validate_json(assisted_review_path.read_text())
        if assisted.candidate_sha256 != predictions.content_sha256 or not assisted.verified:
            raise ValueError(
                "assisted timing review must be verified and bound to these predictions"
            )
        seconds = assisted.review_context.active_seconds if assisted.review_context else 0
        baseline = context.active_seconds
        result.update(
            {
                "human_correction_seconds": seconds or None,
                "human_seconds_saved": baseline - seconds if baseline and seconds else None,
                "human_fraction_saved": 1 - seconds / baseline if baseline and seconds else None,
            }
        )
    return result
