"""Promotion policy for models deployed to the motion-only Live app."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PromotionThresholds:
    eating_recall: float = 0.90
    hard_negative_fpr: float = 0.15
    chew_f1_at_250ms: float = 0.80
    chew_count_error: float = 0.10
    verified_eating_sessions: int = 20
    verified_hard_negative_sessions: int = 20


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


def registry_entry_for_model(repo_root: Path, model_path: Path) -> Path | None:
    """Find the newest registry entry containing the exact checkpoint."""
    runs = Path(repo_root) / "02-DataPipeline" / "model_registry" / "runs"
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
        elif label in {"not-eating", "not_eating", "hard-negative", "hard_negative"}:
            negatives += 1
    return eating, negatives


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
    metrics = _load_json(entry / "metrics.json")
    manifest = _load_json(entry / "dataset_manifest.json")
    split, holdout = _evaluation_metrics(metrics)
    checks: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []

    if split is None:
        reasons.append("no test_locked or gold_locked evaluation is recorded")

    hard_negative_fpr = holdout.get("hard_negative_fpr")

    chew_f1 = holdout.get("chew_f1_at_250ms")
    if chew_f1 is None:
        chew_f1 = holdout.get("chew_f1_250ms")

    count_error = holdout.get("chew_count_error")
    if count_error is None:
        count_error = holdout.get("chew_count_error_fraction")

    verified_eating, verified_negatives = _session_counts(manifest)
    _metric_check(
        checks, reasons, "eating_recall", holdout.get("recall"),
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


def write_promotion_report(entry: Path, result: PromotionResult) -> Path:
    report = {
        "schema_version": 1,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        **result.to_dict(),
    }
    output = entry / "promotion_report.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    return output
