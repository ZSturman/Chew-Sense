"""Promotion policy for models deployed to the motion-only Live app."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import workspace_root


@dataclass(frozen=True)
class PromotionThresholds:
    eating_recall: float = 0.90
    hard_negative_fpr: float = 0.15
    chew_f1_at_250ms: float = 0.80
    chew_count_error: float = 0.10
    median_inter_chew_interval_mae_sec: float = 0.100
    verified_eating_sessions: int = 20
    verified_hard_negative_sessions: int = 20


@dataclass(frozen=True)
class FoodEntryPromotionThresholds:
    """Independent gate; failure never blocks cadence-only chew promotion."""

    food_entry_f1_at_500ms: float = 0.80
    food_entry_count_error: float = 0.15
    false_entries_per_negative_minute: float = 0.20
    per_bite_chew_count_mae: float = 2.0


@dataclass
class PromotionResult:
    passed: bool
    model_id: str | None
    evaluation_split: str | None
    checks: dict[str, dict[str, Any]]
    reasons: list[str]
    development_override: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_contract(entry: Path) -> dict[str, Any]:
    checkpoint_path = entry / "chewnet.pth"
    if not checkpoint_path.exists():
        return {}
    try:
        import torch

        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    except Exception:
        return {}
    output_names = checkpoint.get("output_names", [])
    return {
        "model_mode": checkpoint.get("mode"),
        "temporal_contract_version": checkpoint.get("temporal_contract_version"),
        "output_names": output_names,
        "food_entry_head": "food_entry_logit" in output_names,
        "dataset_hash": checkpoint.get("dataset_hash"),
    }


def registry_entry_for_model(repo_root: Path, model_path: Path) -> Path | None:
    """Find the newest registry entry containing the exact checkpoint."""
    del repo_root
    runs = workspace_root() / "models" / "registry"
    if not runs.exists() or not model_path.exists():
        return None
    target_hash = _file_sha256(model_path)
    matches = []
    for entry in runs.iterdir():
        candidate = entry / "chewnet.pth"
        if entry.is_dir() and candidate.exists() and _file_sha256(candidate) == target_hash:
            matches.append(entry)
    return max(matches, key=lambda path: path.name) if matches else None


def _evaluation_metrics(metrics: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    # Promotion is deliberately evaluated only on a locked holdout. Validation
    # metrics remain useful during development but cannot promote a model.
    for split in ("test_locked", "gold_locked"):
        value = metrics.get(split)
        if isinstance(value, dict) and value:
            return split, value
    if metrics.get("split") in {"test_locked", "gold_locked"}:
        return str(metrics["split"]), metrics
    return None, {}


def _session_counts(manifest: dict[str, Any]) -> tuple[int, int]:
    eating = 0
    negatives = 0
    for session in manifest.get("sessions", []):
        if not isinstance(session, dict) or session.get("verified") is not True:
            continue
        label = str(session.get("label", "")).lower()
        if label == "eating":
            eating += 1
        elif label in {"hard-negative", "hard_negative"} or session.get("hard_negative") is True:
            negatives += 1
    return eating, negatives


def _locked_dataset_lineage(
    entry: Path,
    *,
    evaluation_split: str | None,
    metadata: dict[str, Any],
    checkpoint_contract: dict[str, Any],
    dataset_manifest: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    split_manifest = _load_json(entry / "split_manifest.json")
    dataset_hashes = {
        "checkpoint": checkpoint_contract.get("dataset_hash"),
        "registry_metadata": metadata.get("dataset_hash"),
        "dataset_manifest": dataset_manifest.get("dataset_hash"),
        "split_manifest": split_manifest.get("dataset_hash"),
    }
    nonempty_hashes = [value for value in dataset_hashes.values() if value]
    hashes_match = len(nonempty_hashes) == 4 and len(set(nonempty_hashes)) == 1
    split_ids = set(
        split_manifest.get("splits", {}).get(evaluation_split, [])
        if evaluation_split
        else []
    )
    session_by_id = {
        str(item.get("session_id")): item
        for item in dataset_manifest.get("sessions", [])
        if isinstance(item, dict) and item.get("session_id")
    }
    locked = [session_by_id[session_id] for session_id in split_ids if session_id in session_by_id]
    all_present = bool(split_ids) and len(locked) == len(split_ids)
    fully_reviewed = bool(locked) and all(
        item.get("verified") is True and item.get("event_supervision") is True
        for item in locked
    )
    has_eating = any(str(item.get("label", "")).lower() == "eating" for item in locked)
    has_hard_negative = any(
        str(item.get("label", "")).lower() in {"hard-negative", "hard_negative"}
        or item.get("hard_negative") is True
        for item in locked
    )
    temporal_dataset = (
        dataset_manifest.get("schema_version") == "2.0.0"
        and dataset_manifest.get("artifact_type") == "temporal_motion_dataset"
        and dataset_manifest.get("temporal_contract_version") == "2.0.0"
        and split_manifest.get("version") == 2
    )
    actual = {
        "dataset_hashes": dataset_hashes,
        "hashes_match": hashes_match,
        "split_session_count": len(split_ids),
        "all_split_sessions_present": all_present,
        "fully_reviewed": fully_reviewed,
        "has_eating": has_eating,
        "has_hard_negative": has_hard_negative,
        "temporal_dataset": temporal_dataset,
    }
    return (
        hashes_match
        and all_present
        and fully_reviewed
        and has_eating
        and has_hard_negative
        and temporal_dataset,
        actual,
    )


def _metric_check(
    checks: dict[str, dict[str, Any]],
    reasons: list[str],
    name: str,
    actual: Any,
    threshold: float | int,
    comparison: str,
) -> None:
    numeric = isinstance(actual, (int, float)) and not isinstance(actual, bool)
    passed = numeric and (
        actual >= threshold if comparison == "minimum" else actual <= threshold
    )
    checks[name] = {
        "actual": actual,
        "threshold": threshold,
        "comparison": comparison,
        "passed": bool(passed),
    }
    if not passed:
        rendered = "missing" if actual is None else actual
        operator = ">=" if comparison == "minimum" else "<="
        reasons.append(f"{name} is {rendered}; requires {operator} {threshold}")


def assess_registry_entry(
    entry: Path,
    thresholds: PromotionThresholds | None = None,
) -> PromotionResult:
    """Assess one versioned registry entry against the Live promotion policy."""
    thresholds = thresholds or PromotionThresholds()
    metadata = _load_json(entry / "metadata.json")
    checkpoint_contract = _checkpoint_contract(entry)
    metrics = _load_json(entry / "metrics.json")
    manifest = _load_json(entry / "dataset_manifest.json")
    split, holdout = _evaluation_metrics(metrics)
    checks: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []

    model_mode = checkpoint_contract.get("model_mode")
    temporal_contract = checkpoint_contract.get("temporal_contract_version")
    metadata_matches = (
        metadata.get("model_mode") == model_mode
        and metadata.get("temporal_contract_version") == temporal_contract
    )
    mode_passed = (
        model_mode == "temporal_tcn"
        and temporal_contract == "2.0.0"
        and metadata_matches
    )
    checks["temporal_model_contract"] = {
        "actual": {
            "model_mode": model_mode,
            "temporal_contract_version": temporal_contract,
            "registry_metadata_matches": metadata_matches,
        },
        "threshold": {"model_mode": "temporal_tcn", "temporal_contract_version": "2.0.0"},
        "comparison": "exact",
        "passed": mode_passed,
    }
    if not mode_passed:
        reasons.append(
            "checkpoint is not a temporal_tcn with temporal contract 2.0.0; "
            "legacy binary/phase models cannot promote individual-chew coaching"
        )

    lineage_passed, lineage_actual = _locked_dataset_lineage(
        entry,
        evaluation_split=split,
        metadata=metadata,
        checkpoint_contract=checkpoint_contract,
        dataset_manifest=manifest,
    )
    checks["locked_dataset_lineage"] = {
        "actual": lineage_actual,
        "threshold": "exact v2 hashes and reviewed eating/hard-negative holdout",
        "comparison": "exact",
        "passed": lineage_passed,
    }
    if not lineage_passed:
        reasons.append(
            "locked holdout lineage is incomplete: require matching checkpoint/dataset/split "
            "hashes and fully reviewed eating plus hard-negative sessions"
        )

    if split is None:
        reasons.append("no test_locked or gold_locked evaluation is recorded")

    hard_negative_fpr = holdout.get("hard_negative_fpr")

    chew_f1 = holdout.get("chew_f1_at_250ms")
    if chew_f1 is None:
        chew_f1 = holdout.get("chew_f1_250ms")

    count_error = holdout.get("chew_count_error")
    if count_error is None:
        count_error = holdout.get("chew_count_error_fraction")

    ici_mae = holdout.get("median_inter_chew_interval_mae_sec")
    if ici_mae is None:
        ici_mae = holdout.get("inter_chew_interval_mae_sec")

    verified_eating, verified_negatives = _session_counts(manifest)
    _metric_check(
        checks, reasons, "eating_recall", holdout.get("eating_recall", holdout.get("recall")),
        thresholds.eating_recall, "minimum",
    )
    _metric_check(
        checks, reasons, "hard_negative_fpr", hard_negative_fpr,
        thresholds.hard_negative_fpr, "maximum",
    )
    _metric_check(
        checks, reasons, "chew_f1_at_250ms", chew_f1,
        thresholds.chew_f1_at_250ms, "minimum",
    )
    _metric_check(
        checks, reasons, "chew_count_error", count_error,
        thresholds.chew_count_error, "maximum",
    )
    _metric_check(
        checks, reasons, "median_inter_chew_interval_mae_sec", ici_mae,
        thresholds.median_inter_chew_interval_mae_sec, "maximum",
    )
    _metric_check(
        checks, reasons, "verified_eating_sessions", verified_eating,
        thresholds.verified_eating_sessions, "minimum",
    )
    _metric_check(
        checks, reasons, "verified_hard_negative_sessions", verified_negatives,
        thresholds.verified_hard_negative_sessions, "minimum",
    )

    return PromotionResult(
        passed=split is not None and not reasons,
        model_id=metadata.get("model_id") or entry.name,
        evaluation_split=split,
        checks=checks,
        reasons=reasons,
    )


def assess_food_entry_head(
    entry: Path,
    thresholds: FoodEntryPromotionThresholds | None = None,
) -> PromotionResult:
    """Assess the experimental food-entry head independently.

    Live may expose bite counts or chews-per-bite only when this result is
    recorded as passed for the exact deployed model.  A promoted chew model
    can therefore ship cadence coaching while this capability remains false.
    """

    thresholds = thresholds or FoodEntryPromotionThresholds()
    metadata = _load_json(entry / "metadata.json")
    checkpoint_contract = _checkpoint_contract(entry)
    metrics = _load_json(entry / "metrics.json")
    split, holdout = _evaluation_metrics(metrics)
    food = holdout.get("food_entry") if isinstance(holdout.get("food_entry"), dict) else holdout
    checks: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    if split is None:
        reasons.append("no test_locked or gold_locked evaluation is recorded")
    has_food_entry_head = (
        checkpoint_contract.get("food_entry_head") is True
        and metadata.get("food_entry_head") is True
    )
    checks["food_entry_head"] = {
        "actual": {
            "checkpoint": checkpoint_contract.get("food_entry_head"),
            "registry_metadata": metadata.get("food_entry_head"),
        },
        "threshold": True,
        "comparison": "exact",
        "passed": has_food_entry_head,
    }
    if not has_food_entry_head:
        reasons.append("the exact checkpoint has no food_entry_logit output")
    lineage_passed, lineage_actual = _locked_dataset_lineage(
        entry,
        evaluation_split=split,
        metadata=metadata,
        checkpoint_contract=checkpoint_contract,
        dataset_manifest=_load_json(entry / "dataset_manifest.json"),
    )
    checks["locked_dataset_lineage"] = {
        "actual": lineage_actual,
        "threshold": "exact v2 hashes and reviewed eating/hard-negative holdout",
        "comparison": "exact",
        "passed": lineage_passed,
    }
    if not lineage_passed:
        reasons.append("food-entry evaluation is not tied to a valid locked v2 holdout")

    values = (
        (
            "food_entry_f1_at_500ms",
            food.get("food_entry_f1_at_500ms", food.get("event_f1_at_500ms")),
            thresholds.food_entry_f1_at_500ms,
            "minimum",
        ),
        (
            "food_entry_count_error",
            food.get("food_entry_count_error", food.get("count_error")),
            thresholds.food_entry_count_error,
            "maximum",
        ),
        (
            "false_entries_per_negative_minute",
            food.get("false_entries_per_negative_minute"),
            thresholds.false_entries_per_negative_minute,
            "maximum",
        ),
        (
            "per_bite_chew_count_mae",
            food.get("per_bite_chew_count_mae"),
            thresholds.per_bite_chew_count_mae,
            "maximum",
        ),
    )
    for name, actual, threshold, comparison in values:
        _metric_check(checks, reasons, name, actual, threshold, comparison)
    return PromotionResult(
        passed=split is not None and not reasons,
        model_id=metadata.get("model_id") or entry.name,
        evaluation_split=split,
        checks=checks,
        reasons=reasons,
    )


def write_promotion_report(entry: Path, result: PromotionResult) -> Path:
    report = {
        "schema_version": 1,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        **result.to_dict(),
    }
    output = entry / "promotion_report.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    return output


def write_food_entry_promotion_report(entry: Path, result: PromotionResult) -> Path:
    report = {
        "schema_version": 1,
        "capability": "food_entry",
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        **result.to_dict(),
    }
    output = entry / "food_entry_promotion_report.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    return output
