"""Temporal chew model, supervision targets, replay decoder, and metrics.

This is the v2 motion contract.  The shipping candidate consumes exactly
three seconds of six-axis IMU resampled to 50 Hz plus a per-sample validity
mask.  It is evaluated every 100 ms and emits five values in a fixed order::

    eating_logit, chew_event_logit, phase_sin, phase_cos, phase_confidence

Individual chews are decoded only from local maxima of ``chew_event_logit``.
Phase is auxiliary evidence and is never used as a zero-crossing counter.
Legacy aggregate-feature models remain readable by the registry, but new
individual-chew models must use this module's contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp, pi
from typing import Iterable, Mapping, Sequence

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


TEMPORAL_CONTRACT_VERSION = "2.0.0"
SAMPLE_RATE_HZ = 50
CONTEXT_SECONDS = 3.0
CONTEXT_SAMPLES = int(SAMPLE_RATE_HZ * CONTEXT_SECONDS)
INFERENCE_STEP_SECONDS = 0.1
IMU_CHANNELS = ("ax", "ay", "az", "gx", "gy", "gz")
MODEL_INPUT_CHANNELS = (*IMU_CHANNELS, "gap_valid")
MODEL_OUTPUTS = (
    "eating_logit",
    "chew_event_logit",
    "phase_sin",
    "phase_cos",
    "phase_confidence",
)

# Target layout used by ``temporal_multitask_loss``.
TARGET_EATING = 0
TARGET_EVENT = 1
TARGET_PHASE_SIN = 2
TARGET_PHASE_COS = 3
TARGET_PHASE_CONFIDENCE = 4
MASK_EATING = 5
MASK_EVENT = 6
MASK_PHASE = 7
TARGET_WIDTH = 8


class CausalConv1d(nn.Module):
    """CoreML-friendly left-padded 1-D convolution."""

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        dilation: int = 1,
    ) -> None:
        super().__init__()
        self.left_padding = (kernel_size - 1) * dilation
        self.conv = nn.Conv1d(
            in_channels,
            out_channels,
            kernel_size=kernel_size,
            dilation=dilation,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(F.pad(x, (self.left_padding, 0)))


class _TemporalBlock(nn.Module):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        dilation: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.conv1 = CausalConv1d(in_channels, out_channels, kernel_size, dilation)
        self.conv2 = CausalConv1d(out_channels, out_channels, kernel_size, dilation)
        self.dropout = nn.Dropout(dropout)
        self.residual = (
            nn.Identity()
            if in_channels == out_channels
            else nn.Conv1d(in_channels, out_channels, kernel_size=1)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = self.residual(x)
        y = self.dropout(F.relu(self.conv1(x)))
        y = self.dropout(F.relu(self.conv2(y)))
        return F.relu(y + residual)


class TemporalChewNet(nn.Module):
    """Small causal TCN suitable for PyTorch -> CoreML conversion.

    Input shape is ``(batch, 7, 150)``.  The first six channels are normalized
    IMU axes and channel seven is the unmodified 0/1 gap-validity mask.
    Output shape is ``(batch, 5)`` in ``MODEL_OUTPUTS`` order.
    """

    def __init__(
        self,
        input_channels: int = len(MODEL_INPUT_CHANNELS),
        channels: Sequence[int] = (24, 24, 32, 32),
        kernel_size: int = 5,
        dropout: float = 0.10,
    ) -> None:
        super().__init__()
        blocks: list[nn.Module] = []
        previous = input_channels
        for level, width in enumerate(channels):
            blocks.append(
                _TemporalBlock(
                    previous,
                    int(width),
                    kernel_size=kernel_size,
                    dilation=2**level,
                    dropout=dropout,
                )
            )
            previous = int(width)
        self.tcn = nn.Sequential(*blocks)
        self.head = nn.Linear(previous, len(MODEL_OUTPUTS))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.ndim != 3:
            raise ValueError(f"expected (batch, channels, time), got {tuple(x.shape)}")
        features = self.tcn(x)[:, :, -1]
        raw = self.head(features)
        return torch.cat(
            (
                raw[:, 0:2],
                torch.tanh(raw[:, 2:4]),
                torch.sigmoid(raw[:, 4:5]),
            ),
            dim=1,
        )


def temporal_multitask_loss(
    output: torch.Tensor,
    target: torch.Tensor,
    *,
    eating_weight: float = 1.0,
    event_weight: float = 2.0,
    phase_weight: float = 0.5,
    confidence_weight: float = 0.25,
) -> tuple[torch.Tensor, dict[str, float]]:
    """Masked v2 loss.

    A legacy/coarse-only row has ``event_mask == phase_mask == 0`` and can
    therefore train only the eating head.  Reviewed but unobservable cycles
    likewise never contribute event/phase supervision.
    """

    if output.ndim != 2 or output.shape[1] != len(MODEL_OUTPUTS):
        raise ValueError("temporal output must have shape (batch, 5)")
    if target.ndim != 2 or target.shape[1] != TARGET_WIDTH:
        raise ValueError(f"temporal target must have shape (batch, {TARGET_WIDTH})")

    def masked_mean(values: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        weight = mask.to(dtype=values.dtype)
        denominator = weight.sum()
        if bool(denominator > 0):
            return (values * weight).sum() / denominator
        return torch.zeros((), dtype=values.dtype, device=values.device)

    eating = masked_mean(
        F.binary_cross_entropy_with_logits(
            output[:, 0], target[:, TARGET_EATING], reduction="none"
        ),
        target[:, MASK_EATING],
    )
    event = masked_mean(
        F.binary_cross_entropy_with_logits(
            output[:, 1], target[:, TARGET_EVENT], reduction="none"
        ),
        target[:, MASK_EVENT],
    )
    phase_error = (
        (output[:, 2] - target[:, TARGET_PHASE_SIN]).square()
        + (output[:, 3] - target[:, TARGET_PHASE_COS]).square()
    )
    phase = masked_mean(phase_error, target[:, MASK_PHASE])
    confidence = masked_mean(
        F.binary_cross_entropy(
            output[:, 4].clamp(1e-6, 1 - 1e-6),
            target[:, TARGET_PHASE_CONFIDENCE],
            reduction="none",
        ),
        # Confidence is part of event/phase supervision.  Coarse-only and
        # migrated-v1 sessions must not train it as an all-zero target.
        target[:, MASK_EVENT],
    )

    total = (
        eating_weight * eating
        + event_weight * event
        + phase_weight * phase
        + confidence_weight * confidence
    )
    return total, {
        "loss_total": float(total.detach()),
        "loss_eating": float(eating.detach()),
        "loss_event": float(event.detach()),
        "loss_phase": float(phase.detach()),
        "loss_phase_confidence": float(confidence.detach()),
    }


def gaussian_event_targets(
    timestamps: Sequence[float] | np.ndarray,
    event_timestamps: Sequence[float],
    *,
    sigma_seconds: float = 0.08,
) -> np.ndarray:
    """Return max-composed Gaussian targets centered on ``end_closed_ts``."""

    t = np.asarray(timestamps, dtype=np.float64)
    result = np.zeros(t.shape, dtype=np.float32)
    if sigma_seconds <= 0:
        raise ValueError("sigma_seconds must be positive")
    for event in event_timestamps:
        result = np.maximum(
            result,
            np.exp(-0.5 * ((t - float(event)) / sigma_seconds) ** 2).astype(np.float32),
        )
    return result


def build_temporal_targets(
    timestamps: Sequence[float] | np.ndarray,
    *,
    eating_intervals: Sequence[tuple[float, float]] = (),
    cycles: Sequence[Mapping[str, object]] = (),
    supervision: str = "reviewed",
    event_sigma_seconds: float = 0.08,
) -> np.ndarray:
    """Materialize v2 targets at inference timestamps.

    ``supervision='coarse'`` (including migrated v1 sessions) intentionally
    zeros event and phase masks.  Only cycles whose disposition is accepted
    and observable contribute individual-chew supervision.
    """

    t = np.asarray(timestamps, dtype=np.float64)
    target = np.zeros((len(t), TARGET_WIDTH), dtype=np.float32)
    target[:, MASK_EATING] = 1.0
    for start, end in eating_intervals:
        target[(t >= float(start)) & (t <= float(end)), TARGET_EATING] = 1.0

    if supervision != "reviewed":
        return target

    observable: list[tuple[float, float, float]] = []
    for cycle in cycles:
        disposition = str(cycle.get("disposition", "accepted"))
        if disposition not in {"accepted", "edited", "added"}:
            continue
        if cycle.get("observable", True) is False:
            continue
        try:
            start = float(cycle["start_closed_ts"])
            peak = float(cycle["open_peak_ts"])
            end = float(cycle["end_closed_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if not start < peak < end:
            continue
        observable.append((start, peak, end))

    target[:, MASK_EVENT] = 1.0
    target[:, TARGET_EVENT] = gaussian_event_targets(
        t, [cycle[2] for cycle in observable], sigma_seconds=event_sigma_seconds
    )

    # Phase supervision exists only inside observable closed-open-closed cycles.
    # Adjacent cycles may share a closed boundary; later cycles own the shared
    # sample so the event remains at 2π immediately before the reset to 0.
    for start, peak, end in observable:
        opening = (t >= start) & (t < peak)
        closing = (t >= peak) & (t <= end)
        theta = np.zeros_like(t)
        theta[opening] = pi * (t[opening] - start) / (peak - start)
        theta[closing] = pi + pi * (t[closing] - peak) / (end - peak)
        mask = opening | closing
        target[mask, TARGET_PHASE_SIN] = np.sin(theta[mask])
        target[mask, TARGET_PHASE_COS] = np.cos(theta[mask])
        target[mask, TARGET_PHASE_CONFIDENCE] = 1.0
        target[mask, MASK_PHASE] = 1.0
    return target


def resample_motion_window(
    timestamps: Sequence[float] | np.ndarray,
    imu: Sequence[Sequence[float]] | np.ndarray,
    *,
    end_timestamp: float | None = None,
    sample_rate_hz: int = SAMPLE_RATE_HZ,
    context_seconds: float = CONTEXT_SECONDS,
    max_gap_seconds: float = 0.08,
) -> tuple[np.ndarray, np.ndarray]:
    """Resample raw IMU and return ``(7,time)`` input plus grid timestamps.

    Linear interpolation is used for values.  The validity channel is zero
    outside the measured range and whenever the two samples bracketing a grid
    point are farther apart than ``max_gap_seconds``.  Invalid IMU values are
    zeroed so a model cannot mistake interpolation across a long outage for
    trustworthy motion.
    """

    source_t = np.asarray(timestamps, dtype=np.float64)
    values = np.asarray(imu, dtype=np.float32)
    if source_t.ndim != 1 or values.ndim != 2 or values.shape != (len(source_t), 6):
        raise ValueError("timestamps must be (n,) and imu must be (n, 6)")
    if len(source_t) < 2 or not np.all(np.isfinite(source_t)) or np.any(np.diff(source_t) <= 0):
        raise ValueError("at least two strictly increasing samples are required")
    if not np.all(np.isfinite(values)):
        raise ValueError("motion values must be finite")
    if sample_rate_hz <= 0 or context_seconds <= 0 or max_gap_seconds <= 0:
        raise ValueError("sample rate and context must be positive")

    sample_count = int(round(sample_rate_hz * context_seconds))
    end = float(source_t[-1] if end_timestamp is None else end_timestamp)
    if not np.isfinite(end):
        raise ValueError("end timestamp must be finite")
    grid = end - (sample_count - 1 - np.arange(sample_count)) / float(sample_rate_hz)
    output = np.zeros((7, sample_count), dtype=np.float32)
    for channel in range(6):
        output[channel] = np.interp(grid, source_t, values[:, channel]).astype(np.float32)

    right = np.searchsorted(source_t, grid, side="left")
    inside = (right > 0) & (right < len(source_t))
    left_index = np.clip(right - 1, 0, len(source_t) - 1)
    right_index = np.clip(right, 0, len(source_t) - 1)
    bracket_gap = source_t[right_index] - source_t[left_index]
    valid = inside & (bracket_gap <= max_gap_seconds)
    exact = (right < len(source_t)) & np.isclose(source_t[right_index], grid, rtol=0.0, atol=1e-9)
    exact_left = (right > 0) & np.isclose(source_t[left_index], grid, rtol=0.0, atol=1e-9)
    valid |= exact | exact_left
    output[:6, ~valid] = 0.0
    output[6] = valid.astype(np.float32)
    return output, grid


@dataclass(frozen=True)
class TemporalPrediction:
    timestamp: float
    eating_logit: float
    chew_event_logit: float
    phase_sin: float
    phase_cos: float
    phase_confidence: float
    gap_coverage: float = 1.0

    @property
    def eating_probability(self) -> float:
        return _sigmoid(self.eating_logit)

    @property
    def chew_event_probability(self) -> float:
        return _sigmoid(self.chew_event_logit)

    def to_dict(self) -> dict[str, float]:
        payload = asdict(self)
        payload["eating_probability"] = self.eating_probability
        payload["chew_event_probability"] = self.chew_event_probability
        return payload


@dataclass(frozen=True)
class DecodedChewEvent:
    timestamp: float
    confidence: float
    source: str = "temporal_event_head"


class ChewEventDecoder:
    """Streaming local-peak decoder shared conceptually with Live.

    A peak must be eating-gated, above threshold, backed by adequate valid
    input coverage, and at least 250 ms after the prior emitted event.  The
    one-sample lookahead means an event is reported on the inference step
    immediately after its timestamp; the stored event time remains the peak.
    """

    def __init__(
        self,
        *,
        event_threshold: float = 0.5,
        eating_threshold: float = 0.5,
        min_gap_coverage: float = 0.8,
        refractory_seconds: float = 0.25,
    ) -> None:
        self.event_threshold = float(event_threshold)
        self.eating_threshold = float(eating_threshold)
        self.min_gap_coverage = float(min_gap_coverage)
        self.refractory_seconds = float(refractory_seconds)
        self.raw_outputs: list[TemporalPrediction] = []
        self.decoded_events: list[DecodedChewEvent] = []
        self._previous_previous: TemporalPrediction | None = None
        self._previous: TemporalPrediction | None = None
        self._last_event_timestamp = float("-inf")

    def reset(self) -> None:
        self.raw_outputs.clear()
        self.decoded_events.clear()
        self._previous_previous = None
        self._previous = None
        self._last_event_timestamp = float("-inf")

    def push(self, prediction: TemporalPrediction) -> DecodedChewEvent | None:
        if self.raw_outputs and prediction.timestamp <= self.raw_outputs[-1].timestamp:
            raise ValueError("prediction timestamps must be strictly increasing")
        self.raw_outputs.append(prediction)
        emitted: DecodedChewEvent | None = None
        before = self._previous_previous
        peak = self._previous
        if before is not None and peak is not None:
            probability = peak.chew_event_probability
            is_local_peak = (
                probability >= before.chew_event_probability
                and probability > prediction.chew_event_probability
            )
            eligible = (
                is_local_peak
                and probability >= self.event_threshold
                and peak.eating_probability >= self.eating_threshold
                and peak.gap_coverage >= self.min_gap_coverage
                and peak.timestamp - self._last_event_timestamp >= self.refractory_seconds
            )
            if eligible:
                emitted = DecodedChewEvent(peak.timestamp, probability)
                self.decoded_events.append(emitted)
                self._last_event_timestamp = peak.timestamp
        self._previous_previous = self._previous
        self._previous = prediction
        return emitted


def replay_temporal_predictions(
    predictions: Iterable[TemporalPrediction],
    **decoder_kwargs: float,
) -> tuple[list[dict[str, float]], list[DecodedChewEvent]]:
    decoder = ChewEventDecoder(**decoder_kwargs)
    for prediction in predictions:
        decoder.push(prediction)
    return [row.to_dict() for row in decoder.raw_outputs], list(decoder.decoded_events)


def match_events(
    reference: Sequence[float],
    predicted: Sequence[float],
    *,
    tolerance_seconds: float,
) -> list[tuple[int, int]]:
    """One-to-one nearest matching within a fixed event tolerance."""

    candidates: list[tuple[float, int, int]] = []
    for ref_index, ref in enumerate(reference):
        for pred_index, pred in enumerate(predicted):
            error = abs(float(ref) - float(pred))
            if error <= tolerance_seconds:
                candidates.append((error, ref_index, pred_index))
    used_ref: set[int] = set()
    used_pred: set[int] = set()
    matches: list[tuple[int, int]] = []
    for _, ref_index, pred_index in sorted(candidates):
        if ref_index in used_ref or pred_index in used_pred:
            continue
        used_ref.add(ref_index)
        used_pred.add(pred_index)
        matches.append((ref_index, pred_index))
    return sorted(matches)


def chew_event_metrics(
    reference: Sequence[float],
    predicted: Sequence[float],
    *,
    tolerance_seconds: float = 0.25,
) -> dict[str, float | int | None]:
    """Promotion metrics for individual chews.

    Inter-chew interval error is calculated only between consecutive matched
    events, preventing unmatched false positives from being hidden by a sort.
    """

    ref = [float(value) for value in reference]
    pred = [float(value) for value in predicted]
    matches = match_events(ref, pred, tolerance_seconds=tolerance_seconds)
    tp = len(matches)
    fp = len(pred) - tp
    fn = len(ref) - tp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    count_error = abs(len(pred) - len(ref)) / len(ref) if ref else (0.0 if not pred else 1.0)

    interval_errors: list[float] = []
    for (ref_a, pred_a), (ref_b, pred_b) in zip(matches, matches[1:]):
        if ref_b == ref_a + 1 and pred_b == pred_a + 1:
            interval_errors.append(abs((pred[pred_b] - pred[pred_a]) - (ref[ref_b] - ref[ref_a])))
    median_ici_mae = float(np.median(interval_errors)) if interval_errors else None
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "chew_f1_at_250ms": f1,
        "chew_count_error": count_error,
        "median_inter_chew_interval_mae_sec": median_ici_mae,
    }


def eating_window_metrics(
    truth: Sequence[bool | int],
    probabilities: Sequence[float],
    *,
    threshold: float = 0.5,
    hard_negative_mask: Sequence[bool | int] | None = None,
) -> dict[str, float]:
    y = np.asarray(truth, dtype=bool)
    p = np.asarray(probabilities, dtype=float)
    if y.shape != p.shape:
        raise ValueError("truth and probabilities must have matching shapes")
    predicted = p >= threshold
    positive = y
    recall = float((predicted & positive).sum() / positive.sum()) if positive.any() else 0.0
    hard = (~y) if hard_negative_mask is None else np.asarray(hard_negative_mask, dtype=bool)
    if hard.shape != y.shape:
        raise ValueError("hard_negative_mask must match truth")
    hard_negative_fpr = float((predicted & hard).sum() / hard.sum()) if hard.any() else 0.0
    return {"eating_recall": recall, "hard_negative_fpr": hard_negative_fpr}


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)


__all__ = [
    "CONTEXT_SAMPLES",
    "CONTEXT_SECONDS",
    "ChewEventDecoder",
    "DecodedChewEvent",
    "INFERENCE_STEP_SECONDS",
    "MODEL_INPUT_CHANNELS",
    "MODEL_OUTPUTS",
    "SAMPLE_RATE_HZ",
    "TARGET_WIDTH",
    "TEMPORAL_CONTRACT_VERSION",
    "TemporalChewNet",
    "TemporalPrediction",
    "build_temporal_targets",
    "chew_event_metrics",
    "eating_window_metrics",
    "gaussian_event_targets",
    "match_events",
    "replay_temporal_predictions",
    "resample_motion_window",
    "temporal_multitask_loss",
]
