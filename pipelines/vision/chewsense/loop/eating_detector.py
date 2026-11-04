"""Status of the experimental AirPods chewing-bout detector for dashboards.

The detector is trained by ``chewsense-motion eating-experiment`` into
``<workspace>/experiments/eating``.  This module only reads that run's JSON so
the service process never imports training code.  The summary is descriptive:
it never gates training, promotion or the collection plan.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MODEL_LABELS = {
    "prior": "prior",
    "motion_energy": "motion energy",
    "logreg": "logistic regression",
    "hgb": "gradient boosting",
    "tcn": "causal TCN",
}


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _round(value: Any, digits: int = 3) -> float | None:
    return round(float(value), digits) if isinstance(value, (int, float)) else None


def eating_detector_summary(experiments_root: Path) -> dict[str, Any]:
    latest = _read(Path(experiments_root) / "eating" / "latest.json")
    if not latest:
        return {
            "available": False,
            "status": "No chewing-bout experiment has run. Run: chewsense-motion eating-experiment",
        }
    run_dir = Path(latest.get("run_dir", ""))
    metrics = _read(run_dir / "metrics.json") or {}
    selected = metrics.get("selected_model") or latest.get("selected_model")
    result = (metrics.get("results") or {}).get(selected) or {}
    test = result.get("test") or {}
    dev = result.get("dev_oof") or {}
    weakest = []
    for split_name, split in (("test", test), ("development", dev)):
        for context, values in (split.get("by_context") or {}).items():
            fpr, negatives = values.get("false_positive_rate"), values.get("negatives") or 0
            if fpr is not None and negatives >= 10 and fpr > 0.05:
                weakest.append({"context": context, "split": split_name,
                                "false_positive_rate": _round(fpr), "negatives": negatives})
    weakest.sort(key=lambda item: -(item["false_positive_rate"] or 0))
    counts = metrics.get("counts") or latest.get("counts") or {}

    def scores(split: dict[str, Any]) -> dict[str, Any] | None:
        if not split:
            return None
        ci = (split.get("ci95") or {}).get("f1") or {}
        bouts = split.get("bouts") or {}
        return {
            "f1": _round(split.get("f1")),
            "f1_ci95": [_round(ci.get("low")), _round(ci.get("high"))] if ci.get("low") is not None else None,
            "precision": _round(split.get("precision")),
            "recall": _round(split.get("recall")),
            "false_positive_rate": _round(split.get("false_positive_rate")),
            "sessions": split.get("sessions"),
            "chewing_windows": split.get("positives"),
            "non_chewing_windows": split.get("negatives"),
            "bouts_detected": bouts.get("detected_bouts"),
            "bouts_reference": bouts.get("reference_bouts"),
            "false_bouts": bouts.get("false_bouts"),
            "onset_latency_median_s": _round(bouts.get("onset_latency_median_s"), 2),
        }

    test_scores, dev_scores = scores(test), scores(dev)
    label = MODEL_LABELS.get(selected, selected or "none")
    if test_scores and test_scores["f1"] is not None:
        ci = test_scores["f1_ci95"]
        status = (
            f"{label}: test F1 {test_scores['f1']:.2f}"
            + (f" (95% CI {ci[0]:.2f}–{ci[1]:.2f})" if ci else "")
            + f" on {test_scores['sessions']} recordings, one wearer. Experimental; not deployed."
        )
    else:
        status = f"{label}: no locked-test result yet. Experimental; not deployed."
    recommendations = [
        {k: item.get(k) for k in ("title", "reason", "experiment_id")}
        for item in (metrics.get("recommendations") or [])[:4]
    ]
    return {
        "available": True,
        "run_id": latest.get("run_id"),
        "finished_at": latest.get("finished_at"),
        "selected_model": selected,
        "selected_model_label": label,
        "dataset_fingerprint": latest.get("dataset_fingerprint"),
        "label_target": "human coarse chewing spans (not individual chews or bites)",
        "counts": counts,
        "development": dev_scores,
        "test": test_scores,
        "weakest_contexts": weakest[:4],
        "recommendations": recommendations,
        "report_path": latest.get("report_path") or str(run_dir / "report.html"),
        "test_consultations": (_read(Path(experiments_root) / "eating" / "index.json") or {}).get("test_consultations"),
        "status": status,
        "deployed": False,
    }


def eating_detector_stage(summary: dict[str, Any]) -> dict[str, str]:
    if not summary.get("available"):
        return dict(id="eating-detector", title="AirPods chewing detector (experimental)", state="waiting",
                    detail=summary.get("status", "Not run yet"))
    detail = summary["status"]
    if summary.get("weakest_contexts"):
        worst = summary["weakest_contexts"][0]
        detail += f" Most false alarms: {worst['context']} ({worst['false_positive_rate']:.0%})."
    finished = (summary.get("finished_at") or "")[:10]
    if finished:
        detail += f" Trained {finished}."
    return dict(id="eating-detector", title="AirPods chewing detector (experimental)", state="complete",
                detail=detail)
