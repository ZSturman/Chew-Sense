"""Closed-loop recommendations for what field-test data to record next."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import CollectionTargetConfig, PipelineConfig


DEFAULT_TARGETS = [
    CollectionTargetConfig("eating_soft", "Eating soft food", "positive", 8, ["eating"]),
    CollectionTargetConfig("eating_hard", "Eating hard food", "positive", 8, []),
    CollectionTargetConfig("drinking_water", "Drinking water", "negative", 6, ["drinking"]),
    CollectionTargetConfig("not_eating", "Not eating / baseline", "negative", 6, ["baseline", "not-eating", "noteating"]),
    CollectionTargetConfig("walking", "Walking", "negative", 6, []),
    CollectionTargetConfig("head_shake", "Shaking head", "negative", 6, []),
    CollectionTargetConfig("nodding", "Nodding", "negative", 6, []),
    CollectionTargetConfig("talking", "Talking", "mixed", 6, []),
    CollectionTargetConfig("singing", "Singing", "mixed", 6, []),
]


def generate_collection_plan(
    metrics: dict | None,
    curated_dir: Path,
    config: PipelineConfig,
    model_id: str | None,
    dataset_hash: str | None,
) -> dict[str, Any]:
    """Return a ranked plan of activity presets to collect next.

    This is deliberately heuristic active learning: it uses known activity
    preset tags, per-activity confusion counts, and coverage gaps. It does not
    invent new recording categories.
    """
    targets = _targets_from_config(config)
    source_split, source_metrics = _select_source_metrics(metrics)
    per_activity = _per_activity_metrics(source_metrics)
    coverage_minutes = _activity_minutes(Path(curated_dir))

    candidates: list[dict[str, Any]] = []
    for target in targets:
        activity_keys = {target.activity, *target.aliases}
        activity_metrics = _merge_activity_metrics(per_activity, activity_keys)
        current_minutes = sum(
            minutes for activity, minutes in coverage_minutes.items()
            if activity in activity_keys
        )
        target_minutes = float(target.target_minutes)
        coverage_deficit = max(0.0, target_minutes - current_minutes)

        recommendation = _recommendation_for_target(
            target=target,
            metrics=activity_metrics,
            current_minutes=current_minutes,
            target_minutes=target_minutes,
            coverage_deficit=coverage_deficit,
            config=config,
        )
        if recommendation is not None:
            candidates.append(recommendation)

    candidates.sort(key=lambda item: (item["_priority"], -item["_score"], item["activity"]))
    recommendations = []
    for idx, item in enumerate(candidates[: config.collection.max_recommendations], start=1):
        item = dict(item)
        item["id"] = f"{_safe_slug(item['activity'])}_{_safe_slug(item['reason_codes'][0])}_{idx:03d}"
        item.pop("_score", None)
        item.pop("_priority", None)
        recommendations.append(item)

    return {
        "version": 1,
        "model_id": model_id or "",
        "dataset_hash": dataset_hash or "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_split": source_split,
        "recommendations": recommendations,
    }


def _targets_from_config(config: PipelineConfig) -> list[CollectionTargetConfig]:
    return list(config.collection.targets or DEFAULT_TARGETS)


def _select_source_metrics(metrics: dict | None) -> tuple[str, dict[str, Any]]:
    if not isinstance(metrics, dict):
        return "none", {}
    for split in ("field_regression", "gold_locked", "validation_locked", "all"):
        split_metrics = metrics.get(split)
        if isinstance(split_metrics, dict):
            return split, split_metrics
    if "per_activity" in metrics or "accuracy" in metrics:
        return "all", metrics
    return "none", {}


def _per_activity_metrics(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    per_activity = metrics.get("per_activity")
    if not isinstance(per_activity, dict):
        return {}
    return {
        str(activity): data
        for activity, data in per_activity.items()
        if isinstance(data, dict)
    }


def _merge_activity_metrics(
    per_activity: dict[str, dict[str, Any]],
    activity_keys: set[str],
) -> dict[str, Any]:
    matched = [
        data for activity, data in per_activity.items()
        if activity in activity_keys
    ]
    if not matched:
        return {}

    totals = {
        "n_samples": 0,
        "n_positive": 0,
        "n_negative": 0,
        "false_positive": 0,
        "false_negative": 0,
        "true_positive": 0,
        "true_negative": 0,
    }
    for data in matched:
        counts = _confusion_counts(data)
        for key in totals:
            totals[key] += counts.get(key, 0)

    n_positive = totals["n_positive"]
    n_negative = totals["n_negative"]
    totals["false_positive_rate"] = (
        totals["false_positive"] / n_negative if n_negative else 0.0
    )
    totals["false_negative_rate"] = (
        totals["false_negative"] / n_positive if n_positive else 0.0
    )
    return totals


def _confusion_counts(metrics: dict[str, Any]) -> dict[str, int]:
    out = {
        "n_samples": _int(metrics.get("n_samples")),
        "n_positive": _int(metrics.get("n_positive")),
        "n_negative": _int(metrics.get("n_negative")),
        "false_positive": _int(metrics.get("false_positive")),
        "false_negative": _int(metrics.get("false_negative")),
        "true_positive": _int(metrics.get("true_positive")),
        "true_negative": _int(metrics.get("true_negative")),
    }
    if out["n_samples"] == 0:
        out["n_samples"] = out["n_positive"] + out["n_negative"]

    if out["false_positive"] == 0 and out["false_negative"] == 0:
        matrix = metrics.get("confusion_matrix")
        if (
            isinstance(matrix, list)
            and len(matrix) == 2
            and all(isinstance(row, list) and len(row) == 2 for row in matrix)
        ):
            out["true_negative"] = _int(matrix[0][0])
            out["false_positive"] = _int(matrix[0][1])
            out["false_negative"] = _int(matrix[1][0])
            out["true_positive"] = _int(matrix[1][1])

    if out["false_positive"] == 0 and out["n_negative"] > 0:
        fp_rate = metrics.get("false_positive_rate")
        if isinstance(fp_rate, (int, float)) and math.isfinite(fp_rate):
            out["false_positive"] = int(round(float(fp_rate) * out["n_negative"]))

    if out["false_negative"] == 0 and out["n_positive"] > 0:
        fn_rate = metrics.get("false_negative_rate")
        if isinstance(fn_rate, (int, float)) and math.isfinite(fn_rate):
            out["false_negative"] = int(round(float(fn_rate) * out["n_positive"]))

    return out


def _recommendation_for_target(
    target: CollectionTargetConfig,
    metrics: dict[str, Any],
    current_minutes: float,
    target_minutes: float,
    coverage_deficit: float,
    config: PipelineConfig,
) -> dict[str, Any] | None:
    reason_codes: list[str] = []
    error_rate = 0.0
    error_kind = ""

    expected = target.expected_label_class
    if expected in {"negative", "mixed"} and metrics.get("n_negative", 0) > 0:
        fp_rate = float(metrics.get("false_positive_rate", 0.0) or 0.0)
        if fp_rate > error_rate:
            error_rate = fp_rate
            error_kind = "false_positive"

    if expected in {"positive", "mixed"} and metrics.get("n_positive", 0) > 0:
        fn_rate = float(metrics.get("false_negative_rate", 0.0) or 0.0)
        if fn_rate > error_rate:
            error_rate = fn_rate
            error_kind = "false_negative"

    priority = 3
    score = coverage_deficit
    requested_minutes = _clamp_minutes(coverage_deficit, config)

    if error_rate >= config.collection.high_error_rate:
        priority = 1
        score = 100.0 + error_rate + coverage_deficit / max(target_minutes, 1.0)
        requested_minutes = config.collection.high_error_minutes
        reason_codes.append(f"{error_kind}_rate_high")
    elif error_rate >= config.collection.moderate_error_rate:
        priority = 2
        score = 50.0 + error_rate + coverage_deficit / max(target_minutes, 1.0)
        requested_minutes = config.collection.moderate_error_minutes
        reason_codes.append(f"{error_kind}_rate_moderate")

    if coverage_deficit > 0.25:
        reason_codes.append("coverage_gap")
        score += coverage_deficit / max(target_minutes, 1.0)

    if not reason_codes:
        return None

    if requested_minutes <= 0:
        requested_minutes = config.collection.coverage_min_minutes

    return {
        "id": "",
        "activity": target.activity,
        "activity_label": target.activity_label,
        "expected_label_class": target.expected_label_class,
        "priority": priority,
        "requested_minutes": int(requested_minutes),
        "reason_codes": reason_codes,
        "reason": _reason_text(target, reason_codes[0], current_minutes, target_minutes),
        "current_minutes": round(current_minutes, 2),
        "target_minutes": round(target_minutes, 2),
        "_priority": priority,
        "_score": score,
    }


def _clamp_minutes(deficit: float, config: PipelineConfig) -> int:
    if deficit <= 0:
        return 0
    return int(
        min(
            config.collection.coverage_max_minutes,
            max(config.collection.coverage_min_minutes, math.ceil(deficit)),
        )
    )


def _reason_text(
    target: CollectionTargetConfig,
    reason_code: str,
    current_minutes: float,
    target_minutes: float,
) -> str:
    label = target.activity_label
    if reason_code == "false_positive_rate_high":
        return f"{label} negatives still trigger chewing too often."
    if reason_code == "false_positive_rate_moderate":
        return f"{label} has some false positives and needs more examples."
    if reason_code == "false_negative_rate_high":
        return f"{label} chewing is still being missed too often."
    if reason_code == "false_negative_rate_moderate":
        return f"{label} has some missed chewing and needs more examples."
    return (
        f"{label} is under target coverage "
        f"({current_minutes:.1f}/{target_minutes:.1f} min)."
    )


def _activity_minutes(curated_dir: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    if not curated_dir.exists():
        return out

    for session_dir in sorted(curated_dir.iterdir()):
        if not session_dir.is_dir():
            continue
        csvs = list(session_dir.glob("*.csv"))
        if not csvs:
            continue
        activity = _activity_for_session(session_dir)
        if not activity:
            continue
        try:
            df = pd.read_csv(csvs[0])
        except Exception:
            continue
        duration_sec = _duration_seconds(df)
        out[activity] = out.get(activity, 0.0) + (duration_sec / 60.0)
    return out


def _activity_for_session(session_dir: Path) -> str | None:
    meta_path = session_dir / "_metadata.txt"
    if meta_path.exists():
        try:
            with open(meta_path, "r") as f:
                meta = json.load(f)
            activity = meta.get("activity")
            if not activity:
                source = meta.get("source_metadata")
                if isinstance(source, dict):
                    activity = source.get("activity")
            if activity:
                return str(activity)
        except (OSError, json.JSONDecodeError):
            pass

    lower = session_dir.name.lower()
    if lower.startswith("eating"):
        return "eating"
    if lower.startswith("not-eating") or lower.startswith("noteating"):
        return "not_eating"
    return None


def _duration_seconds(df: pd.DataFrame) -> float:
    if "timestamp" in df.columns:
        try:
            timestamps = df["timestamp"].astype(float)
            if len(timestamps) >= 2:
                duration = float(timestamps.max() - timestamps.min())
                if math.isfinite(duration) and duration > 0:
                    return duration
        except Exception:
            pass
    return float(len(df)) / 100.0


def _int(value: Any) -> int:
    if isinstance(value, (int, float)) and math.isfinite(value):
        return int(value)
    return 0


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "activity"
