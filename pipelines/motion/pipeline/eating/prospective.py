"""Prospective scores of the exported detector on recordings made after it.

Cross-validation re-uses every development recording for fitting, and the
locked test may only be consulted for a confirmation.  A third kind of
evidence costs neither: a detector that was exported *before* a recording was
captured has never seen it.  Scoring that recording once, with the detector's
own threshold and decoder and the same streaming replay Live runs, is a
genuine forward test.  The ledger keeps the first score of each recording for
each detector and never rewrites it, so the evidence accumulates as
recordings arrive and cannot be re-tuned afterwards.

Only development recordings are scored.  Locked-test recordings are skipped,
so the test-consultation count is untouched.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .dataset import EatingSession, build_windows, dataset_fingerprint
from .evaluate import bout_metrics, grouped_window_metrics, recording_level, window_metrics
from .runtime import decoder_config, run_stream
from .splits import DEV


def _brief(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {k: metrics.get(k) for k in (
        "windows", "positives", "negatives", "tp", "fp", "tn", "fn", "precision", "recall", "f1",
        "false_positive_rate", "auc_roc", "auc_pr")}


def _utc_timestamp(value: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None


def _capture_started_at(session: EatingSession) -> datetime | None:
    """Use an absolute capture time, never compare a local filename with UTC.

    Collect writes its manifest when capture finishes. Subtracting the whole
    duration is conservative: a take crossing the training cutoff is excluded.
    Legacy-import manifests record migration time, so cannot establish this.
    """

    if session.source_kind != "canonical":
        return _utc_timestamp(session.created_at)
    try:
        manifest = json.loads((Path(session.source_path) / "session.json").read_text())
    except (OSError, ValueError):
        return None
    if manifest.get("source_app") == "legacy_import":
        return None
    stamp = _utc_timestamp(str(manifest.get("created_at") or ""))
    return stamp - timedelta(seconds=max(session.duration_s, 0.0)) if stamp is not None else None


def score_recording(spec: Mapping[str, Any], session: EatingSession) -> dict[str, Any]:
    """Replay one recording through the exported detector and compare with its human labels."""

    windows = build_windows([session], window_s=float(spec["input"]["window_s"]),
                            stride_s=float(spec["decision"]["stride_s"]),
                            min_valid_fraction=float(spec["input"]["min_valid_fraction"]))
    stream = run_stream(spec, session.motion_t, session.imu, windows.t_end)
    probability = stream["probability"].to_numpy()
    threshold = float(spec["decision"]["threshold"])
    scored = (windows.label >= 0) & np.isfinite(probability)
    y, p = windows.label[scored].astype(int), probability[scored]
    bouts = bout_metrics(session, windows.t_end, probability, decoder_config(spec))
    return {
        "session_id": session.session_id,
        "recorded_at": session.created_at,
        "activity": session.activity,
        "clock": session.clock,
        "label_fingerprint": dataset_fingerprint([session], {"window_s": float(spec["input"]["window_s"])}),
        "threshold": threshold,
        "windows": _brief(window_metrics(y, p, threshold)) if len(y) else None,
        "by_context": {
            context: _brief(block)
            for context, block in grouped_window_metrics(windows.context[scored], y, p, threshold).items()
        } if len(y) else {},
        "bouts": {k: bouts[k] for k in (
            "reference_bouts", "detected_bouts", "false_bouts", "chewing_seconds", "chewing_time_covered_s",
            "negative_seconds", "negative_time_flagged_s")},
        "onset_latency_s": [ref["onset_latency_s"] for ref in bouts["references"]],
        "recording_decision": recording_level({session.session_id: bouts})["recordings"][0],
    }


def update_ledger(
    *,
    spec: Mapping[str, Any],
    sessions: Sequence[EatingSession],
    split_of: Mapping[str, str],
    trained_ids: set[str],
    model_finished_at: str,
    ledger_path: Path,
) -> dict[str, Any]:
    """Score every eligible recording that is not in the ledger yet; return the ledger.

    Eligible: development split, absent from the detector's training run, and
    captured after that run finished, established with timezone-aware times.
    Ambiguous local times and legacy migration times cannot establish that.
    Existing entries are never recomputed.
    """

    ledger_path = Path(ledger_path)
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {
        "artifact_type": "eating_prospective_ledger",
        "model_id": spec["model_id"],
        "model_run_id": spec["provenance"]["run_id"],
        "model_finished_at": model_finished_at,
        "rule": "development recordings captured after the detector's training run, scored once and never rescored",
        "entries": [],
    }
    if ledger["model_id"] != spec["model_id"]:
        raise ValueError(f"{ledger_path.name} belongs to detector {ledger['model_id']}")
    known = {entry["session_id"] for entry in ledger["entries"]}
    cutoff = _utc_timestamp(model_finished_at)
    if cutoff is None:
        raise ValueError("model_finished_at must include a timezone")
    added = []
    for session in sorted(sessions, key=lambda s: (s.created_at, s.session_id)):
        if (split_of.get(session.session_id) != DEV or session.session_id in trained_ids
                or session.session_id in known):
            continue
        captured = _capture_started_at(session)
        if captured is None or captured <= cutoff:
            continue
        entry = score_recording(spec, session)
        entry["capture_started_at_utc"] = captured.isoformat()
        entry["scored_at"] = datetime.now(timezone.utc).isoformat()
        ledger["entries"].append(entry)
        added.append(session.session_id)
    ledger["summary"] = summarize(ledger["entries"])
    if added or not ledger_path.exists():
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True, default=float) + "\n")
    ledger["added_now"] = added
    return ledger


def summarize(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pooled counts over all prospective recordings (windows overlap; the unit is the recording)."""

    totals = {k: 0 for k in ("tp", "fp", "tn", "fn")}
    contexts: dict[str, dict[str, int]] = {}
    for entry in entries:
        for key in totals:
            totals[key] += int((entry.get("windows") or {}).get(key) or 0)
        for context, block in (entry.get("by_context") or {}).items():
            bucket = contexts.setdefault(context, {k: 0 for k in ("tp", "fp", "tn", "fn")})
            for key in bucket:
                bucket[key] += int(block.get(key) or 0)

    def rates(c: Mapping[str, int]) -> dict[str, Any]:
        return {
            **c,
            "recall": c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else None,
            "false_positive_rate": c["fp"] / (c["fp"] + c["tn"]) if c["fp"] + c["tn"] else None,
        }

    return {
        "recordings": len(entries),
        "recordings_with_chewing": sum(1 for e in entries if (e.get("bouts") or {}).get("chewing_seconds", 0) > 0),
        "bouts_detected": sum((e.get("bouts") or {}).get("detected_bouts", 0) for e in entries),
        "bouts": sum((e.get("bouts") or {}).get("reference_bouts", 0) for e in entries),
        "false_bouts": sum((e.get("bouts") or {}).get("false_bouts", 0) for e in entries),
        "windows": rates(totals),
        "by_context": {context: rates(bucket) for context, bucket in sorted(contexts.items())},
    }
