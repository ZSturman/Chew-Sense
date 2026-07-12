"""
Bite/chew evaluation harness.

Operates on field-test bundles. Compares predicted bites/strokes against
human-reviewed ground truth from `bite_review.json` and emits a markdown
report in the workspace evaluation reports directory.

Metrics computed:

  * Chew-count MAPE            (per-bundle, aggregate, per-activity, per-texture)
  * Chew-stroke F1             at ±150 ms and ±250 ms tolerance windows
                               (greedy nearest-neighbour matching;
                               sufficient for ~1.5 Hz event spacing)
  * Bite-count absolute error  + MAPE
  * Bite-boundary IoU          (mean over matched bite pairs)
  * Hard-negative false-bite rate (bites per 10 min on negative activities)

Ground-truth source: `bite_review.json` written by the Swift
`BiteReviewView`. Bundles without `bite_review.json` are reported but
contribute only to the prediction-side aggregates.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import numpy as np

from .events import BiteEvent, ChewStroke


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------

@dataclass
class _PredBundle:
    bites: List[BiteEvent]
    strokes: List[ChewStroke]
    activity: Optional[str]
    activity_label: Optional[str]
    expected_label_class: Optional[str]
    bundle_dir: Path


@dataclass
class _GroundTruth:
    bites: List[BiteEvent]
    strokes: List[ChewStroke]


@dataclass
class _FacePresence:
    """Intervals where the face is/isn't visible. Strokes / bite spans
    that fall entirely inside `present=False` intervals are masked from
    F1, IoU and chew-count metrics, and counted toward `coverage_pct`."""
    intervals: list[tuple[float, float, bool]]

    @property
    def total_duration(self) -> float:
        if not self.intervals:
            return 0.0
        return float(self.intervals[-1][1] - self.intervals[0][0])

    @property
    def present_duration(self) -> float:
        return float(sum(e - s for s, e, p in self.intervals if p))

    @property
    def coverage_pct(self) -> float:
        total = self.total_duration
        return (self.present_duration / total) if total > 0 else 1.0

    def is_present(self, time: float) -> bool:
        for s, e, p in self.intervals:
            if s <= time < e:
                return p
        # Outside the recorded window, be conservative and treat as missing.
        return False

    def overlap_present(self, start: float, end: float) -> float:
        """Seconds of overlap between [start, end] and `present=True` intervals."""
        total = 0.0
        for s, e, p in self.intervals:
            if not p:
                continue
            o = max(0.0, min(end, e) - max(start, s))
            total += o
        return total


def _load_face_presence(bundle_dir: Path) -> Optional[_FacePresence]:
    fp = bundle_dir / "face_presence.json"
    if not fp.exists():
        return None
    try:
        payload = json.loads(fp.read_text())
    except Exception:
        return None
    intervals: list[tuple[float, float, bool]] = []
    for item in payload.get("intervals", []):
        try:
            intervals.append((
                float(item["start"]),
                float(item["end"]),
                bool(item["present"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    if not intervals:
        return None
    return _FacePresence(intervals=intervals)


def _filter_strokes_in_frame(
    strokes: Sequence[ChewStroke],
    face: Optional[_FacePresence],
) -> list[ChewStroke]:
    if face is None:
        return list(strokes)
    return [s for s in strokes if face.is_present(float(s.timestamp))]


def _filter_bites_in_frame(
    bites: Sequence[BiteEvent],
    face: Optional[_FacePresence],
    min_overlap_ratio: float = 0.5,
) -> list[BiteEvent]:
    if face is None:
        return list(bites)
    kept: list[BiteEvent] = []
    for b in bites:
        dur = max(0.0, b.end_time - b.start_time)
        if dur <= 0:
            continue
        ratio = face.overlap_present(b.start_time, b.end_time) / dur
        if ratio >= min_overlap_ratio:
            kept.append(b)
    return kept


def _load_predictions(bundle_dir: Path) -> _PredBundle:
    bites: List[BiteEvent] = []
    strokes: List[ChewStroke] = []

    bites_csv = bundle_dir / "bite_events.csv"
    if bites_csv.exists():
        with open(bites_csv, newline="") as f:
            for row in csv.DictReader(f):
                bites.append(BiteEvent(
                    start_time=float(row["start_time"]),
                    end_time=float(row["end_time"]),
                    confidence=float(row.get("confidence") or 0.0),
                    chew_count=int(float(row.get("chew_count") or 0)),
                ))

    chew_csv = bundle_dir / "chew_events.csv"
    if chew_csv.exists():
        with open(chew_csv, newline="") as f:
            for row in csv.DictReader(f):
                strokes.append(ChewStroke(
                    timestamp=float(row["timestamp"]),
                    confidence=float(row.get("confidence") or 1.0),
                    source=row.get("source") or "heuristic",
                ))

    meta = bundle_dir / "session_metadata.json"
    activity = None
    activity_label = None
    expected = None
    if meta.exists():
        try:
            payload = json.loads(meta.read_text())
        except Exception:
            payload = {}
        # Same keys as in field_tests.py and SessionReviewModels.
        rt = payload.get("runtime_config") or payload
        activity = payload.get("activity") or rt.get("activity")
        activity_label = payload.get("activity_label") or rt.get("activity_label")
        expected = payload.get("expected_label_class") or rt.get("expected_label_class")

    return _PredBundle(
        bites=bites,
        strokes=strokes,
        activity=activity,
        activity_label=activity_label,
        expected_label_class=expected,
        bundle_dir=bundle_dir,
    )


def _load_ground_truth(bundle_dir: Path) -> Optional[_GroundTruth]:
    review = bundle_dir / "bite_review.json"
    if not review.exists():
        return None
    try:
        payload = json.loads(review.read_text())
    except Exception:
        return None

    raw_bites = payload.get("bites", [])
    raw_strokes = payload.get("strokes", [])

    bites: List[BiteEvent] = []
    for b in raw_bites:
        try:
            start = float(b["start_time"])
            end = float(b["end_time"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        bites.append(BiteEvent(start_time=start, end_time=end))

    strokes: List[ChewStroke] = []
    for s in raw_strokes:
        try:
            t = float(s["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        src = s.get("source") or "manual"
        strokes.append(ChewStroke(timestamp=t, source=src))

    # Attribute strokes to bites for chew_count.
    bites.sort(key=lambda b: b.start_time)
    strokes.sort(key=lambda s: s.timestamp)
    for b in bites:
        in_bite = [s for s in strokes if b.start_time <= s.timestamp < b.end_time]
        b.chew_strokes = in_bite
        b.chew_count = len(in_bite)

    return _GroundTruth(bites=bites, strokes=strokes)


# ----------------------------------------------------------------------
# Matching helpers
# ----------------------------------------------------------------------

def _match_strokes(
    predicted: Sequence[ChewStroke],
    truth: Sequence[ChewStroke],
    tolerance_sec: float,
) -> tuple[int, int, int]:
    """Greedy nearest-neighbour matching by timestamp.

    Returns (true_positives, false_positives, false_negatives).
    """
    if not predicted and not truth:
        return 0, 0, 0
    pt = sorted(p.timestamp for p in predicted)
    gt = sorted(t.timestamp for t in truth)
    used_gt = [False] * len(gt)

    tp = 0
    for ts in pt:
        # binary search for the closest unused gt timestamp
        best = -1
        best_dt = float("inf")
        # linear scan is fine; chew streams are O(hundreds) per session
        for j, g in enumerate(gt):
            if used_gt[j]:
                continue
            dt = abs(g - ts)
            if dt < best_dt:
                best_dt = dt
                best = j
            if g - ts > tolerance_sec and dt > best_dt:
                break
        if best >= 0 and best_dt <= tolerance_sec:
            used_gt[best] = True
            tp += 1
    fp = len(pt) - tp
    fn = len(gt) - tp
    return tp, fp, fn


def _f1(tp: int, fp: int, fn: int) -> float:
    denom = 2 * tp + fp + fn
    return (2 * tp / denom) if denom > 0 else 0.0


def _bite_iou(a: BiteEvent, b: BiteEvent) -> float:
    inter_start = max(a.start_time, b.start_time)
    inter_end = min(a.end_time, b.end_time)
    inter = max(0.0, inter_end - inter_start)
    union = max(a.end_time, b.end_time) - min(a.start_time, b.start_time)
    return inter / union if union > 0 else 0.0


def _match_bites(
    predicted: Sequence[BiteEvent],
    truth: Sequence[BiteEvent],
    iou_threshold: float = 0.3,
) -> list[tuple[Optional[int], Optional[int], float]]:
    """Greedy IoU matching; returns (pred_idx | None, truth_idx | None, iou)."""
    used_t = [False] * len(truth)
    pairs: list[tuple[Optional[int], Optional[int], float]] = []
    for pi, p in enumerate(predicted):
        best_t = -1
        best_iou = 0.0
        for tj, t in enumerate(truth):
            if used_t[tj]:
                continue
            iou = _bite_iou(p, t)
            if iou > best_iou:
                best_iou = iou
                best_t = tj
        if best_t >= 0 and best_iou >= iou_threshold:
            used_t[best_t] = True
            pairs.append((pi, best_t, best_iou))
        else:
            pairs.append((pi, None, 0.0))
    for tj, used in enumerate(used_t):
        if not used:
            pairs.append((None, tj, 0.0))
    return pairs


# ----------------------------------------------------------------------
# Per-bundle metrics
# ----------------------------------------------------------------------

@dataclass
class BundleReport:
    bundle: str
    activity: Optional[str]
    expected_label_class: Optional[str]
    has_ground_truth: bool

    pred_bite_count: int
    pred_chew_count: int
    pred_duration: float

    gt_bite_count: int = 0
    gt_chew_count: int = 0

    bite_count_abs_error: float = 0.0
    bite_count_mape: float = 0.0
    chew_count_abs_error: float = 0.0
    chew_count_mape: float = 0.0

    stroke_f1_150ms: float = 0.0
    stroke_f1_250ms: float = 0.0
    stroke_tp_150ms: int = 0
    stroke_fp_150ms: int = 0
    stroke_fn_150ms: int = 0

    mean_bite_iou: float = 0.0
    matched_bite_pairs: int = 0

    # Face-presence coverage. 1.0 = face visible the whole session.
    coverage_pct: float = 1.0
    has_face_presence: bool = False


def _evaluate_bundle(bundle_dir: Path) -> BundleReport:
    pred = _load_predictions(bundle_dir)
    gt = _load_ground_truth(bundle_dir)
    face = _load_face_presence(bundle_dir)

    duration = 0.0
    if pred.bites:
        duration = float(pred.bites[-1].end_time - pred.bites[0].start_time)

    report = BundleReport(
        bundle=bundle_dir.name,
        activity=pred.activity,
        expected_label_class=pred.expected_label_class,
        has_ground_truth=gt is not None,
        pred_bite_count=len(pred.bites),
        pred_chew_count=sum((b.chew_count or 0) for b in pred.bites),
        pred_duration=duration,
    )
    if face is not None:
        report.has_face_presence = True
        report.coverage_pct = face.coverage_pct

    if gt is None:
        return report

    # Mask out strokes / bites that fall in face-missing intervals —
    # the visual ground truth is unreliable there.
    pred_strokes = _filter_strokes_in_frame(pred.strokes, face)
    gt_strokes = _filter_strokes_in_frame(gt.strokes, face)
    pred_bites = _filter_bites_in_frame(pred.bites, face)
    gt_bites = _filter_bites_in_frame(gt.bites, face)

    report.gt_bite_count = len(gt_bites)
    report.gt_chew_count = len(gt_strokes)

    # Bite-count error
    bd = abs(len(pred_bites) - report.gt_bite_count)
    report.bite_count_abs_error = float(bd)
    if report.gt_bite_count > 0:
        report.bite_count_mape = bd / report.gt_bite_count

    # Chew-count error (aggregate over the session)
    cd = abs(len(pred_strokes) - report.gt_chew_count)
    report.chew_count_abs_error = float(cd)
    if report.gt_chew_count > 0:
        report.chew_count_mape = cd / report.gt_chew_count

    # Stroke event F1 at two tolerances
    tp1, fp1, fn1 = _match_strokes(pred_strokes, gt_strokes, 0.150)
    tp2, fp2, fn2 = _match_strokes(pred_strokes, gt_strokes, 0.250)
    report.stroke_tp_150ms, report.stroke_fp_150ms, report.stroke_fn_150ms = tp1, fp1, fn1
    report.stroke_f1_150ms = _f1(tp1, fp1, fn1)
    report.stroke_f1_250ms = _f1(tp2, fp2, fn2)

    # Bite-boundary IoU on matched pairs
    pairs = _match_bites(pred_bites, gt_bites)
    matched = [p for p in pairs if p[0] is not None and p[1] is not None]
    if matched:
        report.matched_bite_pairs = len(matched)
        report.mean_bite_iou = float(np.mean([m[2] for m in matched]))

    return report


# ----------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------

NEGATIVE_ACTIVITIES = {
    "head_shake", "nodding", "talking", "singing",
    "walking", "drinking_water", "baseline",
}


@dataclass
class AggregateReport:
    bundles: List[BundleReport] = field(default_factory=list)

    @property
    def with_gt(self) -> list[BundleReport]:
        return [b for b in self.bundles if b.has_ground_truth]

    def chew_count_mape(self) -> float:
        with_gt = [b for b in self.with_gt if b.gt_chew_count > 0]
        if not with_gt:
            return float("nan")
        return float(np.mean([b.chew_count_mape for b in with_gt]))

    def bite_count_mape(self) -> float:
        with_gt = [b for b in self.with_gt if b.gt_bite_count > 0]
        if not with_gt:
            return float("nan")
        return float(np.mean([b.bite_count_mape for b in with_gt]))

    def stroke_f1(self, tolerance_ms: int) -> float:
        # Micro-F1 across all sessions
        tp = fp = fn = 0
        for b in self.with_gt:
            if tolerance_ms == 150:
                tp += b.stroke_tp_150ms; fp += b.stroke_fp_150ms; fn += b.stroke_fn_150ms
        if tolerance_ms != 150:
            # f1_250ms is per-bundle macro-only; expose per-bundle mean
            f1s = [b.stroke_f1_250ms for b in self.with_gt]
            return float(np.mean(f1s)) if f1s else float("nan")
        return _f1(tp, fp, fn)

    def hard_negative_false_bite_rate(self) -> dict[str, float]:
        """Bites per 10 minutes on each negative-class activity."""
        seconds_by_activity: dict[str, float] = defaultdict(float)
        bites_by_activity: dict[str, int] = defaultdict(int)
        for b in self.bundles:
            if b.activity not in NEGATIVE_ACTIVITIES:
                continue
            seconds_by_activity[b.activity] += b.pred_duration
            bites_by_activity[b.activity] += b.pred_bite_count
        out: dict[str, float] = {}
        for activity in seconds_by_activity:
            secs = seconds_by_activity[activity]
            if secs <= 0:
                out[activity] = 0.0
                continue
            out[activity] = bites_by_activity[activity] / (secs / 600.0)
        return out

    def per_activity_chew_mape(self) -> dict[str, float]:
        groups: dict[str, list[float]] = defaultdict(list)
        for b in self.with_gt:
            if b.gt_chew_count <= 0 or not b.activity:
                continue
            groups[b.activity].append(b.chew_count_mape)
        return {k: float(np.mean(v)) for k, v in groups.items() if v}


def evaluate_bundles(bundle_dirs: Iterable[Path]) -> AggregateReport:
    agg = AggregateReport()
    for d in bundle_dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        agg.bundles.append(_evaluate_bundle(d))
    return agg


def discover_bundles(root: Path, max_depth: int = 3) -> list[Path]:
    """Find bundle-shaped directories anywhere under `root` (up to `max_depth` levels).

    Bundles are identified by the presence of `predictions.csv` or
    `bite_events.csv`.  The `_archive` convention nests them two levels deep
    (`_archive/<batch>/<bundle>/`), so the default depth of 3 covers both
    flat layouts and archived ones.
    """
    root = Path(root)
    if not root.exists():
        return []
    out: list[Path] = []

    def _walk(d: Path, depth: int) -> None:
        if depth == 0:
            return
        try:
            children = sorted(d.iterdir())
        except PermissionError:
            return
        for child in children:
            if not child.is_dir():
                continue
            if (child / "predictions.csv").exists() or (child / "bite_events.csv").exists():
                out.append(child)
            else:
                _walk(child, depth - 1)

    _walk(root, max_depth)
    return out


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------

def render_report_md(agg: AggregateReport) -> str:
    lines: list[str] = []
    lines.append("# Chew/Bite Evaluation Report")
    lines.append("")
    lines.append(f"- Bundles evaluated: **{len(agg.bundles)}**")
    lines.append(f"- With ground truth: **{len(agg.with_gt)}**")
    lines.append("")

    lines.append("## Aggregate")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("| --- | --- |")
    lines.append(f"| chew-count MAPE | {agg.chew_count_mape():.3f} |")
    lines.append(f"| bite-count MAPE | {agg.bite_count_mape():.3f} |")
    lines.append(f"| stroke F1 \u00b1150 ms (micro) | {agg.stroke_f1(150):.3f} |")
    lines.append(f"| stroke F1 \u00b1250 ms (macro mean) | {agg.stroke_f1(250):.3f} |")
    lines.append("")

    lines.append("## Hard-negative false-bite rate (bites per 10 min)")
    lines.append("")
    fbr = agg.hard_negative_false_bite_rate()
    if fbr:
        lines.append("| activity | bites / 10 min |")
        lines.append("| --- | --- |")
        for act, rate in sorted(fbr.items()):
            lines.append(f"| `{act}` | {rate:.2f} |")
    else:
        lines.append("_No bundles tagged with negative activities found._")
    lines.append("")

    per_act = agg.per_activity_chew_mape()
    lines.append("## Per-activity chew-count MAPE")
    lines.append("")
    if per_act:
        lines.append("| activity | chew-count MAPE |")
        lines.append("| --- | --- |")
        for act, mape in sorted(per_act.items()):
            lines.append(f"| `{act}` | {mape:.3f} |")
    else:
        lines.append("_No activity-tagged bundles with ground truth._")
    lines.append("")

    lines.append("## Per-bundle")
    lines.append("")
    lines.append(
        "| bundle | activity | gt? | coverage | pred bites | gt bites | bite MAPE | "
        "pred chews | gt chews | chew MAPE | F1@150 | F1@250 | bite IoU |"
    )
    lines.append("| --- | --- | :-: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |")
    for b in agg.bundles:
        cov = f"{b.coverage_pct:.2f}" if b.has_face_presence else "-"
        lines.append(
            f"| `{b.bundle}` | `{b.activity or '-'}` | "
            f"{'✓' if b.has_ground_truth else '·'} | {cov} | "
            f"{b.pred_bite_count} | {b.gt_bite_count} | {b.bite_count_mape:.3f} | "
            f"{b.pred_chew_count} | {b.gt_chew_count} | {b.chew_count_mape:.3f} | "
            f"{b.stroke_f1_150ms:.3f} | {b.stroke_f1_250ms:.3f} | "
            f"{b.mean_bite_iou:.3f} |"
        )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "BundleReport",
    "AggregateReport",
    "evaluate_bundles",
    "discover_bundles",
    "render_report_md",
]
