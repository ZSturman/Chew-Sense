"""Training readiness gates and the isolated experimental training run.

Training is requested only when every gate passes. A run materializes a new
versioned dataset from finalized *training-role* sessions only, creates
locked chronological splits, trains, evaluates on the locked validation split
and registers the result in an experimental registry under
``loop/training/workspace``. It never deploys or promotes; promotion remains
the existing explicit ``assess_registry_entry`` decision.

Run as ``python -m chewsense.loop.training <request.json>`` so heavy ML
imports and failures stay outside the service process.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..canonical import utc_now
from .guided import write_json_atomic


def readiness(dataset: dict[str, Any], evaluation: dict[str, Any], history: list[dict[str, Any]],
              config: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Deterministic gates. Every gate must pass; reasons are always reported."""
    policy = config["training"]
    now = now or datetime.now(timezone.utc)
    training = dataset["training"]
    gates = []

    def gate(gate_id: str, passed: bool, detail: str):
        gates.append({"id": gate_id, "passed": bool(passed), "detail": detail})

    gate("verified_positive_sessions",
         training["verified_positive_sessions"] >= policy["min_verified_training_sessions"],
         f"{training['verified_positive_sessions']} verified sessions with chewing; "
         f"need {policy['min_verified_training_sessions']}")
    gate("verified_negative_sessions",
         training.get("verified_hard_negative_sessions", 0) >= policy["min_verified_negative_sessions"],
         f"{training.get('verified_hard_negative_sessions', 0)} verified hard-negative sessions "
         "(talking, walking, drinking or head movement); "
         f"need {policy['min_verified_negative_sessions']}")
    sync_fraction = training["sync_verified_fraction"]
    gate("synchronization",
         sync_fraction is not None and sync_fraction >= policy["min_sync_verified_fraction"],
         "fraction of training sessions with human-confirmed clocks: "
         + ("none" if sync_fraction is None else f"{sync_fraction:.2f}"))
    gate("independent_evaluation",
         evaluation.get("evaluation_sessions", 0) >= config["evaluation"]["min_evaluation_sessions"],
         f"{evaluation.get('evaluation_sessions', 0)} frozen blind evaluation sessions; "
         f"need {config['evaluation']['min_evaluation_sessions']}")
    gate("partitions_disjoint", dataset["role_overlap"] == 0,
         f"{dataset['role_overlap']} sessions appear in both training and evaluation")
    previous = [h for h in history if h.get("state") in {"completed", "running", "requested"}]
    active = [h for h in history if h.get("state") in {"running", "requested"}]
    gate("no_active_training", not active, f"{len(active)} experimental training run(s) already in progress")
    new_sessions = training["verified_sessions"] - (previous[-1]["training_sessions"] if previous else 0)
    gate("new_data", not previous or new_sessions >= policy["min_new_sessions_between_runs"],
         f"{new_sessions} new verified training sessions since the last run")
    recent = [h for h in history
              if h.get("state") in {"completed", "running", "requested", "failed", "finished"}
              and datetime.fromisoformat(h["requested_at"]) > now - timedelta(days=7)]
    gate("rate_limit", len(recent) < policy["max_runs_per_7_days"],
         f"{len(recent)} runs in the last 7 days; limit {policy['max_runs_per_7_days']}")
    completed = [h for h in history if h.get("state") == "completed" and h.get("validation_f1") is not None]
    plateau = False
    window = policy["plateau_runs"]
    if len(completed) >= window:
        f1 = [h["validation_f1"] for h in completed[-window:]]
        plateau = max(f1) - f1[0] < policy["plateau_min_f1_gain"]
    gate("not_plateaued", not plateau,
         "recent runs improved" if not plateau else
         f"last {window} runs improved F1 by less than {policy['plateau_min_f1_gain']}")
    return {
        "ready": all(g["passed"] for g in gates),
        "plateaued": plateau,
        "gates": gates,
        "auto_experimental_training": bool(policy["auto_experimental_training"]),
        "promotion": "never automatic; requires assess_registry_entry and explicit approval",
    }


def launch(request_path: Path, *, timeout_s: float = 6 * 3600,
           stop_event: threading.Event | None = None) -> subprocess.CompletedProcess:
    request = json.loads(Path(request_path).read_text())
    env = dict(os.environ, CHEWSENSE_WORKSPACE=request["experimental_workspace"],
               CHEWSENSE_LOOP_PARENT_PID=str(os.getpid()))
    log = Path(request["run_dir"]) / "training.log"
    with open(log, "a") as handle:
        command = [sys.executable, "-m", "chewsense.loop.training", str(request_path)]
        process = subprocess.Popen(command, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                return subprocess.CompletedProcess(command, process.wait(timeout=0.25))
            except subprocess.TimeoutExpired:
                interrupted = stop_event is not None and stop_event.is_set()
                if not interrupted and time.monotonic() < deadline:
                    continue
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                except ProcessLookupError:
                    pass
                raise RuntimeError("Training interrupted by service shutdown" if interrupted else "Training timed out")


def _run(request_path: Path) -> int:  # pragma: no cover - exercised only with real data
    from pipeline.config import load_config
    from pipeline.evaluate import evaluate_model
    from pipeline.promotion import assess_registry_entry, write_promotion_report
    from pipeline.registry import register_model
    from pipeline.temporal_dataset import create_locked_temporal_splits, materialize_temporal_dataset
    from pipeline.train import train_model

    request = json.loads(request_path.read_text())
    run_dir = Path(request["run_dir"])
    features = run_dir / "dataset"
    result = materialize_temporal_dataset([Path(p) for p in request["sessions"]], features)
    split_path = run_dir / "locked_splits.json"
    # Keep holdout membership fixed across iterations, then snapshot the exact
    # manifest used by this run for reproducibility.
    shared_split = Path(request["experimental_workspace"]) / "locked_splits.json"
    shared_split.parent.mkdir(parents=True, exist_ok=True)
    split_manifest = create_locked_temporal_splits(result.manifest_path, shared_split)
    shutil.copy2(shared_split, split_path)
    config = load_config()
    # The local loop must never start an external experiment-logging session.
    config.wandb.enabled = False
    model_path = run_dir / "model.pt"
    trained = train_model(features_dir=features, output_path=model_path, config=config,
                          dataset_hash=result.dataset_hash, data_source="canonical_v2_loop",
                          split_manifest_path=split_path)
    metrics = evaluate_model(features, model_path, config, split_manifest_path=split_path,
                             split_name="validation_locked")
    test_metrics = evaluate_model(features, model_path, config, split_manifest_path=split_path,
                                  split_name="test_locked")
    entry = register_model(model_path=model_path, repo_root=run_dir, config=config,
                           dataset_manifest=result.manifest_path, split_manifest=split_manifest,
                           metrics={"validation_locked": metrics, "test_locked": test_metrics},
                           model_id=trained.get("model_id"))
    promotion = assess_registry_entry(entry)
    write_promotion_report(entry, promotion)
    write_json_atomic(run_dir / "result.json", {
        "completed_at": utc_now(),
        "dataset_hash": result.dataset_hash,
        "registry_entry": str(entry),
        "metrics": metrics,
        "test_metrics": test_metrics,
        "promotion_assessment": promotion.to_dict(),
        "promoted": False,
    })
    return 0


if __name__ == "__main__":  # pragma: no cover
    from .process_lifetime import watch_service_parent

    watch_service_parent()
    sys.exit(_run(Path(sys.argv[1])))
