"""Evaluation metrics for chew-detection and event-segmentation."""
from __future__ import annotations

import numpy as np


def frame_f1(pred: np.ndarray, gt: np.ndarray, positive_class: int) -> dict[str, float]:
    p = (pred == positive_class)
    g = (gt == positive_class)
    tp = int(np.logical_and(p, g).sum())
    fp = int(np.logical_and(p, ~g).sum())
    fn = int(np.logical_and(~p, g).sum())
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"precision": prec, "recall": rec, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def event_f1_at_iou(
    pred_events: list[tuple[float, float]],
    gt_events: list[tuple[float, float]],
    iou_threshold: float = 0.5,
) -> dict[str, float]:
    matched_gt: set[int] = set()
    tp = 0
    for ps, pe in pred_events:
        best_iou = 0.0
        best_j = -1
        for j, (gs, ge) in enumerate(gt_events):
            if j in matched_gt:
                continue
            inter = max(0.0, min(pe, ge) - max(ps, gs))
            union = max(pe, ge) - min(ps, gs)
            if union <= 0:
                continue
            iou = inter / union
            if iou > best_iou:
                best_iou = iou
                best_j = j
        if best_iou >= iou_threshold and best_j >= 0:
            tp += 1
            matched_gt.add(best_j)
    fp = len(pred_events) - tp
    fn = len(gt_events) - tp
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"precision": prec, "recall": rec, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def chew_timestamp_mae(
    pred_peaks: np.ndarray,
    gt_peaks: np.ndarray,
    max_match_distance_s: float = 0.4,
) -> dict[str, float]:
    if len(pred_peaks) == 0 or len(gt_peaks) == 0:
        return {"mae_s": float("nan"), "matched": 0,
                "missed": int(len(gt_peaks)), "extra": int(len(pred_peaks))}
    used_gt = np.zeros(len(gt_peaks), dtype=bool)
    used_pred = np.zeros(len(pred_peaks), dtype=bool)
    diffs: list[float] = []
    for i, p in enumerate(pred_peaks):
        d = np.abs(gt_peaks - p)
        d[used_gt] = np.inf
        j = int(np.argmin(d))
        if d[j] <= max_match_distance_s:
            diffs.append(float(d[j]))
            used_gt[j] = True
            used_pred[i] = True
    return {
        "mae_s": float(np.mean(diffs)) if diffs else float("nan"),
        "matched": int(used_pred.sum()),
        "missed": int((~used_gt).sum()),
        "extra": int((~used_pred).sum()),
    }
