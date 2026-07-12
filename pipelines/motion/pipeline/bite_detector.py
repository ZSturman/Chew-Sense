"""Historical bite-segmentation prototype.

Canonical production semantics are now ``bite_event`` (food-entry timestamp)
and ``chewing_bout`` (linked interval) in
``pipelines/vision/chewsense/session_bundle.py``. This module is
retained for migration and fixture parity only.

The legacy detector segments continuous chewing into intervals.

A "bite" is a contiguous run of frames above the chewing-probability
threshold, separated from neighbouring bites by at least `min_gap_sec`
of below-threshold (idle) time. Within each bite, individual chew
strokes are counted by detecting peaks in the chewing-probability
signal — or, when provided, in the 1-3 Hz acceleration band-power
signal which is more directly tied to jaw motion at ~1.5 Hz.

Public types come from the canonical schema module so that bite
segmentation, meal analysis, and the chew/bite metrics evaluation
harness all speak the same language:

    from pipeline.events import ChewStroke, BiteEvent
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from pipeline.events import BiteEvent, ChewStroke  # noqa: E402


@dataclass
class BiteDetectorConfig:
    """Configuration for bite detection algorithm."""
    min_gap_sec: float = 1.5            # min below-threshold gap to split bites
    min_bite_duration: float = 1.0      # min bite duration to keep (seconds)
    probability_threshold: float = 0.5  # threshold for chewing state
    # Chew-stroke counting: jaw motion is ~1.5 Hz, so peaks can't be closer
    # than ~0.4 s. Tunable for slower/faster chewers.
    min_chew_period_sec: float = 0.35


class BiteDetector:
    """Segments continuous chewing-probability signals into discrete bites."""

    def __init__(self, config: BiteDetectorConfig | None = None):
        self.config = config or BiteDetectorConfig()

    # ------------------------------------------------------------------
    def detect_bites(
        self,
        timestamps: np.ndarray,
        probabilities: np.ndarray,
        chew_signal: Optional[np.ndarray] = None,
    ) -> List[BiteEvent]:
        """Detect bite events from a chewing-probability signal.

        Args:
            timestamps:    1-D array of frame timestamps (seconds).
            probabilities: 1-D array of chewing probabilities in [0, 1].
            chew_signal:   Optional 1-D array used for chew-stroke peak
                           counting (e.g. the 1-3 Hz band-power feature).
                           If omitted, peaks are detected in `probabilities`.

        Returns:
            List of `BiteEvent`, one per accepted bite, with `chew_count`
            and per-stroke `chew_strokes` populated.
        """
        timestamps = np.asarray(timestamps, dtype=float)
        probabilities = np.asarray(probabilities, dtype=float)
        if timestamps.shape != probabilities.shape:
            raise ValueError("timestamps and probabilities must have the same shape")
        if timestamps.size == 0:
            return []

        cfg = self.config
        is_chew = probabilities >= cfg.probability_threshold
        segments = self._find_chewing_segments(timestamps, is_chew)
        merged = self._merge_close_segments(timestamps, segments, cfg.min_gap_sec)

        peak_signal = chew_signal if chew_signal is not None else probabilities
        peak_signal = np.asarray(peak_signal, dtype=float)
        if peak_signal.shape != timestamps.shape:
            raise ValueError("chew_signal must have the same shape as timestamps")

        bites: List[BiteEvent] = []
        for start_idx, end_idx in merged:
            duration = float(timestamps[end_idx] - timestamps[start_idx])
            if duration < cfg.min_bite_duration:
                continue
            confidence = float(np.mean(probabilities[start_idx : end_idx + 1]))
            strokes = self._detect_strokes(
                timestamps[start_idx : end_idx + 1],
                peak_signal[start_idx : end_idx + 1],
            )
            bites.append(BiteEvent(
                start_time=float(timestamps[start_idx]),
                end_time=float(timestamps[end_idx]),
                confidence=confidence,
                chew_count=len(strokes),
                chew_strokes=strokes,
            ))
        return bites

    # ------------------------------------------------------------------
    def _find_chewing_segments(
        self,
        timestamps: np.ndarray,
        is_chewing: np.ndarray,
    ) -> List[Tuple[int, int]]:
        """Return inclusive (start_idx, end_idx) pairs for each True run."""
        if is_chewing.size == 0:
            return []
        padded = np.concatenate(([False], is_chewing, [False]))
        edges = np.diff(padded.astype(np.int8))
        starts = np.where(edges == 1)[0]
        ends = np.where(edges == -1)[0] - 1
        return list(zip(starts.tolist(), ends.tolist()))

    def _merge_close_segments(
        self,
        timestamps: np.ndarray,
        segments: List[Tuple[int, int]],
        min_gap_sec: float,
    ) -> List[Tuple[int, int]]:
        """Merge segments separated by less than `min_gap_sec` of idle time."""
        if not segments:
            return []
        merged = [segments[0]]
        for start, end in segments[1:]:
            prev_start, prev_end = merged[-1]
            gap = timestamps[start] - timestamps[prev_end]
            if gap < min_gap_sec:
                merged[-1] = (prev_start, end)
            else:
                merged.append((start, end))
        return merged

    def _detect_strokes(
        self,
        timestamps: np.ndarray,
        signal: np.ndarray,
    ) -> List[ChewStroke]:
        """Detect chew-stroke peaks within a single bite window.

        A point i is a peak if signal[i] > signal[i-1] and signal[i] >=
        signal[i+1] and at least `min_chew_period_sec` has elapsed since
        the last accepted peak. The mean of the local signal acts as a
        soft threshold so noise floors don't get counted as strokes.
        """
        if signal.size < 3:
            return []
        cfg = self.config
        last_t = -np.inf
        threshold = float(np.mean(signal))
        peak_max = float(np.max(signal)) if signal.size else 1.0
        denom = max(peak_max - threshold, 1e-9)
        strokes: List[ChewStroke] = []
        for i in range(1, signal.size - 1):
            v = float(signal[i])
            if v <= threshold:
                continue
            if v <= signal[i - 1] or v < signal[i + 1]:
                continue
            t = float(timestamps[i])
            if t - last_t < cfg.min_chew_period_sec:
                continue
            confidence = float(min(1.0, max(0.0, (v - threshold) / denom)))
            strokes.append(ChewStroke(
                timestamp=t,
                confidence=confidence,
                source="heuristic",
            ))
            last_t = t
        return strokes


# ----------------------------------------------------------------------
# Bundle-level entry point
# ----------------------------------------------------------------------

def detect_bites_from_bundle(
    bundle_dir: Path,
    config: BiteDetectorConfig | None = None,
    chew_signal_column: str = "chew_bandpower_1_3hz",
) -> List[BiteEvent]:
    """Run bite detection over a real-world session bundle.

    Reads `predictions.csv` (must include `timestamp` and `probability`,
    optionally `smoothed`), and uses `chew_signal_column` from
    `predictions.csv` if present, falling back to `probability`.

    Returns a list of `BiteEvent` and writes `bite_events.csv` and
    `chew_events.csv` into `bundle_dir`.
    """
    import csv

    bundle_dir = Path(bundle_dir)
    pred_csv = bundle_dir / "predictions.csv"
    if not pred_csv.exists():
        raise FileNotFoundError(f"No predictions.csv in bundle: {bundle_dir}")

    timestamps: list[float] = []
    probs: list[float] = []
    chew_sig: list[float] = []
    with open(pred_csv, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # `timestamp` in predictions.csv is an ISO-8601 string written by
            # the iOS app; the numeric session-relative axis lives in
            # `window_end` (preferred) or `window_start`.
            t_raw = row.get("window_end") or row.get("window_start") or row.get("timestamp")
            try:
                t = float(t_raw)
                p = float(row.get("smoothed") or row["probability"])
            except (KeyError, TypeError, ValueError):
                continue
            timestamps.append(t)
            probs.append(p)
            if chew_signal_column in row and row[chew_signal_column] not in (None, ""):
                try:
                    chew_sig.append(float(row[chew_signal_column]))
                except ValueError:
                    chew_sig.append(float("nan"))

    chew_arr = (
        np.asarray(chew_sig, dtype=float)
        if len(chew_sig) == len(timestamps) and len(chew_sig) > 0
        else None
    )

    detector = BiteDetector(config)
    bites = detector.detect_bites(
        np.asarray(timestamps, dtype=float),
        np.asarray(probs, dtype=float),
        chew_arr,
    )

    _write_bite_events_csv(bundle_dir / "bite_events.csv", bites)
    _write_chew_events_csv(bundle_dir / "chew_events.csv", bites)
    return bites


def _write_bite_events_csv(path: Path, bites: List[BiteEvent]) -> None:
    import csv
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["start_time", "end_time", "duration", "confidence", "chew_count"])
        for b in bites:
            w.writerow([
                f"{b.start_time:.6f}",
                f"{b.end_time:.6f}",
                f"{b.duration:.6f}",
                f"{b.confidence:.6f}",
                b.chew_count or 0,
            ])


def _write_chew_events_csv(path: Path, bites: List[BiteEvent]) -> None:
    import csv
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "bite_index", "confidence", "source"])
        for bite_idx, b in enumerate(bites):
            for s in b.chew_strokes:
                w.writerow([f"{s.timestamp:.6f}", bite_idx, f"{s.confidence:.6f}", s.source])


def main():
    """CLI: detect bites from a chewing log CSV or a session bundle."""
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Detect bites from chewing log")
    parser.add_argument("input", help="Path to chewing log CSV or session bundle directory")
    parser.add_argument("--min-gap", type=float, default=1.5)
    parser.add_argument("--min-duration", type=float, default=1.0)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--output", "-o", help="Output JSON path (default: stdout)")
    args = parser.parse_args()

    cfg = BiteDetectorConfig(
        min_gap_sec=args.min_gap,
        min_bite_duration=args.min_duration,
        probability_threshold=args.threshold,
    )

    in_path = Path(args.input)
    if in_path.is_dir():
        bites = detect_bites_from_bundle(in_path, cfg)
        source = str(in_path)
    else:
        import csv as _csv
        timestamps: list[float] = []
        probs: list[float] = []
        chew_sig: list[float] = []
        with open(in_path, newline="") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                timestamps.append(float(row["timestamp"]))
                probs.append(float(row["probability"]))
                if "chew_signal" in row and row["chew_signal"] != "":
                    chew_sig.append(float(row["chew_signal"]))
        detector = BiteDetector(cfg)
        bites = detector.detect_bites(
            np.asarray(timestamps),
            np.asarray(probs),
            np.asarray(chew_sig) if chew_sig else None,
        )
        source = str(in_path)

    payload = {
        "input": source,
        "n_bites": len(bites),
        "total_chew_count": sum(b.chew_count or 0 for b in bites),
        "bites": [b.to_dict() for b in bites],
    }
    text = json.dumps(payload, indent=2)
    if args.output:
        Path(args.output).write_text(text)
        print(f"Wrote {len(bites)} bites -> {args.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
