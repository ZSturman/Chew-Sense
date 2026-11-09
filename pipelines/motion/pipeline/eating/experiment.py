"""One reproducible chewing-bout experiment: data -> CV -> test -> report.

Every run writes a self-contained folder under
``<workspace>/experiments/eating/runs/<run_id>/`` with its configuration,
dataset manifest (provenance, rejections, fingerprint), split snapshot,
per-window predictions, metrics, model artifacts, training curves and an HTML
report, then appends a summary to ``index.json``.  Runs are experimental: no
model is registered, promoted, exported or deployed.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import torch

from .dataset import (
    EVALUATION_TIERS,
    LABEL_TIERS,
    EatingSession,
    build_windows,
    dataset_fingerprint,
    discover_sessions,
)
from .evaluate import (
    DecoderConfig,
    aggregate_bouts,
    bout_metrics,
    choose_threshold,
    grouped_window_metrics,
    session_bootstrap,
    threshold_sweep,
    window_metrics,
)
from .features import compute_features, feature_names
from .models import MODEL_NAMES, fit_model, inference_latency_ms, make_model, predict
from .splits import DEV, TEST, assign_splits, development_folds


@dataclass
class ExperimentConfig:
    window_s: float = 3.0
    stride_s: float = 0.5
    guard_s: float = 1.0
    min_valid_fraction: float = 0.9
    folds: int = 5
    models: tuple[str, ...] = MODEL_NAMES
    tcn_epochs: int = 12
    seed: int = 0
    test_fraction: float = 0.25
    training_tiers: tuple[str, ...] = LABEL_TIERS
    evaluation_tiers: tuple[str, ...] = EVALUATION_TIERS
    bootstrap_resamples: int = 1000
    evaluate_test: bool = True
    note: str = ""
    label_target: str = field(default="human_coarse_chewing_bout", init=False)


def _git_state(repo: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value)}")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n")


def _disagreement_spans(
    frame: pd.DataFrame, column: str, *, min_gap: float = 0.8, stride: float = 0.5
) -> list[dict[str, Any]]:
    """Group labelled windows the model confidently contradicts into review spans."""

    scored = frame[(frame["label"] >= 0) & frame[column].notna()].copy()
    scored["disagreement"] = (scored[column] - scored["label"]).abs()
    flagged = scored[scored["disagreement"] >= min_gap].sort_values(["session_id", "t_end"])
    spans: list[dict[str, Any]] = []
    for session_id, rows in flagged.groupby("session_id", sort=False):
        current: dict[str, Any] | None = None
        for row in rows.itertuples():
            if current and row.t_end - current["end"] <= stride + 1e-6 and row.label == current["label"]:
                current["end"] = row.t_end
                current["windows"] += 1
                current["probabilities"].append(getattr(row, column))
                continue
            if current:
                spans.append(current)
            current = {
                "session_id": session_id,
                "start": row.t_end,
                "end": row.t_end,
                "label": int(row.label),
                "tier": row.tier,
                "context": row.context,
                "split": row.split,
                "windows": 1,
                "probabilities": [getattr(row, column)],
            }
        if current:
            spans.append(current)
    for span in spans:
        span["mean_probability"] = float(np.mean(span.pop("probabilities")))
        span["window_start"] = span["start"]
        span["priority"] = span["windows"] * abs(span["mean_probability"] - span["label"])
    return sorted(spans, key=lambda s: -s["priority"])


def run_experiment(
    *,
    workspace: Path,
    config: ExperimentConfig,
    repo_root: Path | None = None,
    canonical_roots: list[Path] | None = None,
    field_test_roots: list[Path] | None = None,
    loop_root: Path | None = None,
    output_root: Path | None = None,
    log=print,
) -> Path:
    workspace = Path(workspace)
    output_root = Path(output_root) if output_root else workspace / "experiments" / "eating"
    canonical_roots = canonical_roots if canonical_roots is not None else [
        workspace / "sessions" / "raw",
        workspace / "loop" / "assisted" / "sessions" / "raw",
    ]
    field_test_roots = field_test_roots if field_test_roots is not None else [
        workspace / "sessions" / "field-tests"
    ]
    loop_root = loop_root if loop_root is not None else workspace / "loop"
    started = datetime.now(timezone.utc)
    run_id = started.strftime("eating-%Y%m%d-%H%M%S")
    run_dir = output_root / "runs" / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = output_root / "runs" / f"{started.strftime('eating-%Y%m%d-%H%M%S')}-{suffix}"
    run_id = run_dir.name
    (run_dir / "models").mkdir(parents=True, exist_ok=False)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)

    log(f"Run {run_id}")
    sessions, rejected = discover_sessions(
        canonical_roots=canonical_roots, field_test_roots=field_test_roots, loop_root=loop_root
    )
    if not sessions:
        raise RuntimeError("no usable sessions with human labels were found")
    by_id: dict[str, EatingSession] = {s.session_id: s for s in sessions}
    log(f"  sessions usable={len(sessions)} rejected={len(rejected)}")
    windows = build_windows(
        sessions,
        window_s=config.window_s,
        stride_s=config.stride_s,
        guard_s=config.guard_s,
        min_valid_fraction=config.min_valid_fraction,
    )
    features = compute_features(windows.X)
    split_payload = assign_splits(sessions, output_root / "splits.json", test_fraction=config.test_fraction)
    split_of = {sid: split_payload["assignments"][sid]["split"] for sid in by_id}
    dev_sessions = [s for s in sessions if split_of[s.session_id] == DEV]
    fold_of = development_folds(dev_sessions, k=config.folds)

    frame = pd.DataFrame({
        "session_id": windows.session_id,
        "t_end": windows.t_end,
        "label": windows.label.astype(int),
        "tier": windows.tier,
        "context": windows.context,
        "exclusion": windows.exclusion,
        "valid_fraction": windows.valid_fraction,
    })
    frame["split"] = frame["session_id"].map(split_of)
    frame["fold"] = frame["session_id"].map(fold_of).fillna(-1).astype(int)
    train_tier = frame["tier"].isin(config.training_tiers).to_numpy()
    eval_tier = frame["tier"].isin(config.evaluation_tiers).to_numpy()
    labelled = frame["label"].to_numpy() >= 0
    y_all = frame["label"].to_numpy()
    is_dev = (frame["split"] == DEV).to_numpy()
    is_test = (frame["split"] == TEST).to_numpy()
    usable_gap = frame["exclusion"].ne("sensor_gap").to_numpy()

    fingerprint_config = {
        k: v for k, v in asdict(config).items()
        if k in {"window_s", "stride_s", "guard_s", "min_valid_fraction", "training_tiers"}
    }
    fingerprint = dataset_fingerprint(sessions, fingerprint_config)
    models = [m for m in config.models if not (m == "tcn" and abs(config.window_s - 3.0) > 1e-9)]
    if len(models) != len(config.models):
        log("  tcn skipped: the deployable TCN contract requires 3-second windows")

    histories: dict[str, list[Any]] = {}
    for name in models:
        column = f"p_{name}"
        frame[column] = np.nan
        histories[name] = []
        # Development: out-of-fold predictions for every window of dev sessions.
        for fold in range(config.folds):
            fit_rows = is_dev & labelled & train_tier & (frame["fold"].to_numpy() != fold)
            held_sessions = frame["fold"].to_numpy() == fold
            if not held_sessions.any() or len(np.unique(y_all[fit_rows])) < 2:
                continue
            monitor_rows = held_sessions & labelled & eval_tier
            model = make_model(name, seed=config.seed + fold, tcn_epochs=config.tcn_epochs)
            fit_model(
                model, name, features[fit_rows], windows.X[fit_rows], y_all[fit_rows],
                monitor=(windows.X[monitor_rows], y_all[monitor_rows]) if name == "tcn" else None,
            )
            frame.loc[held_sessions, column] = predict(
                model, name, features[held_sessions], windows.X[held_sessions]
            )
            if name == "tcn":
                histories[name].append({"fold": fold, "history": model.history})
        # Final model: all development labels; evaluated once on test sessions.
        fit_rows = is_dev & labelled & train_tier
        final = make_model(name, seed=config.seed, tcn_epochs=config.tcn_epochs)
        fit_model(final, name, features[fit_rows], windows.X[fit_rows], y_all[fit_rows])
        if is_test.any() and config.evaluate_test:
            frame.loc[is_test, column] = predict(final, name, features[is_test], windows.X[is_test])
        if name == "tcn":
            torch.save(final.checkpoint(), run_dir / "models" / "tcn_eating_only.pt")
            histories[name].append({"fold": "final", "history": final.history})
        else:
            joblib.dump(final, run_dir / "models" / f"{name}.joblib")
        frame.loc[~usable_gap, column] = np.nan
        frame[f"latency_ms_{name}"] = inference_latency_ms(
            final, name, features[fit_rows][:200], windows.X[fit_rows][:200]
        )
        log(f"  trained {name}")

    def scored(split_mask: np.ndarray, column: str) -> np.ndarray:
        return split_mask & labelled & eval_tier & frame[column].notna().to_numpy()

    results: dict[str, Any] = {}
    for name in models:
        column = f"p_{name}"
        dev_rows = scored(is_dev, column)
        threshold = choose_threshold(y_all[dev_rows], frame.loc[dev_rows, column].to_numpy())
        entry: dict[str, Any] = {"threshold": threshold}
        for split_name, split_mask in (("dev_oof", is_dev), ("test", is_test)):
            rows = scored(split_mask, column)
            y = y_all[rows]
            p = frame.loc[rows, column].to_numpy()
            sids = frame.loc[rows, "session_id"].to_numpy()
            if not len(y):
                entry[split_name] = None
                continue
            metrics = window_metrics(y, p, threshold)
            ci = {}
            for metric in ("f1", "recall", "false_positive_rate", "auc_pr"):
                ci[metric] = session_bootstrap(
                    sids, y, p,
                    lambda yy, pp, m=metric: window_metrics(yy, pp, threshold)[m],
                    n_resamples=config.bootstrap_resamples,
                    seed=config.seed,
                )
            metrics["ci95"] = ci
            metrics["sessions"] = int(len(np.unique(sids)))
            metrics["by_context"] = grouped_window_metrics(frame.loc[rows, "context"].to_numpy(), y, p, threshold)
            metrics["by_tier"] = grouped_window_metrics(frame.loc[rows, "tier"].to_numpy(), y, p, threshold)
            metrics["by_session"] = grouped_window_metrics(sids, y, p, threshold)
            metrics["threshold_sweep"] = threshold_sweep(y, p)
            decoder = DecoderConfig.from_threshold(threshold)
            per_session = {}
            for sid in sorted(set(frame.loc[split_mask, "session_id"])):
                session_rows = frame[(frame["session_id"] == sid)].sort_values("t_end")
                per_session[sid] = bout_metrics(
                    by_id[sid],
                    session_rows["t_end"].to_numpy(),
                    session_rows[column].to_numpy(),
                    decoder,
                )
            metrics["bouts"] = aggregate_bouts(list(per_session.values()))
            metrics["bouts_by_session"] = per_session
            entry[split_name] = metrics
        entry["inference_latency_ms"] = float(frame[f"latency_ms_{name}"].iloc[0])
        results[name] = entry

    candidates = [m for m in models if m != "prior" and results[m].get("dev_oof")]
    selected = max(candidates, key=lambda m: results[m]["dev_oof"]["auc_pr"] or 0.0) if candidates else None
    review_queue = (
        _disagreement_spans(frame, f"p_{selected}", stride=config.stride_s)[:40] if selected else []
    )
    explanations = {}
    for name in ("motion_energy", "logreg"):
        path = run_dir / "models" / f"{name}.joblib"
        if path.exists():
            explanations[name] = joblib.load(path).explain()[:15]

    # ------------------------------------------------------------------ write
    session_rows = []
    for session in sessions:
        mask = frame["session_id"] == session.session_id
        session_rows.append({
            **session.summary(),
            "split": split_of[session.session_id],
            "fold": fold_of.get(session.session_id),
            "windows_total": int(mask.sum()),
            "windows_labelled": int((mask & (frame["label"] >= 0)).sum()),
            "windows_chewing": int((mask & (frame["label"] == 1)).sum()),
            "excluded_by_reason": frame.loc[mask & (frame["label"] < 0), "exclusion"].value_counts().to_dict(),
        })
    manifest = {
        "artifact_type": "eating_detection_dataset",
        "fingerprint": fingerprint,
        "label_target": config.label_target,
        "label_tiers": list(LABEL_TIERS),
        "never_labels": [
            "guided instructions", "vision proposals", "automatic sources",
            "unmarked field-test time (accepted model output)", "unknown spans",
        ],
        "participants": sorted({"local-user"}),
        "sessions": session_rows,
        "rejected": rejected,
        "windows": {
            "total": int(len(frame)),
            "labelled": int(labelled.sum()),
            "chewing": int((y_all == 1).sum()),
            "excluded": frame.loc[~labelled, "exclusion"].value_counts().to_dict(),
            "labelled_by_tier": frame.loc[labelled, "tier"].value_counts().to_dict(),
        },
    }
    finished = datetime.now(timezone.utc)
    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "git": _git_state(repo_root) if repo_root else {},
        "config": asdict(config),
        "dataset_fingerprint": fingerprint,
        "selected_model": selected,
        "selection_rule": "highest development out-of-fold AUC-PR, prior excluded",
        "status": "experimental (not registered, promoted, exported or deployed)",
        "feature_names": feature_names(),
        "counts": {
            "sessions": len(sessions),
            "participants": 1,
            "dev_sessions": len(dev_sessions),
            "test_sessions": int(sum(1 for s in sessions if split_of[s.session_id] == TEST)),
            "labelled_windows": int(labelled.sum()),
            "chewing_windows": int((y_all == 1).sum()),
            "chewing_bouts": int(sum(
                1 for s in sessions for i in s.intervals if i.label == "chewing"
            )),
            "motion_samples": int(sum(len(s.motion_t) for s in sessions)),
        },
        "results": results,
        "explanations": explanations,
        "review_queue": review_queue,
        "training_history": histories,
    }
    from .recommendations import collection_recommendations

    # Per-recording fingerprints let the dashboard tell a locked-test label
    # change (development results still current) from a development change.
    by_session = {s.session_id: s for s in sessions}
    summary["sessions_overview"] = [
        {"session_id": row["session_id"], "activity": row["activity"], "split": row["split"],
         "fingerprint": dataset_fingerprint([by_session[row["session_id"]]], fingerprint_config)
         if row["session_id"] in by_session else None}
        for row in session_rows
    ]
    summary["recommendations"] = collection_recommendations(summary)
    _write_json(run_dir / "config.json", asdict(config))
    _write_json(run_dir / "dataset_manifest.json", manifest)
    _write_json(run_dir / "splits.json", split_payload)
    _write_json(run_dir / "metrics.json", summary)
    frame.drop(columns=[c for c in frame.columns if c.startswith("latency_ms_")]).to_csv(
        run_dir / "predictions.csv.gz", index=False, compression="gzip"
    )

    index_path = output_root / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"runs": []}
    row = {
        "run_id": run_id,
        "finished_at": finished.isoformat(),
        "dataset_fingerprint": fingerprint,
        "selected_model": selected,
        "git_commit": summary["git"].get("commit"),
        "note": config.note,
        "config": {k: summary["config"][k] for k in ("window_s", "stride_s", "guard_s")},
        "counts": summary["counts"],
        "models": {
            name: {
                split: (
                    {k: results[name][split].get(k) for k in ("f1", "recall", "precision", "false_positive_rate", "auc_pr")}
                    if results[name].get(split) else None
                )
                for split in ("dev_oof", "test")
            }
            for name in models
        },
    }
    row["test_evaluated"] = config.evaluate_test
    index["runs"].append(row)
    # Decoder confirmations (decoder_study) also consult the locked test without a run.
    index["test_consultations"] = (sum(1 for run in index["runs"] if run.get("test_evaluated", True))
                                   + len(index.get("other_test_consultations") or []))
    _write_json(index_path, index)
    if config.evaluate_test:
        # The dashboards' "current model" is the latest run with a locked-test result;
        # exploratory --dev-only runs are listed in the index only.
        _write_json(output_root / "latest.json", {
            **row, "run_dir": str(run_dir), "report_path": str(run_dir / "report.html"),
        })

    from .report import write_report

    write_report(run_dir, summary=summary, manifest=manifest, frame=frame, sessions=by_id, index=index)
    log(f"  report: {run_dir / 'report.html'}")
    return run_dir


def rebuild_report(
    run_dir: Path,
    *,
    workspace: Path,
    canonical_roots: list[Path] | None = None,
    field_test_roots: list[Path] | None = None,
    loop_root: Path | None = None,
) -> Path:
    """Regenerate ``report.html`` from a run's saved artifacts without retraining."""

    from .report import write_report

    run_dir = Path(run_dir)
    workspace = Path(workspace)
    summary = json.loads((run_dir / "metrics.json").read_text())
    manifest = json.loads((run_dir / "dataset_manifest.json").read_text())
    frame = pd.read_csv(run_dir / "predictions.csv.gz")
    sessions, _ = discover_sessions(
        canonical_roots=canonical_roots if canonical_roots is not None else [
            workspace / "sessions" / "raw", workspace / "loop" / "assisted" / "sessions" / "raw",
        ],
        field_test_roots=field_test_roots if field_test_roots is not None else [workspace / "sessions" / "field-tests"],
        loop_root=loop_root if loop_root is not None else workspace / "loop",
    )
    by_id = {s.session_id: s for s in sessions}
    missing = sorted(set(frame["session_id"]) - set(by_id))
    if missing:
        raise RuntimeError(f"recordings used by this run are no longer readable: {missing}")
    index_path = run_dir.parent.parent / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"runs": []}
    return write_report(run_dir, summary=summary, manifest=manifest, frame=frame, sessions=by_id, index=index)
