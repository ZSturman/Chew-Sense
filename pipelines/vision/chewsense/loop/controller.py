"""Evaluation, dataset eligibility, weakness analysis and collection planning.

All metrics come from the existing deterministic ``evaluate_experiment`` on
frozen blind reviews. Instructions, predictions, assisted edits and unknown
spans are kept separate throughout:

* ``intended``  — what the wearer was instructed to do (guided sidecar)
* ``predicted`` — immutable Vision candidates
* ``verified``  — frozen human reviews (blind = independent evaluation truth)
* ``unknown``   — excluded spans, coarse unknown, invalid quality intervals
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..canonical import (
    CanonicalPointer,
    ReviewRevision,
    load_candidate_artifact,
    materialize_annotations,
    utc_now,
)
from ..eval.phase0 import evaluate_experiment, event_metrics
from ..session_bundle import load_coarse_intervals
from .experiments import (
    Activity,
    ExperimentDefinition,
    ExperimentRegistry,
    build_catalog,
    catalog_fingerprint,
)
from .guided import write_json_atomic
from .lmstudio import LMStudio
from .requirements import build_requirements, timing_check_definition
from .store import LoopPaths, read_json, write_once
from .eating_detector import eating_detector_summary
from .training import readiness

NEGATIVE_KINDS = ("rest", "talk", "drink", "walk", "head_movement", "stop_chewing")
KIND_TO_TAG = {"talk": "talking", "walk": "walking", "head_movement": "head_movement",
               "drink": "drinking", "rest": "rest", "chew": "chewing"}


def _pointer(session: Path) -> CanonicalPointer | None:
    path = session / "annotations/canonical_events.json"
    return CanonicalPointer.model_validate_json(path.read_text()) if path.exists() else None


class LoopController:
    def __init__(self, paths: LoopPaths, config: dict[str, Any], *,
                 lm: LMStudio | None = None,
                 trainer: Callable[[Path], Any] | None = None):
        self.paths = paths
        self.config = config
        self.registry = ExperimentRegistry(paths.experiments / "definitions")
        self.lm = lm
        self.trainer = trainer

    # ------------------------------------------------------------------ data
    def records(self) -> list[dict[str, Any]]:
        return [r for r in (read_json(p, {}) for p in sorted(self.paths.session_state.glob("*.json")))
                if r and r.get("session_id")]

    def _session(self, workspace: Path, session_id: str) -> Path:
        return workspace / "sessions/raw" / session_id

    def _review(self, session: Path, pointer: CanonicalPointer) -> ReviewRevision:
        return ReviewRevision.model_validate_json((session / pointer.review_path).read_text())

    def dataset_summary(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        training, evaluation = [], []
        coverage: dict[str, dict[str, int]] = {}
        sync_flags = []
        for record in records:
            sid, role = record["session_id"], record.get("role")
            tags = list((record.get("tags") or {}).get("activity", []))
            if record.get("food_category"):
                tags.append(f"food:{record['food_category']}")
            for tag in tags or ["untagged"]:
                coverage.setdefault(tag, {"collected": 0, "verified_training": 0,
                                          "verified_evaluation": 0})["collected"] += 1
            if record.get("legacy") or role == "diagnostic":
                continue
            if record.get("guided") and (not record.get("device_validation_complete")
                                         or not record.get("definition_verified")):
                continue
            if role == "training":
                session = self._session(self.paths.assisted, sid)
                pointer = _pointer(session) if session.exists() else None
                if pointer is None:
                    continue
                review = self._review(session, pointer)
                coarse = load_coarse_intervals(session) or []
                positive = any(i.label == "chewing" for i in coarse)
                negative = bool(coarse) and all(i.label == "not_chewing" for i in coarse)
                from pipeline.temporal_dataset import HARD_NEGATIVE_ACTIVITIES, session_activity_context

                activity = session_activity_context(session, read_json(session / "session.json", {}))
                sync = bool(review.review_context and review.review_context.sync_verified)
                sync_flags.append(sync)
                training.append({"session_id": sid, "positive": positive, "negative": negative,
                                 "hard_negative": negative and activity in HARD_NEGATIVE_ACTIVITIES,
                                 "sync_verified": sync, "path": str(session)})
                for tag in tags or ["untagged"]:
                    coverage[tag]["verified_training"] += 1
            elif role == "evaluation":
                session = self._session(self.paths.blind, sid)
                if session.exists() and _pointer(session) is not None:
                    evaluation.append(sid)
                    for tag in tags or ["untagged"]:
                        coverage[tag]["verified_evaluation"] += 1
        parents = {r["session_id"]: r.get("repeat_of_session_id") for r in records}
        roles = {r["session_id"]: r.get("role") for r in records}
        overlap = sum(1 for sid, parent in parents.items()
                      if parent and roles.get(parent) and roles.get(parent) != roles.get(sid))
        overlap += len({t["session_id"] for t in training} & set(evaluation))
        wearers = {r.get("wearer_id", "local-user") for r in records}
        return {
            "training": {
                "verified_sessions": len(training),
                "verified_positive_sessions": sum(t["positive"] for t in training),
                "verified_negative_sessions": sum(t["negative"] for t in training),
                "verified_hard_negative_sessions": sum(t["hard_negative"] for t in training),
                "sync_verified_fraction": (sum(sync_flags) / len(sync_flags)) if sync_flags else None,
                "eligible_sessions": [t["path"] for t in training if t["sync_verified"]],
            },
            "evaluation_sessions": evaluation,
            "role_overlap": overlap,
            "coverage": coverage,
            "wearers": len(wearers),
            "collected_sessions": len(records),
            "roles": {role: sum(1 for r in records if r.get("role") == role)
                      for role in ("training", "evaluation", "diagnostic")},
        }

    # ------------------------------------------------------------ evaluation
    def _evaluate_session(self, record: dict[str, Any]) -> dict[str, Any] | None:
        sid = record["session_id"]
        blind = self._session(self.paths.blind, sid)
        assisted = self._session(self.paths.assisted, sid)
        gold_pointer = _pointer(blind) if blind.exists() else None
        if gold_pointer is None:
            return None
        derived = self.paths.assisted / "derived/vision" / sid
        assisted_pointer = _pointer(assisted) if assisted.exists() else None
        if assisted_pointer is not None:
            prediction_path = derived / f"candidates-v{assisted_pointer.candidate_revision:04d}.json"
        else:
            revisions = sorted(derived.glob("candidates-v[0-9][0-9][0-9][0-9].json"))
            if not revisions:
                return None
            prediction_path = revisions[0]
        gold_review = blind / gold_pointer.review_path
        gold_candidates = (self.paths.blind / "derived/vision" / sid
                           / f"candidates-v{gold_pointer.candidate_revision:04d}.json")
        assisted_review = (assisted / assisted_pointer.review_path) if assisted_pointer else None
        results = {}
        for tolerance in self.config["evaluation"]["tolerances_s"]:
            key = hashlib.sha256("|".join([
                load_candidate_artifact(prediction_path).content_sha256,
                gold_pointer.selected_review_sha256, f"{tolerance:.3f}",
                assisted_pointer.selected_review_sha256 if assisted_pointer else "-",
            ]).encode()).hexdigest()[:16]
            out = self.paths.reports / "evaluation" / sid / f"eval-{tolerance:.3f}-{key}.json"
            if out.exists():
                results[tolerance] = json.loads(out.read_text())
                continue
            try:
                result = evaluate_experiment(prediction_path, gold_review, gold_candidates,
                                             tolerance_s=tolerance,
                                             assisted_review_path=assisted_review)
            except ValueError as exc:
                return {"session_id": sid, "error": str(exc)}
            write_once(out, result)
            results[tolerance] = result
        primary = results[self.config["evaluation"]["primary_tolerance_s"]]
        return {"session_id": sid, "results": results, "primary": primary,
                "breakdown": self._breakdown(record, prediction_path, gold_candidates, gold_review, primary),
                "human_agreement": self._agreement(gold_candidates, gold_review, derived, assisted_pointer, assisted)}

    def _breakdown(self, record, prediction_path, gold_candidates_path, gold_review_path, primary):
        intended = read_json(self.paths.session_reports(record["session_id"]) / "intended-activity.json")
        if not intended:
            return {}
        predictions = {c.id: c.end_closed_ts for c in load_candidate_artifact(prediction_path).chews}
        gold = {c.id: c.end_closed_ts for c in materialize_annotations(
            load_candidate_artifact(gold_candidates_path),
            ReviewRevision.model_validate_json(gold_review_path.read_text())).chew_cycles}
        matched_gold = {m["gold_id"] for m in primary["matches"]}
        extra, missed = set(primary["extra_prediction_ids"]), set(primary["missed_gold_ids"])
        out: dict[str, dict[str, float]] = {}
        for interval in intended["intervals"]:
            if interval["sync_marker"]:
                continue
            a, b = interval["start_ts"], interval["end_ts"]
            row = out.setdefault(interval["kind"], {"tp": 0, "fp": 0, "fn": 0, "seconds": 0.0})
            row["seconds"] += max(0.0, b - a)
            row["tp"] += sum(1 for g, t in gold.items() if a <= t < b and g in matched_gold)
            row["fn"] += sum(1 for g, t in gold.items() if a <= t < b and g in missed)
            row["fp"] += sum(1 for p, t in predictions.items() if a <= t < b and p in extra)
        return out

    def _agreement(self, gold_candidates_path, gold_review_path, derived, assisted_pointer, assisted):
        if assisted_pointer is None:
            return None
        gold = materialize_annotations(load_candidate_artifact(gold_candidates_path),
                                       ReviewRevision.model_validate_json(gold_review_path.read_text()))
        other = materialize_annotations(
            load_candidate_artifact(derived / f"candidates-v{assisted_pointer.candidate_revision:04d}.json"),
            self._review(assisted, assisted_pointer))
        metrics = event_metrics([(c.id, c.end_closed_ts) for c in other.chew_cycles],
                                [(c.id, c.end_closed_ts) for c in gold.chew_cycles], 0.1)
        return {"f1_at_100ms": metrics["f1"], "blind_events": metrics["gold_count"],
                "assisted_events": metrics["predicted_count"]}

    def evaluation_summary(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        sessions, errors = [], []
        for record in records:
            if record.get("role") != "evaluation" or record.get("legacy"):
                continue
            if record.get("guided") and (not record.get("device_validation_complete")
                                         or not record.get("definition_verified")):
                continue
            result = self._evaluate_session(record)
            if result is None:
                continue
            (errors if "error" in result else sessions).append(result)
        summary: dict[str, Any] = {"evaluation_sessions": len(sessions), "errors": errors,
                                   "independent": True, "tolerances": {}}
        if not sessions:
            return summary
        for tolerance in self.config["evaluation"]["tolerances_s"]:
            rows = [s["results"][tolerance] for s in sessions]
            tp, fp, fn = (sum(r[k] for r in rows) for k in ("tp", "fp", "fn"))
            precision = tp / (tp + fp) if tp + fp else None
            recall = tp / (tp + fn) if tp + fn else None
            f1 = (2 * precision * recall / (precision + recall)
                  if precision and recall else (0.0 if precision is not None and recall is not None else None))
            weighted = [(r["timestamp_mae_s"], r["tp"]) for r in rows if r["timestamp_mae_s"] is not None]
            p95 = [r["timestamp_p95_s"] for r in rows if r["timestamp_p95_s"] is not None]
            summary["tolerances"][f"{tolerance:.3f}"] = {
                "tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1,
                "gold_events": tp + fn,
                "per_session_f1": {s["session_id"]: s["results"][tolerance]["f1"] for s in sessions},
                "per_session_relative_count_error": {
                    s["session_id"]: s["results"][tolerance]["relative_count_error"] for s in sessions},
                "timestamp_mae_s": (sum(m * n for m, n in weighted) / sum(n for _, n in weighted))
                if weighted and sum(n for _, n in weighted) else None,
                "worst_session_timestamp_p95_s": max(p95) if p95 else None,
            }
        by_kind: dict[str, dict[str, float]] = {}
        by_food: dict[str, dict[str, float]] = {}
        by_gaps = {"with_sensor_gaps": [0, 0, 0], "without_sensor_gaps": [0, 0, 0]}
        records_by_id = {r["session_id"]: r for r in records}
        for s in sessions:
            record = records_by_id[s["session_id"]]
            for kind, row in s["breakdown"].items():
                target = by_kind.setdefault(kind, {"tp": 0, "fp": 0, "fn": 0, "seconds": 0.0})
                for key in target:
                    target[key] += row[key]
            food = record.get("food_category") or "unspecified"
            primary = s["primary"]
            target = by_food.setdefault(food, {"tp": 0, "fp": 0, "fn": 0})
            for key in ("tp", "fp", "fn"):
                target[key] += primary[key]
            bucket = by_gaps["with_sensor_gaps" if record.get("motion_gaps") else "without_sensor_gaps"]
            for i, key in enumerate(("tp", "fp", "fn")):
                bucket[i] += primary[key]
        for row in by_kind.values():
            minutes = row["seconds"] / 60
            row["false_events_per_minute"] = row["fp"] / minutes if minutes else None
            row["recall"] = row["tp"] / (row["tp"] + row["fn"]) if row["tp"] + row["fn"] else None
        for row in by_food.values():
            row["recall"] = row["tp"] / (row["tp"] + row["fn"]) if row["tp"] + row["fn"] else None
            row["precision"] = row["tp"] / (row["tp"] + row["fp"]) if row["tp"] + row["fp"] else None
        summary.update(
            by_instructed_activity=by_kind,
            by_food_category=by_food,
            by_sensor_gaps={k: dict(zip(("tp", "fp", "fn"), v)) for k, v in by_gaps.items()},
            human_effort=self._effort(sessions),
            human_agreement=[s["human_agreement"] | {"session_id": s["session_id"]}
                             for s in sessions if s["human_agreement"]],
            note=("Vision proposals vs frozen independent blind reviews on observable spans, "
                  "conditional on the coarse gate. Breakdown by instructed activity uses the "
                  "guided schedule's actual start/end times as context, not as labels."),
        )
        return summary

    @staticmethod
    def _effort(sessions):
        pairs = [(s["primary"].get("gold_active_seconds"), s["primary"].get("human_correction_seconds"))
                 for s in sessions]
        pairs = [(b, a) for b, a in pairs if b and a]
        if not pairs:
            return {"measured_sessions": 0, "fraction_saved": None}
        blind, assisted = sum(b for b, _ in pairs), sum(a for _, a in pairs)
        return {"measured_sessions": len(pairs), "independent_seconds": blind,
                "assisted_seconds": assisted, "fraction_saved": 1 - assisted / blind,
                "note": "Same-wearer timing is biased by familiarity unless counterbalanced."}

    # ------------------------------------------------------------ acceptance
    def acceptance(self, evaluation: dict[str, Any], sync: dict[str, Any]) -> dict[str, Any]:
        targets = self.config["acceptance"]["research"]
        primary = evaluation["tolerances"].get(
            f"{self.config['evaluation']['primary_tolerance_s']:.3f}")
        enough = (evaluation["evaluation_sessions"] >= self.config["evaluation"]["min_evaluation_sessions"]
                  and primary is not None
                  and primary["gold_events"] >= self.config["evaluation"]["min_gold_events"])
        checks = []

        def check(cid, value, target, lower_is_better=False):
            if value is None or not enough:
                state = "insufficient_evidence"
            else:
                state = "met" if (value <= target if lower_is_better else value >= target) else "not_met"
            checks.append({"id": cid, "value": value, "target": target, "state": state})

        check("chew_event_f1_at_250ms", primary and primary["f1"], targets["chew_event_f1_at_250ms"])
        worst_count = None
        if primary:
            values = [v for v in primary["per_session_relative_count_error"].values() if v is not None]
            worst_count = max(values) if values else None
        check("per_session_relative_count_error", worst_count,
              targets["per_session_relative_count_error"], True)
        check("closure_timestamp_mae_s", primary and primary["timestamp_mae_s"],
              targets["closure_timestamp_mae_s"], True)
        check("closure_timestamp_p95_s", primary and primary["worst_session_timestamp_p95_s"],
              targets["closure_timestamp_p95_s"], True)
        check("sync_median_residual_s", sync.get("worst_median_s"), targets["sync_median_residual_s"], True)
        check("labeling_time_saved_fraction", evaluation.get("human_effort", {}).get("fraction_saved"),
              targets["labeling_time_saved_fraction"])
        met = all(c["state"] == "met" for c in checks)
        return {"stage": "research", "sufficient_evidence": enough, "all_met": met, "checks": checks,
                "production_requirements": "not defined; requires multi-wearer physical validation"}

    def sync_summary(self, records):
        assessed = [read_json(self.paths.session_reports(r["session_id"]) / "sync-assessment.json")
                    for r in records]
        assessed = [a for a in assessed if a]
        medians = [a["median_absolute_residual_s"] for a in assessed if a.get("status") != "inconclusive"]
        confirmed = 0
        for record in records:
            for workspace in (self.paths.assisted, self.paths.blind):
                session = self._session(workspace, record["session_id"])
                pointer = _pointer(session)
                review = (read_json(session / pointer.review_path, {}) if pointer else
                          read_json(session / "annotations/review-draft.json", {}))
                if (review.get("review_context") or {}).get("sync_verified"):
                    confirmed += 1
                    break
        return {
            "assessed_sessions": len(assessed),
            "consistent": sum(a["status"] == "consistent" for a in assessed),
            "exceeds_budget": sum(a["status"] == "exceeds_budget" for a in assessed),
            "inconclusive": sum(a["status"] == "inconclusive" for a in assessed),
            "worst_median_s": max(medians) if medians else None,
            "automatic_only": True,
            "human_confirmed": confirmed,
        }

    # ------------------------------------------------------------ weaknesses
    def weaknesses(self, records, dataset, evaluation, sync, training_state, motion_history=()) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []

        def add(wid, category, title, evidence, action, priority, tags=None, experiment_id=None):
            found.append({"id": wid, "category": category, "title": title, "evidence": evidence,
                          "action": action, "priority": priority, "tags": tags or [],
                          "experiment_id": experiment_id})

        min_eval = self.config["evaluation"]["min_evaluation_sessions"]
        if evaluation["evaluation_sessions"] < min_eval:
            add("evaluation-data", "insufficient_evaluation_data",
                "Not enough independently reviewed evaluation recordings",
                {"frozen_blind_sessions": evaluation["evaluation_sessions"], "required": min_eval},
                "collect", "high", experiment_id="baseline-90s")
        if sync["exceeds_budget"] >= 1:
            add("sync-budget", "synchronization_defect",
                "Automatic marker check exceeds the synchronization budget",
                {"sessions_exceeding": sync["exceeds_budget"], "worst_median_s": sync["worst_median_s"]},
                "fix_capture", "high")
        timing_received = any(r.get("experiment_id") == "timing-check" and r.get("device_validation_complete") for r in records)
        if timing_received and not sync["consistent"]:
            add("sync-review", "insufficient_synchronization_evidence",
                "Inspect the completed timing check before requesting another",
                {"inconclusive": sync["inconclusive"], "outside_budget": sync["exceeds_budget"]},
                "investigate", "high", experiment_id="timing-check")
        elif sync["inconclusive"] and not sync["consistent"]:
            add("sync-evidence", "insufficient_synchronization_evidence",
                "Timing needs three distinct matched nods",
                {"inconclusive_recordings": sync["inconclusive"]},
                "collect_sync_check", "high", experiment_id="timing-check")
        by_experiment: dict[str, list[dict]] = {}
        for record in records:
            if record.get("experiment_id"):
                by_experiment.setdefault(record["experiment_id"], []).append(record)
        for experiment_id, items in sorted(by_experiment.items()):
            failed = [r for r in items if not r.get("device_validation_complete")]
            if len(items) >= 2 and len(failed) / len(items) >= 0.5:
                checks = sorted({c for r in failed for c in r.get("failed_checks", [])})
                add(f"quality-{experiment_id}", "recording_quality",
                    f"Recordings of {experiment_id} often fail quality checks",
                    {"recordings": len(items), "failed": len(failed), "failed_checks": checks},
                    "improve_setup", "medium", experiment_id=experiment_id)
            mismatched = [r for r in items if r.get("definition_verified") is False]
            if mismatched:
                add(f"definition-{experiment_id}", "recording_quality",
                    f"{len(mismatched)} recordings carry an unregistered definition of {experiment_id}",
                    {"sessions": [r["session_id"] for r in mismatched]}, "investigate", "high",
                    experiment_id=experiment_id)
        overall = evaluation.get("by_instructed_activity", {})
        negatives = [r for k, r in overall.items() if k in NEGATIVE_KINDS and r["seconds"]]
        base_rate = (sum(r["fp"] for r in negatives) / (sum(r["seconds"] for r in negatives) / 60)
                     if negatives else None)
        for kind, row in sorted(overall.items()):
            rate = row.get("false_events_per_minute")
            if kind in NEGATIVE_KINDS and rate and row["fp"] >= 5 and base_rate and rate >= 2 * base_rate:
                tag = KIND_TO_TAG.get(kind, kind)
                verified = dataset["coverage"].get(tag, {}).get("verified_training", 0)
                add(f"fp-{kind}", "model_limitation",
                    f"Vision proposes false chews during {kind.replace('_', ' ')}",
                    {"false_events": row["fp"], "false_events_per_minute": round(rate, 2),
                     "baseline_negative_rate": round(base_rate, 2), "verified_training_sessions": verified},
                    "review_and_collect_negatives", "medium", tags=[tag])
        for food, row in sorted(evaluation.get("by_food_category", {}).items()):
            if row.get("recall") is not None and row["tp"] + row["fn"] >= 20 and row["recall"] < 0.7:
                add(f"recall-{food}", "model_limitation",
                    f"Vision misses chews with {food} food",
                    {"recall": round(row["recall"], 2), "gold_events": row["tp"] + row["fn"]},
                    "collect", "medium", tags=[f"food:{food}"])
        for item in evaluation.get("human_agreement", []):
            if item["blind_events"] >= 10 and item["f1_at_100ms"] < 0.9:
                add(f"agreement-{item['session_id']}", "annotation_quality",
                    "Blind and assisted human reviews disagree",
                    item, "adjudicate", "medium")
        coverage = dataset["coverage"]
        for definition in self.registry.published():
            if "training" not in definition.evaluation_purpose:
                continue
            for tag in definition.tags.get("activity", []):
                row = coverage.get(tag, {})
                if row.get("verified_training", 0) < definition.target_recordings:
                    add(f"coverage-{tag}", "insufficient_training_data",
                        f"Few verified training recordings for {tag.replace('_', ' ')}",
                        {"verified_training": row.get("verified_training", 0),
                         "collected": row.get("collected", 0), "target": definition.target_recordings},
                        "collect", "low", tags=[tag])
        if training_state.get("plateaued"):
            add("training-plateau", "model_limitation",
                "Recent experimental models stopped improving",
                {"plateau_runs": self.config["training"]["plateau_runs"]}, "change_model", "medium")
        completed = [h for h in motion_history if h.get("state") == "completed" and h.get("test_metrics")]
        if completed:
            from pipeline.promotion import PromotionThresholds

            limits = PromotionThresholds()
            metrics = completed[-1]["test_metrics"]
            for activity, row in metrics.get("per_activity", {}).items():
                rate = row.get("hard_negative_fpr")
                if activity in {"talking", "walking", "drinking", "head_movement"} and rate is not None and rate > limits.hard_negative_fpr:
                    add(f"motion-fp-{activity}", "model_limitation",
                        f"AirPods model confuses {activity.replace('_', ' ')} with eating",
                        {"split": "test_locked", "hard_negative_fpr": rate,
                         "target": limits.hard_negative_fpr},
                        "review_and_collect_negatives", "high", tags=[activity])
            score = metrics.get("chew_f1_at_250ms")
            for food, row in metrics.get("per_food_category", {}).items():
                score_for_food = row.get("chew_f1_at_250ms")
                events = row.get("chew_true_positives", 0) + row.get("chew_false_negatives", 0)
                if score_for_food is not None and events >= self.config["planner"]["min_activity_gold_events"] and score_for_food < limits.chew_f1_at_250ms:
                    add(f"motion-food-{food}", "model_limitation",
                        f"AirPods model needs more verified {food} food examples",
                        {"split": "test_locked", "chew_f1_at_250ms": score_for_food, "gold_events": events},
                        "collect", "high", tags=[f"food:{food}"])
            if score is not None and score < limits.chew_f1_at_250ms:
                add("motion-chew-events", "model_limitation",
                    "AirPods model still misses or adds chewing events on the locked test recordings",
                    {"split": "test_locked", "chew_f1_at_250ms": score,
                     "target": limits.chew_f1_at_250ms},
                    "collect", "high", experiment_id="baseline-90s")
        # One weakness per id; keep the most specific evidence.
        unique = {w["id"]: w for w in found}
        return sorted(unique.values(), key=lambda w: ({"high": 0, "medium": 1, "low": 2}[w["priority"]], w["id"]))

    # --------------------------------------------------------------- planner
    def plan(self, weaknesses, progress: dict[str, int], acceptance) -> dict[str, Any]:
        policy = self.config["planner"]
        requests: dict[str, list[dict[str, Any]]] = {}
        created: list[str] = []
        published = {d.experiment_id: d for d in self.registry.published()}
        by_tag: dict[str, list[ExperimentDefinition]] = {}
        for definition in published.values():
            for tag in definition.tags.get("activity", []) + [
                    f"food:{c}" for c in definition.tags.get("food_category", [])]:
                by_tag.setdefault(tag, []).append(definition)
        generated_active = [d for d in published.values() if d.request.source == "planner"
                            and progress.get(d.experiment_id, 0) < d.target_recordings]
        stop_reason = None
        if acceptance.get("all_met"):
            stop_reason = "research acceptance targets met; only evaluation refresh is requested"
        for weakness in weaknesses:
            if weakness["action"] not in {"collect", "review_and_collect_negatives", "collect_sync_check"}:
                continue
            if stop_reason and weakness["category"] != "insufficient_evaluation_data":
                continue
            options = ([published[weakness["experiment_id"]]] if weakness.get("experiment_id") in published
                       else [d for t in weakness["tags"] for d in by_tag.get(t, [])])
            if not options and weakness["action"] == "collect_sync_check":
                definition = self._publishable(timing_check_definition())
                if definition is not None:
                    options = [definition]
                    created.append(definition.experiment_id)
            if not options and weakness["tags"]:
                if len(generated_active) >= policy["max_active_generated"]:
                    continue
                definition = self._generate(weakness)
                if definition is None:
                    continue
                created.append(definition.experiment_id)
                generated_active.append(definition)
                options = [definition]
            if not options:
                continue
            # Prefer an experiment focused on the failed activity over a mixed
            # baseline that happens to carry the same tag.
            options = sorted(options, key=lambda d: (len(d.tags.get("activity", [])),
                                                     d.total_seconds, d.experiment_id))
            target = options[0]
            remaining = target.target_recordings - progress.get(target.experiment_id, 0)
            if remaining <= 0:
                bumped = self._bump(target, weakness)
                if bumped is None:
                    continue
                created.append(f"{bumped.experiment_id}@v{bumped.version}")
                target = bumped
            requests.setdefault(target.experiment_id, []).append({
                "weakness_id": weakness["id"], "category": weakness["category"],
                "summary": weakness["title"], "priority": weakness["priority"],
                "evidence": weakness["evidence"]})
        return {"requests": requests, "created": created, "stop_reason": stop_reason}

    def _publishable(self, definition: ExperimentDefinition) -> ExperimentDefinition | None:
        if not self.config["planner"]["auto_publish_generated"]:
            self.registry.add(definition)
            return None
        self.registry.add(definition)
        self.registry.publish(definition.experiment_id, definition.version, by="planner")
        return definition

    def _bump(self, definition: ExperimentDefinition, weakness) -> ExperimentDefinition | None:
        versions = self.registry.versions(definition.experiment_id)
        if len(versions) >= self.config["planner"]["max_versions_per_experiment"]:
            return None
        payload = definition.model_dump()
        payload.update(
            version=self.registry.next_version(definition.experiment_id),
            target_recordings=min(50, definition.target_recordings + (1 if definition.experiment_id == "timing-check" else 3)),
            request={"source": "planner", "weakness_id": weakness["id"],
                     "summary": weakness["title"], "evidence": weakness["evidence"]},
        )
        return self._publishable(ExperimentDefinition.model_validate(payload))

    def _generate(self, weakness) -> ExperimentDefinition | None:
        """Template a small confound experiment for an activity with no experiment."""
        tag = weakness["tags"][0]
        kind = {v: k for k, v in KIND_TO_TAG.items()}.get(tag)
        if kind is None or kind == "chew" or tag.startswith("food:"):
            return None
        walking = kind == "walk"
        nod = dict(kind="sync_marker", duration_s=5, intended_label="not_chewing", sync_marker=True,
                   expected_behavior="One clear nod visible in video and AirPods motion",
                   on_quality_loss="repeat", min_valid_fraction=0.9)
        body = dict(kind=kind, duration_s=30, intended_label="not_chewing",
                    expected_behavior=f"{tag.replace('_', ' ').capitalize()} without food",
                    requires_face=not walking, on_quality_loss="mark" if walking else "pause")
        activities = [
            dict(nod, id="nod-start", instruction="Look at the camera and nod distinctly"),
            dict(kind="rest", id="still-1", instruction="Stay still without eating", duration_s=10,
                 intended_label="not_chewing", expected_behavior="Still", requires_face=not walking),
            dict(body, id="activity-1", instruction=f"{tag.replace('_', ' ').capitalize()} naturally"),
            dict(kind="rest", id="still-2", instruction="Pause and stay still", duration_s=10,
                 intended_label="not_chewing", expected_behavior="Still", requires_face=not walking),
            dict(body, id="activity-2", instruction=f"{tag.replace('_', ' ').capitalize()} again"),
            dict(nod, id="nod-end", instruction="Face the camera and perform a final nod"),
        ]
        for activity in activities:
            Activity.model_validate(activity)
        experiment_id = f"gen-{tag.replace('_', '-')}"
        payload = dict(
            experiment_id=experiment_id, version=self.registry.next_version(experiment_id),
            title=f"{tag.replace('_', ' ').capitalize()} Without Eating",
            summary=f"Collect {tag.replace('_', ' ')} without food as negative examples.",
            purpose=f"Requested because: {weakness['title']}.",
            request={"source": "planner", "weakness_id": weakness["id"], "summary": weakness["title"],
                     "evidence": weakness["evidence"]},
            priority=weakness["priority"], target_recordings=3,
            preparation=["Wear both AirPods and confirm they are connected.", "No food required.",
                         "Prop the iPhone so your face is visible."],
            equipment=["AirPods"], food={"kind": "none"},
            camera={"position": "Front camera facing you with your face visible.",
                    "jaw_required": not walking},
            safety_notes=(["Only walk where it is safe; stop the recording at any time."] if walking else []),
            activities=activities,
            labeling={"wearer_no_food_attestation": True},
            evaluation_purpose=["training", "confound_negative"],
            tags={"activity": [tag]},
        )
        try:
            definition = ExperimentDefinition.model_validate(payload)
        except ValueError:
            return None
        return self._publishable(definition)

    # --------------------------------------------------------------- catalog
    def progress(self, records) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in records:
            if record.get("experiment_id") and record.get("definition_verified") \
                    and record.get("device_validation_complete"):
                counts[record["experiment_id"]] = counts.get(record["experiment_id"], 0) + 1
        return counts

    def publish_catalog(self, progress: dict[str, int], requests: dict[str, list]) -> dict[str, Any]:
        previous = read_json(self.paths.catalog, None)
        catalog = build_catalog(self.registry.published(), progress,
                                catalog_version=(previous or {}).get("catalog_version", 0),
                                generated_at=utc_now())
        for entry in catalog["experiments"]:
            entry["requests"] = requests.get(entry["definition"]["experiment_id"], [])
            if entry["requests"]:
                levels = [r["priority"] for r in entry["requests"]] + [entry["definition"]["priority"]]
                entry["effective_priority"] = min(levels, key={"high": 0, "medium": 1, "low": 2}.get)
            else:
                entry["effective_priority"] = entry["definition"]["priority"]
        fingerprint = catalog_fingerprint(catalog)
        if previous and previous.get("fingerprint") == fingerprint:
            return previous
        catalog["catalog_version"] += 1
        catalog["fingerprint"] = fingerprint
        write_json_atomic(self.paths.catalog, catalog)
        return catalog

    # --------------------------------------------------------------- analyze
    def training_history(self) -> list[dict[str, Any]]:
        root = self.paths.root / "training"
        runs = [read_json(p) for p in sorted(root.glob("run-*/request.json"))]
        history = []
        for run in filter(None, runs):
            result = read_json(Path(run["run_dir"]) / "result.json")
            saved_state = read_json(Path(run["run_dir"]) / "state.json", {}).get("state", "requested")
            state = saved_state if saved_state in {"failed", "running"} else "completed" if result else saved_state
            f1 = None
            if result:
                f1 = (result.get("metrics") or {}).get("chew_f1_at_250ms")
            history.append(dict(run, state=state, validation_f1=f1,
                                test_metrics=(result or {}).get("test_metrics"),
                                promotion_assessment=(result or {}).get("promotion_assessment")))
        return history

    def maybe_request_training(self, gate: dict[str, Any], dataset: dict[str, Any]) -> dict | None:
        if not (gate["ready"] and gate["auto_experimental_training"]):
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_dir = self.paths.root / "training" / f"run-{stamp}"
        run_dir.mkdir(parents=True)
        request = {"run_dir": str(run_dir), "requested_at": utc_now(),
                   "experimental_workspace": str(self.paths.root / "training" / "workspace"),
                   "sessions": dataset["training"]["eligible_sessions"],
                   "training_sessions": dataset["training"]["verified_sessions"],
                   "evaluation_sessions_excluded": dataset["evaluation_sessions"],
                   "promotion": "never automatic"}
        write_once(run_dir / "request.json", request)
        if self.trainer is not None:
            write_json_atomic(run_dir / "state.json", {"state": "running"})
            try:
                result = self.trainer(run_dir / "request.json")
                if isinstance(result, Future):
                    result.add_done_callback(lambda future: self._finish_training(run_dir, future))
                else:
                    self._finish_training(run_dir, result)
            except Exception as exc:  # noqa: BLE001
                write_json_atomic(run_dir / "state.json", {"state": "failed", "error": str(exc)})
        return request

    @staticmethod
    def _finish_training(run_dir: Path, result) -> None:
        try:
            if isinstance(result, Future):
                result = result.result()
            if getattr(result, "returncode", 0) != 0:
                raise RuntimeError(f"Training exited with code {result.returncode}; inspect training.log.")
            if not (run_dir / "result.json").exists():
                raise RuntimeError("Training exited without a result.json; inspect training.log.")
            write_json_atomic(run_dir / "state.json", {"state": "finished"})
        except Exception as exc:  # noqa: BLE001
            write_json_atomic(run_dir / "state.json", {"state": "failed", "error": str(exc)})

    def analyze(self) -> dict[str, Any]:
        records = self.records()
        dataset = self.dataset_summary(records)
        evaluation = self.evaluation_summary(records)
        sync = self.sync_summary(records)
        if sync["consistent"] and sync.get("human_confirmed"):
            if any(d.experiment_id == "timing-check" for d in self.registry.published()):
                self.registry.retire("timing-check", by="planner",
                                     reason="Timing diagnostic completed and human-confirmed; retain its immutable definition and recording.")
        history = self.training_history()
        gate = readiness(dataset, evaluation, history, self.config)
        acceptance = self.acceptance(evaluation, sync)
        prior_models = [h for h in history if h.get("state") == "completed"]
        prior_passed = bool(prior_models and (prior_models[-1].get("promotion_assessment") or {}).get("passed"))
        training_request = (self.maybe_request_training(gate, dataset)
                            if not (acceptance["all_met"] and prior_passed) else None)
        if training_request:
            history = self.training_history()
            gate = readiness(dataset, evaluation, history, self.config)
        completed = [h for h in history if h.get("state") == "completed"]
        motion_passed = bool(completed and (completed[-1].get("promotion_assessment") or {}).get("passed"))
        loop_targets_met = acceptance["all_met"] and motion_passed
        weaknesses = self.weaknesses(records, dataset, evaluation, sync, gate, history)
        progress = self.progress(records)
        plan = self.plan(weaknesses, progress, {**acceptance, "all_met": loop_targets_met})
        progress = self.progress(records)
        catalog = self.publish_catalog(progress, plan["requests"])
        stopping = {
            "acceptance_met": loop_targets_met,
            "vision_targets_met": acceptance["all_met"],
            "motion_assessment_passed": motion_passed,
            "training_plateaued": gate["plateaued"],
            "evaluation_too_small": evaluation["evaluation_sessions"]
            < self.config["evaluation"]["min_evaluation_sessions"],
            "single_wearer": dataset["wearers"] <= 1,
            "planner_stop_reason": plan["stop_reason"],
            "notes": [n for n in [
                "Cross-person generalization cannot be assessed with one wearer."
                if dataset["wearers"] <= 1 else None,
                "No motion model is trained or promoted until every gate passes."
                if not gate["ready"] else None,
            ] if n],
        }
        evidence = {"weaknesses": [{k: w[k] for k in ("id", "category", "title", "evidence")}
                                   for w in weaknesses],
                    "acceptance": acceptance["checks"]}
        # The language-model summary is slow and optional: it is produced in the
        # background (see summarize_in_background) and shown only while it
        # still describes exactly this evidence.
        evidence_sha = hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()
        cached = read_json(self.paths.reports / "loop" / "lmstudio.json") or {}
        lm = cached if cached.get("evidence_sha256") == evidence_sha else None
        report = {
            "generated_at": utc_now(),
            "dataset": {k: v for k, v in dataset.items()},
            "evaluation": evaluation,
            "synchronization": sync,
            "acceptance": acceptance,
            "weaknesses": weaknesses,
            "plan": plan,
            "training": {"readiness": gate, "history": history, "requested": training_request},
            "stopping": stopping,
            "catalog_version": catalog["catalog_version"],
            "lmstudio": lm,
            "lmstudio_evidence": {"sha256": evidence_sha, "evidence": evidence},
        }
        report["eating_detector"] = eating_detector_summary(self.paths.root.parent / "experiments")
        report["requirements"] = build_requirements(
            records, dataset, evaluation, sync, gate, history,
            (read_json(self.paths.review_tasks, {}) or {}).get("tasks", []), self.config,
            eating=report["eating_detector"])
        write_json_atomic(self.paths.reports / "loop" / "latest.json", report)
        return report

    def summarize(self, report: dict[str, Any]) -> dict[str, Any] | None:
        """Ask LM Studio for advisory text; cache it with its evidence hash."""
        if self.lm is None or not self.config["lmstudio"]["enabled"]:
            return None
        evidence = report["lmstudio_evidence"]
        cached = read_json(self.paths.reports / "loop" / "lmstudio.json") or {}
        if cached.get("evidence_sha256") == evidence["sha256"] and cached.get("used"):
            return cached
        result = self.lm.summarize(evidence["evidence"])
        result.update(evidence_sha256=evidence["sha256"], generated_at=utc_now())
        write_json_atomic(self.paths.reports / "loop" / "lmstudio.json", result)
        return result
