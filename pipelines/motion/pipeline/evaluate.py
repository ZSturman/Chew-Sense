"""Evaluate temporal chew models and legacy binary eating checkpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import torch

from .config import PipelineConfig
from .train import ChewNet
from .splits import indices_for_split
from .temporal import (
    ChewEventDecoder,
    MASK_EATING,
    MASK_EVENT,
    TARGET_EATING,
    TARGET_EVENT,
    TemporalChewNet,
    TemporalPrediction,
    match_events,
)

try:
    from sklearn.metrics import (
        accuracy_score,
        roc_auc_score,
        precision_score,
        recall_score,
        f1_score,
        confusion_matrix,
    )
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


def evaluate_model(
    features_dir: Path,
    model_path: Path,
    config: PipelineConfig,
    split_manifest_path: Path | None = None,
    split_name: str = "all",
    curated_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Evaluate trained model on feature data.
    
    Accepts the v2 temporal model or a legacy binary eating-only checkpoint.
    
    Args:
        features_dir: Directory containing X.npy and y.npy
        model_path: Path to trained model checkpoint
        config: Pipeline configuration
    
    Returns:
        Dictionary with evaluation metrics
    """
    if not SKLEARN_AVAILABLE:
        raise RuntimeError(
            "scikit-learn is required for evaluation.\n"
            "Install with: pip install scikit-learn"
        )
    
    features_dir = Path(features_dir)
    model_path = Path(model_path)
    
    # Load model
    ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
    mean = ckpt["mean"]
    std = ckpt["std"]
    mode = ckpt.get("mode", "binary")
    
    if mode == "temporal_tcn":
        model = TemporalChewNet(
            input_channels=ckpt.get("input_channels", 7),
            channels=ckpt.get("channels", [24, 24, 32, 32]),
            kernel_size=ckpt.get("kernel_size", 5),
            dropout=ckpt.get("dropout", 0.0),
        )
    elif mode == "binary":
        model = ChewNet(
            input_dim=ckpt["input_dim"],
            hidden_dim=ckpt["hidden_dim"],
        )
    else:
        raise ValueError(
            f"checkpoint mode {mode!r} is retired; retrain from canonical v2 temporal windows"
        )
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    
    # Load data
    X = np.load(features_dir / "X.npy").astype(np.float32)
    y = np.load(features_dir / "y.npy").astype(np.float32)

    session_ids_arr = None
    sid_path = features_dir / "session_ids.npy"
    if sid_path.exists():
        session_ids_arr = np.load(sid_path, allow_pickle=True)
    timestamps_path = features_dir / "timestamps.npy"
    timestamps_arr = (
        np.load(timestamps_path).astype(np.float64)
        if timestamps_path.exists()
        else None
    )
    hard_negative_path = features_dir / "hard_negative_mask.npy"
    hard_negative_arr = (
        np.load(hard_negative_path).astype(bool)
        if hard_negative_path.exists()
        else None
    )

    split_indices = indices_for_split(features_dir, split_manifest_path, split_name)
    if split_indices is not None:
        if len(split_indices) == 0:
            raise ValueError(f"Split '{split_name}' has no feature rows")
        X = X[split_indices]
        y = y[split_indices]
        if session_ids_arr is not None:
            session_ids_arr = session_ids_arr[split_indices]
        if timestamps_arr is not None:
            timestamps_arr = timestamps_arr[split_indices]
        if hard_negative_arr is not None:
            hard_negative_arr = hard_negative_arr[split_indices]
        print(f"Evaluating split '{split_name}' ({len(split_indices)} rows)")
    
    # Normalize and predict
    if mode == "temporal_tcn":
        X_norm = X.copy()
        X_norm[:, :6, :] = (
            X_norm[:, :6, :] - np.asarray(mean).reshape(1, 6, 1)
        ) / np.asarray(std).reshape(1, 6, 1)
        X_norm[:, :6, :] *= X_norm[:, 6:7, :]
    else:
        X_norm = (X - mean) / std
    X_t = torch.from_numpy(X_norm)
    
    with torch.no_grad():
        output = model(X_t).numpy()
    
    if mode == "temporal_tcn":
        metrics = _evaluate_temporal(
            y,
            output,
            X,
            config,
            session_ids=session_ids_arr,
            timestamps=timestamps_arr,
            hard_negative_mask=hard_negative_arr,
        )
    else:
        metrics = _evaluate_binary(y, output, config)
    metrics["split"] = split_name
    metrics["split_manifest_path"] = str(split_manifest_path) if split_manifest_path else None

    # Per-activity breakdown — only when we have session_ids and a curated dir
    if (
        mode == "binary"
        and session_ids_arr is not None
        and curated_dir is not None
    ):
        try:
            from .field_tests import session_activity_map
            activity_map = session_activity_map(Path(curated_dir))
            if activity_map:
                per_act: dict[str, dict[str, Any]] = {}
                for activity in sorted(set(activity_map.values())):
                    mask = np.array([
                        activity_map.get(str(sid)) == activity for sid in session_ids_arr
                    ], dtype=bool)
                    if not mask.any():
                        continue
                    sub_metrics = _evaluate_binary(y[mask], output[mask], config)
                    sub_metrics.pop("confusion_matrix", None)
                    per_act[activity] = sub_metrics
                if per_act:
                    metrics["per_activity"] = per_act
        except Exception as exc:  # pragma: no cover - diagnostic only
            metrics["per_activity_error"] = str(exc)

    return metrics


def _target_event_peaks(timestamps: np.ndarray, event_target: np.ndarray) -> list[float]:
    result: list[float] = []
    for index in range(len(event_target)):
        left = event_target[index - 1] if index else float("-inf")
        right = event_target[index + 1] if index + 1 < len(event_target) else float("-inf")
        if (
            event_target[index] >= 0.5
            and event_target[index] >= left
            and event_target[index] > right
        ):
            result.append(float(timestamps[index]))
    return result


def _evaluate_temporal(
    target: np.ndarray,
    output: np.ndarray,
    raw_windows: np.ndarray,
    config: PipelineConfig,
    *,
    session_ids: np.ndarray | None,
    timestamps: np.ndarray | None,
    hard_negative_mask: np.ndarray | None,
) -> dict[str, Any]:
    if target.ndim != 2 or target.shape[1] < 8:
        raise ValueError("temporal evaluation requires v2 eight-column targets")
    if timestamps is None or session_ids is None:
        raise ValueError(
            "temporal evaluation requires timestamps.npy and session_ids.npy for event replay"
        )

    eating_valid = target[:, MASK_EATING] >= 0.5
    if not np.any(eating_valid):
        raise ValueError("temporal evaluation has no valid eating supervision")
    eating = _evaluate_binary(
        target[eating_valid, TARGET_EATING],
        output[eating_valid, 0],
        config,
    )
    eating["eating_recall"] = eating["recall"]
    probabilities = 1.0 / (1.0 + np.exp(-output[:, 0]))
    hard = (
        hard_negative_mask
        if hard_negative_mask is not None
        else target[:, TARGET_EATING] < 0.5
    )
    hard = np.asarray(hard, dtype=bool) & eating_valid
    eating["hard_negative_fpr"] = (
        float((probabilities[hard] >= config.evaluation.pred_threshold).mean())
        if np.any(hard)
        else 0.0
    )

    runtime = config.runtime
    total_tp = total_fp = total_fn = 0
    total_reference = 0
    absolute_count_error = 0
    interval_errors: list[float] = []
    decoded_by_session: dict[str, list[float]] = {}
    for session_id in sorted(set(map(str, session_ids))):
        indices = np.flatnonzero(session_ids.astype(str) == session_id)
        indices = indices[np.argsort(timestamps[indices])]
        if not len(indices):
            continue
        decoder = ChewEventDecoder(
            event_threshold=runtime.chew_event_threshold,
            eating_threshold=config.evaluation.pred_threshold,
            min_gap_coverage=runtime.min_gap_coverage,
            refractory_seconds=runtime.event_refractory_sec,
        )
        for index in indices:
            decoder.push(
                TemporalPrediction(
                    timestamp=float(timestamps[index]),
                    eating_logit=float(output[index, 0]),
                    chew_event_logit=float(output[index, 1]),
                    phase_sin=float(output[index, 2]),
                    phase_cos=float(output[index, 3]),
                    phase_confidence=float(output[index, 4]),
                    gap_coverage=float(raw_windows[index, 6, :].mean()),
                )
            )
        local_timestamps = timestamps[indices]
        local_event_valid = target[indices, MASK_EVENT] >= 0.5
        if not np.any(local_event_valid):
            # Coarse/migrated sessions never become event negatives.
            continue
        predicted_raw = [event.timestamp for event in decoder.decoded_events]
        predicted: list[float] = []
        for event_timestamp in predicted_raw:
            nearest = int(np.argmin(np.abs(local_timestamps - event_timestamp)))
            if local_event_valid[nearest]:
                predicted.append(event_timestamp)
        event_target = np.where(
            local_event_valid,
            target[indices, TARGET_EVENT],
            0.0,
        )
        reference = _target_event_peaks(local_timestamps, event_target)
        decoded_by_session[session_id] = predicted
        matches = match_events(reference, predicted, tolerance_seconds=0.25)
        total_tp += len(matches)
        total_fp += len(predicted) - len(matches)
        total_fn += len(reference) - len(matches)
        total_reference += len(reference)
        absolute_count_error += abs(len(predicted) - len(reference))
        for (ref_a, pred_a), (ref_b, pred_b) in zip(matches, matches[1:]):
            if ref_b == ref_a + 1 and pred_b == pred_a + 1:
                interval_errors.append(
                    abs(
                        (predicted[pred_b] - predicted[pred_a])
                        - (reference[ref_b] - reference[ref_a])
                    )
                )

    precision = total_tp / (total_tp + total_fp) if total_tp + total_fp else 0.0
    recall = total_tp / (total_tp + total_fn) if total_tp + total_fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    eating.update(
        {
            "mode": "temporal_tcn",
            "chew_true_positives": total_tp,
            "chew_false_positives": total_fp,
            "chew_false_negatives": total_fn,
            "chew_f1_at_250ms": f1,
            "chew_count_error": (
                absolute_count_error / total_reference
                if total_reference
                else (0.0 if not absolute_count_error else 1.0)
            ),
            "median_inter_chew_interval_mae_sec": (
                float(np.median(interval_errors)) if interval_errors else None
            ),
            "decoded_chew_events_by_session": decoded_by_session,
        }
    )
    return eating


def _evaluate_binary(
    y_soft: np.ndarray,
    logits: np.ndarray,
    config: PipelineConfig,
) -> dict[str, Any]:
    """Evaluate binary chewing classification."""
    ec = config.evaluation
    y = (y_soft >= ec.chew_threshold).astype(np.int32).reshape(-1)
    
    probs = (1.0 / (1.0 + np.exp(-logits))).reshape(-1)  # sigmoid
    preds = (probs >= ec.pred_threshold).astype(np.int32)
    
    acc = accuracy_score(y, preds)
    try:
        auc = roc_auc_score(y, probs)
    except ValueError:
        auc = float("nan")
    prec = precision_score(y, preds, zero_division=0)
    rec = recall_score(y, preds, zero_division=0)
    f1 = f1_score(y, preds, zero_division=0)
    cm = confusion_matrix(y, preds, labels=[0, 1])
    tn, fp, fn, tp = [int(v) for v in cm.ravel()]
    n_positive = int(y.sum())
    n_negative = int(len(y) - y.sum())
    false_positive_rate = (fp / n_negative) if n_negative else 0.0
    false_negative_rate = (fn / n_positive) if n_positive else 0.0
    
    print("\nEVALUATION RESULTS (Binary Classification)")
    print("-------------------------------------------")
    print(f"Accuracy:   {acc:.4f}")
    print(f"ROC AUC:    {auc:.4f}")
    print(f"Precision:  {prec:.4f}")
    print(f"Recall:     {rec:.4f}")
    print(f"F1 Score:   {f1:.4f}")
    print("Confusion Matrix:")
    print(cm)
    
    return {
        "mode": "binary",
        "accuracy": float(acc),
        "roc_auc": float(auc),
        "precision": float(prec),
        "recall": float(rec),
        "f1": float(f1),
        "confusion_matrix": cm.tolist(),
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "true_positive": tp,
        "false_positive_rate": float(false_positive_rate),
        "false_negative_rate": float(false_negative_rate),
        "n_samples": len(y),
        "n_positive": n_positive,
        "n_negative": n_negative,
    }
