"""What happened after the recordings were uploaded, in one file.

``pipeline_status.json`` answers, for the Review app and the command line:

1. which recordings exist (including rejected ones and why);
2. whether each passed synchronization and quality checks;
3. which have usable labels, of which provenance;
4. how much usable training data exists;
5. which recordings are development or locked test;
6. whether a model has been trained, and on exactly this data;
7. which model is selected, bundled in Live and driving Live;
8. how well it performs (development and locked test, with intervals);
9. where its predictions are wrong (per-recording timelines);
10. what to record next.

It only reads existing artifacts.  Nothing here trains, labels, assigns
splits, or consults the locked test set.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .dataset import (
    ASSISTED_TIERS,
    CHEWING,
    EatingSession,
    build_windows,
    dataset_fingerprint,
    discover_sessions,
)
from .evaluate import DecoderConfig, decode_bouts

FINGERPRINT_KEYS = ("window_s", "stride_s", "guard_s", "min_valid_fraction", "training_tiers")


def _read(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _round(value: Any, digits: int = 4) -> Any:
    if isinstance(value, float):
        return None if not np.isfinite(value) else round(value, digits)
    return value


def _loop_records(loop_root: Path) -> dict[str, dict[str, Any]]:
    records = {}
    for path in sorted((loop_root / "state" / "sessions").glob("*.json")):
        record = _read(path) or {}
        records[str(record.get("session_id") or path.stem)] = record
    return records


def _sync(session: EatingSession, record: Mapping[str, Any] | None, loop_root: Path) -> dict[str, Any]:
    """Clock and quality facts, stated as measured, assumed or unknown."""

    if record:
        assessment = _read(loop_root / "reports" / "sessions" / session.session_id / "sync-assessment.json") or {}
        guided = _read(loop_root / "reports" / "sessions" / session.session_id / "guided-check.json") or {}
        failed = list(record.get("failed_checks") or guided.get("failed_checks") or [])
        return {
            "clock": session.clock,
            "sync_check": record.get("sync_assessment") or assessment.get("status") or "not assessed",
            "sync_detail": assessment.get("message") or assessment.get("summary"),
            "sensor_coverage": _round(record.get("sensor_coverage")),
            "motion_gaps": record.get("motion_gaps"),
            "quality_checks": "passed" if not failed else "failed: " + ", ".join(map(str, failed)),
        }
    return {
        "clock": session.clock,
        "sync_check": "not marker-checked" if session.clock == "shared_host_clock"
        else "clock origin = first motion row",
        "sync_detail": None,
        "sensor_coverage": None,
        "motion_gaps": None,
        "quality_checks": "not run (recorded before guided quality checks)",
    }


def _labels(session: EatingSession) -> dict[str, Any]:
    seconds: dict[str, float] = {}
    chewing = 0.0
    for item in session.intervals:
        seconds[item.tier] = seconds.get(item.tier, 0.0) + item.end - item.start
        if item.label == CHEWING:
            chewing += item.end - item.start
    assisted = sum(v for k, v in seconds.items() if k in ASSISTED_TIERS)
    source = Path(session.source_path)
    reviews = sorted((source / "annotations").glob("coarse-review-v*.json")) if source.is_dir() else []
    return {
        "labelled_s": round(sum(seconds.values()), 1),
        "chewing_s": round(chewing, 1),
        "by_tier_s": {k: round(v, 1) for k, v in sorted(seconds.items())},
        "assisted_s": round(assisted, 1),
        "latest_assisted_review": reviews[-1].name if reviews else None,
        "chewing_bouts": sum(1 for i in session.intervals if i.label == CHEWING),
    }


def _proposal_state(session_id: str, workspace: Path) -> dict[str, Any] | None:
    for root in (workspace / "loop" / "assisted" / "derived" / "assist", workspace / "derived" / "assist"):
        revisions = sorted((root / session_id).glob("coarse-proposals-v*.json"))
        if revisions:
            payload = _read(revisions[-1]) or {}
            return {"file": str(revisions[-1]), **{k: (payload.get("summary") or {}).get(k) for k in (
                "coverage", "review_items", "batch_items", "label_conflicts")}}
    return None


def _has_video(session: EatingSession) -> bool:
    folder = Path(session.source_path)
    return folder.is_dir() and any(p.suffix.lower() in {".mov", ".mp4"} for p in folder.iterdir())


def _live_models(repo: Path, workspace: Path | None = None) -> dict[str, Any]:
    spec = _read(repo / "apps/live-ios/ChewSenseLive/EatingDetector/eating-detector.json") or {}
    constants = repo / "apps/live-ios/ChewSenseLive/Generated/NormalizationConstants.swift"
    chewnet = None
    if constants.exists():
        match = re.search(r'MODEL_ID\s*(?::\s*String\s*)?=\s*"([^"]*)"', constants.read_text())
        chewnet = match.group(1) if match else None
    validation = None
    if workspace is not None and spec.get("model_id"):
        validation = _read(workspace / "experiments/eating/deploy" / spec["model_id"] / "physical-validation.json")
    physical_status = "none recorded"
    if validation and validation.get("model_id") == spec.get("model_id"):
        physical_status = validation.get("summary") or physical_status
    return {
        "exported_detector": {
            "model_id": spec.get("model_id"),
            "status": spec.get("status"),
            "run_id": (spec.get("provenance") or {}).get("run_id"),
            "threshold": (spec.get("decision") or {}).get("threshold"),
            "mode_in_live": "shadow by default (logged to eating_detector.csv); drives Live only when the "
                            "experimental mode is chosen in Settings",
        } if spec else None,
        "chewnet": {
            "model_id": chewnet,
            "role": "drives Live by default; trained May 2026 on 6 field tests; test F1 0.50 in the November audit",
        },
        "physical_device_validation": physical_status,
    }


def _timelines(frame: pd.DataFrame, column: str, threshold: float, sessions: Mapping[str, EatingSession]) -> dict[str, Any]:
    """Compact per-recording prediction timelines for Review (rounded, one row per window)."""

    decoder = DecoderConfig.from_threshold(threshold)
    result = {}
    for session_id, rows in frame.groupby("session_id", sort=True):
        rows = rows.sort_values("t_end")
        t = rows["t_end"].to_numpy()
        p = rows[column].to_numpy(dtype=float)
        states, bouts = decode_bouts(t, p, decoder)
        label = rows["label"].to_numpy()
        session = sessions.get(session_id)
        result[session_id] = {
            "split": str(rows["split"].iloc[0]),
            "t_end": [round(float(v), 2) for v in t],
            "probability": [None if not np.isfinite(v) else round(float(v), 3) for v in p],
            "label": [int(v) for v in label],
            "decoded": [int(v) for v in states],
            "bouts": [[round(a, 2), round(b, 2)] for a, b in bouts],
            "human_intervals": [
                [round(i.start, 2), round(i.end, 2), i.label, i.tier] for i in (session.intervals if session else [])
            ],
            "guided_context": [
                [round(a, 2), round(b, 2), kind] for a, b, kind in (session.context_spans if session else [])
            ],
            "false_positive_windows": int(((label == 0) & (p >= threshold)).sum()),
            "false_negative_windows": int(((label == 1) & (p < threshold)).sum()),
        }
    return result


def build_status(*, workspace: Path, repo: Path) -> dict[str, Any]:
    workspace, repo = Path(workspace), Path(repo)
    loop_root = workspace / "loop"
    eating_root = workspace / "experiments" / "eating"
    sessions, rejected = discover_sessions(
        canonical_roots=[workspace / "sessions" / "raw", loop_root / "assisted" / "sessions" / "raw"],
        field_test_roots=[workspace / "sessions" / "field-tests"],
        loop_root=loop_root,
    )
    by_id = {s.session_id: s for s in sessions}
    records = _loop_records(loop_root)
    splits = ((_read(eating_root / "splits.json") or {}).get("assignments")) or {}
    latest = _read(eating_root / "latest.json") or {}
    run_dir = Path(latest["run_dir"]) if latest.get("run_dir") else None
    metrics = _read(run_dir / "metrics.json") if run_dir else None
    index = _read(eating_root / "index.json") or {"runs": []}
    # latest.json deliberately pins the last test-evaluated export candidate.
    # The dashboard must instead show the newest completed experiment, including
    # dev-only checks, with its own (possibly absent) test scores.
    for run in reversed(index.get("runs") or []):
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or Path(run_id).name != run_id:
            continue
        candidate_dir = eating_root / "runs" / run_id
        candidate_metrics = _read(candidate_dir / "metrics.json")
        if candidate_metrics:
            run_dir, metrics = candidate_dir, candidate_metrics
            break

    windows = build_windows(sessions) if sessions else None
    window_counts: dict[str, dict[str, int]] = {}
    if windows is not None:
        for sid in by_id:
            mask = windows.session_id == sid
            window_counts[sid] = {
                "labelled": int((mask & (windows.label >= 0)).sum()),
                "chewing": int((mask & (windows.label == 1)).sum()),
            }

    recordings = []
    for session in sessions:
        record = records.get(session.session_id)
        split = (splits.get(session.session_id) or {}).get("split")
        recordings.append({
            "session_id": session.session_id,
            "recorded_at": session.created_at,
            "source": "loop" if record else session.source_kind,
            "activity": session.activity,
            "duration_s": round(session.duration_s, 1),
            "video": _has_video(session),
            "role": (record or {}).get("role"),
            "sync": _sync(session, record, loop_root),
            "labels": _labels(session),
            "windows": window_counts.get(session.session_id, {}),
            "split": split or "unassigned (assigned at the next run)",
            "usable": True,
            "proposals": _proposal_state(session.session_id, workspace),
            "attention": (record or {}).get("attention"),
        })
    for item in rejected:
        record = records.get(item["session_id"])
        recordings.append({
            "session_id": item["session_id"],
            "recorded_at": None,
            "source": "loop" if record else "workspace",
            "usable": False,
            "reason": item["reason"],
            "role": (record or {}).get("role"),
            "split": (splits.get(item["session_id"]) or {}).get("split"),
            "proposals": _proposal_state(item["session_id"], workspace),
            "attention": (record or {}).get("attention"),
        })

    tiers: dict[str, float] = {}
    for session in sessions:
        for item in session.intervals:
            tiers[item.tier] = tiers.get(item.tier, 0.0) + item.end - item.start
    by_split: dict[str, dict[str, Any]] = {}
    for row in recordings:
        if not row["usable"]:
            continue
        bucket = by_split.setdefault(row["split"], {"recordings": 0, "with_chewing": 0, "labelled_windows": 0, "chewing_windows": 0})
        bucket["recordings"] += 1
        bucket["with_chewing"] += int(row["labels"]["chewing_s"] > 0)
        bucket["labelled_windows"] += row["windows"].get("labelled", 0)
        bucket["chewing_windows"] += row["windows"].get("chewing", 0)
    data = {
        "usable_recordings": len(sessions),
        "rejected_recordings": len(rejected),
        "participants": 1,
        "labelled_minutes": round(sum(tiers.values()) / 60, 1),
        "chewing_minutes": round(sum(i.end - i.start for s in sessions for i in s.intervals if i.label == CHEWING) / 60, 1),
        "verified_clock_chewing_minutes": round(sum(
            i.end - i.start for s in sessions if s.clock == "shared_host_clock"
            for i in s.intervals if i.label == CHEWING) / 60, 1),
        "labelled_minutes_by_tier": {k: round(v / 60, 2) for k, v in sorted(tiers.items())},
        "labelled_windows": int((windows.label >= 0).sum()) if windows is not None else 0,
        "chewing_windows": int((windows.label == 1).sum()) if windows is not None else 0,
        "by_split": by_split,
        # Incomplete or diagnostic takes stay reviewable for diagnosis, but
        # their spans never reach training or evaluation, so they are not queued.
        "awaiting_assisted_review": sorted(
            row["session_id"] for row in recordings
            if row["usable"] and row.get("proposals") and not (row.get("labels") or {}).get("latest_assisted_review")
        ),
    }

    model: dict[str, Any] = {"trained": bool(metrics), "live": _live_models(repo, workspace)}
    if metrics:
        selected = metrics.get("selected_model")
        result = (metrics.get("results") or {}).get(selected) or {}
        config = metrics.get("config") or {}
        current_fingerprint = dataset_fingerprint(sessions, {k: config[k] for k in FINGERPRINT_KEYS if k in config})
        overview = metrics.get("sessions_overview") or []
        trained_ids = {row["session_id"] for row in overview}
        fingerprint_config = {k: config[k] for k in FINGERPRINT_KEYS if k in config}
        run_prints = {row["session_id"]: row.get("fingerprint") for row in overview}
        # Development results stay current when only locked-test recordings
        # changed. Runs before per-recording fingerprints cannot tell.
        changed_dev: list[str] | None = None
        if overview and all(run_prints.values()):
            changed_dev = sorted(
                s.session_id for s in sessions
                if (splits.get(s.session_id) or {}).get("split") != "test"
                and run_prints.get(s.session_id) != dataset_fingerprint([s], fingerprint_config))
            changed_dev += sorted(r["session_id"] for r in overview if r.get("split") == "dev" and r["session_id"] not in by_id)

        def split_scores(name: str) -> dict[str, Any] | None:
            block = result.get(name)
            if not block:
                return None
            bouts = block.get("bouts") or {}
            return {
                **{k: _round(block.get(k)) for k in (
                    "f1", "precision", "recall", "false_positive_rate", "auc_pr", "auc_roc")},
                "f1_ci95": [_round((block.get("ci95") or {}).get("f1", {}).get(k)) for k in ("low", "high")],
                "recordings": block.get("sessions"),
                "chewing_windows": block.get("positives"),
                "non_chewing_windows": block.get("negatives"),
                "bouts_detected": bouts.get("detected_bouts"),
                "bouts_reference": bouts.get("reference_bouts"),
                "false_bouts": bouts.get("false_bouts"),
                "false_bouts_per_hour": _round(bouts.get("false_bouts_per_negative_hour"), 1),
                "onset_latency_median_s": _round(bouts.get("onset_latency_median_s"), 2),
            }

        comparison = []
        for name, entry in (metrics.get("results") or {}).items():
            comparison.append({
                "model": name,
                "selected": name == selected,
                "dev_f1": _round(((entry.get("dev_oof") or {}).get("f1"))),
                "dev_auc_pr": _round(((entry.get("dev_oof") or {}).get("auc_pr"))),
                "test_f1": _round(((entry.get("test") or {}).get("f1"))),
                "latency_ms": _round(entry.get("inference_latency_ms"), 2),
            })
        per_recording = []
        for split_name in ("dev_oof", "test"):
            block = result.get(split_name) or {}
            for sid, values in (block.get("by_session") or {}).items():
                bouts = (block.get("bouts_by_session") or {}).get(sid) or {}
                per_recording.append({
                    "session_id": sid,
                    "split": "dev" if split_name == "dev_oof" else "test",
                    "recall": _round(values.get("recall")),
                    "false_positive_rate": _round(values.get("false_positive_rate")),
                    "chewing_windows": values.get("positives"),
                    "non_chewing_windows": values.get("negatives"),
                    "false_bouts": bouts.get("false_bouts"),
                    "bouts_detected": bouts.get("detected_bouts"),
                    "bouts_reference": bouts.get("reference_bouts"),
                })
        per_recording.sort(key=lambda r: -((r["false_positive_rate"] or 0) + (1 - (r["recall"] if r["recall"] is not None else 1))))
        contexts = []
        for split_name in ("dev_oof", "test"):
            for context, values in ((result.get(split_name) or {}).get("by_context") or {}).items():
                contexts.append({
                    "context": context, "split": "dev" if split_name == "dev_oof" else "test",
                    "false_positive_rate": _round(values.get("false_positive_rate")),
                    "recall": _round(values.get("recall")),
                    "negatives": values.get("negatives"), "positives": values.get("positives"),
                })
        frame = pd.read_csv(run_dir / "predictions.csv.gz") if (run_dir / "predictions.csv.gz").exists() else None
        model.update({
            "run_id": metrics.get("run_id"),
            "finished_at": metrics.get("finished_at"),
            "git_commit": (metrics.get("git") or {}).get("commit"),
            "selected_model": selected,
            "selection_rule": metrics.get("selection_rule"),
            "threshold": result.get("threshold"),
            "dataset_fingerprint": metrics.get("dataset_fingerprint"),
            "current_dataset_fingerprint": current_fingerprint,
            "trained_on_current_data": current_fingerprint == metrics.get("dataset_fingerprint"),
            "development_changed_recordings": changed_dev,
            "development_current": (current_fingerprint == metrics.get("dataset_fingerprint")
                                    if changed_dev is None else not changed_dev),
            "recordings_not_in_run": sorted(set(by_id) - trained_ids),
            "development": split_scores("dev_oof"),
            "test": split_scores("test"),
            "comparison": comparison,
            "test_consultations": index.get("test_consultations"),
            "report_path": str(run_dir / "report.html"),
            "errors_by_recording": per_recording,
            "errors_by_context": sorted(contexts, key=lambda c: -(c["false_positive_rate"] or 0)),
            "review_queue": (metrics.get("review_queue") or [])[:15],
            "timelines": _timelines(frame, f"p_{selected}", float(result.get("threshold") or 0.5), by_id)
            if frame is not None and f"p_{selected}" in frame else {},
        })
    deploy_dir = eating_root / "deploy"
    spec_id = (model["live"].get("exported_detector") or {}).get("model_id")
    parity = _read(deploy_dir / spec_id / "parity-latest.json") if spec_id else None
    model["parity"] = {k: parity.get(k) for k in (
        "status", "generated_at", "recordings", "recordings_passed", "scored_windows",
        "feature_values_bit_identical", "max_probability_abs_diff", "state_disagreements", "swift_runtime", "inputs",
    )} if parity else None
    assist = _read(workspace / "experiments" / "assist" / "latest.json")
    evaluation = {k: assist.get(k) for k in (
        "evaluation_id", "generated_at", "recordings", "windows_scored", "coverage", "auto_chewing_precision",
        "auto_not_chewing_precision", "chewing_recovered_automatically", "false_positive_windows",
        "false_negative_windows", "review_windows", "video_unavailable_windows", "effort_model",
    )} if assist else None

    behavior_latest = _read(workspace / "experiments" / "behavior" / "latest.json")
    if behavior_latest:
        timing = behavior_latest.get("bout_timing") or {}
        cadence = behavior_latest.get("cadence") or {}
        cycles = behavior_latest.get("video_cycle_confidence") or {}
        behavior = {
            "evaluation_id": behavior_latest.get("evaluation_id"),
            "registry": behavior_latest.get("registry"),
            "bout_timing": {k: timing.get(k) for k in (
                "split", "reference_bouts", "matched_bouts", "median_onset_error_s", "median_abs_offset_error_s",
                "median_abs_duration_error_s", "reference_pauses", "pauses_detected", "median_abs_pause_error_s")},
            "cadence": {k: cadence.get(k) for k in (
                "windows", "median_abs_difference_hz", "within_0_2_hz", "video_hz_range", "confidence_by_peak_share",
                "chew_count", "caveat")},
            "video_cycle_confidence": {
                "cycles": cycles.get("cycles"),
                "loro_auc": (cycles.get("leave_one_recording_out") or {}).get("auc_roc"),
                "fixed_constants_auc": (cycles.get("fixed_constants") or {}).get("auc_roc"),
                "table": cycles.get("table_all_data"),
                "meaning": cycles.get("meaning"),
            },
        }
    else:
        from ..behavior.measures import registry

        behavior = {"evaluation_id": None, "registry": registry()}

    history = [
        {
            "run_id": run.get("run_id"),
            "finished_at": run.get("finished_at"),
            "test_evaluated": run.get("test_evaluated", True),
            "selected_model": run.get("selected_model"),
            "dataset_fingerprint": (run.get("dataset_fingerprint") or "")[:12],
            "recordings": (run.get("counts") or {}).get("sessions"),
            "note": run.get("note"),
            "dev_f1": _round((((run.get("models") or {}).get(run.get("selected_model")) or {}).get("dev_oof") or {}).get("f1")),
            "test_f1": _round((((run.get("models") or {}).get(run.get("selected_model")) or {}).get("test") or {}).get("f1")),
        }
        for run in index.get("runs") or []
    ]
    next_steps = []
    if model.get("trained") and not model.get("development_current"):
        next_steps.append({
            "title": "Re-run the development check",
            "reason": f"development labels or recordings changed since {model.get('run_id')} "
                      f"({len(model.get('recordings_not_in_run') or [])} recordings not in that run)",
            "command": "chewsense-motion eating-experiment --dev-only",
        })
    if data["awaiting_assisted_review"]:
        next_steps.append({
            "title": "Review proposed chewing spans",
            "reason": ", ".join(data["awaiting_assisted_review"]),
            "command": "Review → Chewing spans",
        })
    for item in (metrics or {}).get("recommendations") or []:
        next_steps.append({k: item.get(k) for k in ("title", "reason", "experiment_id")})
    return {
        "artifact_type": "eating_pipeline_status",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(workspace),
        "recordings": sorted(recordings, key=lambda r: (not r["usable"], r.get("recorded_at") or "", r["session_id"])),
        "data": data,
        "model": model,
        "assisted_labeling": evaluation,
        "behavior": behavior,
        "history": history,
        "next": next_steps,
        "commands": {
            "refresh": "chewsense-motion pipeline-status",
            "development_check": "chewsense-motion eating-experiment --dev-only",
            "locked_test_run": "chewsense-motion eating-experiment   (consults the locked test; run only to confirm)",
            "export": "chewsense-motion eating-export --install-live",
            "parity": "chewsense-motion eating-parity",
            "proposals": "chewsense-motion assist-propose --loop",
            "assist_evaluation": "chewsense-motion assist-eval",
            "behavior_evaluation": "chewsense-motion behavior-eval",
        },
        "limitations": [
            "One wearer: no cross-person result exists.",
            "Locked test: few recordings contain chewing; intervals are wide.",
            "Most chewing labels have unverified clocks (2025 legacy).",
            "Wearer shadow-mode accuracy and battery use remain unverified; physical fixture execution alone cannot validate them.",
        ],
    }


def write_status(status: Mapping[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=1, default=float) + "\n")
    tmp.rename(path)
    return path


def summarize(status: Mapping[str, Any]) -> list[str]:
    data, model = status["data"], status["model"]
    lines = [
        f"Recordings: {data['usable_recordings']} usable, {data['rejected_recordings']} rejected; "
        f"{data['labelled_minutes']} labelled minutes ({data['chewing_minutes']} chewing, "
        f"{data['verified_clock_chewing_minutes']} with a shared clock)",
        "Splits: " + ", ".join(f"{k} {v['recordings']} ({v['with_chewing']} with chewing)" for k, v in data["by_split"].items()),
    ]
    if model.get("trained"):
        test = model.get("test") or {}
        dev = model.get("development") or {}
        lines.append(
            f"Model: {model['selected_model']} from {model['run_id']} · dev F1 {dev.get('f1')} · test F1 {test.get('f1')} "
            f"(95% CI {test.get('f1_ci95')}) · test consulted {model.get('test_consultations')} times"
        )
        lines.append("Trained on current data: " + ("yes" if model.get("trained_on_current_data") else
                     f"no ({len(model.get('recordings_not_in_run') or [])} recordings not in the run)")
                     + ("" if model.get("trained_on_current_data") or not model.get("development_current")
                        else "; only locked-test recordings changed, development results still current"))
    live = (model.get("live") or {}).get("exported_detector") or {}
    if live:
        parity = model.get("parity") or {}
        lines.append(f"Live: exported detector {live.get('model_id')} ({live.get('status')}), parity {parity.get('status', 'not run')}")
    for item in status.get("next") or []:
        lines.append(f"Next: {item.get('title')} — {item.get('reason')}")
    return lines
