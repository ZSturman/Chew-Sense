"""Legacy parity gate for the retired visual stroke seeder.

Production video labeling is owned by `02b-VideoAssistedLabeling`. This
script remains only for fixture-level parity checks before the old stroke
seeder is archived.

    stroke F1 (labeler) >= stroke F1 (heuristic) + min_gain_pct/100

Exit code 0 = pass, 2 = fail. Intended for the closed-loop CI step
inside the lab — never against shipped models.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "02-DataPipeline"))

from pipeline import stroke_seeds, visual_labeler  # noqa: E402
from pipeline.bite_metrics import _match_strokes, _f1  # noqa: E402
from pipeline.events import ChewStroke  # noqa: E402


def _truth_strokes(bundle_dir: Path) -> list[ChewStroke]:
    fp = bundle_dir / "bite_review.json"
    if not fp.exists():
        return []
    payload = json.loads(fp.read_text())
    out: list[ChewStroke] = []
    for s in payload.get("strokes", []):
        src = (s.get("source") or "manual").lower()
        if src in ("auto", "heuristic"):
            continue  # ignore seeder-created strokes; not ground truth
        try:
            out.append(ChewStroke(timestamp=float(s["timestamp"]), source=src))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _heuristic_strokes(bundle_dir: Path) -> list[ChewStroke]:
    """Run the mouth-openness heuristic without touching disk."""
    from pipeline.mouth_shape import extract_mouth_shape_from_video

    video = next(
        (p for ext in ("*.mov", "*.mp4", "*.m4v") for p in bundle_dir.glob(ext)),
        None,
    )
    if video is None:
        return []
    frames = extract_mouth_shape_from_video(video)
    return stroke_seeds._strokes_from_mouth_frames(frames)


def _labeler_strokes(bundle_dir: Path, model_path: Path) -> list[ChewStroke]:
    result = visual_labeler.infer_bundle(bundle_dir, model_path)
    if not result:
        return []
    return list(result.get("strokes", []))


def _aggregate_f1(
    bundles: Sequence[Path],
    pred_fn,
    tolerance_sec: float,
) -> float:
    tp = fp = fn = 0
    for b in bundles:
        truth = _truth_strokes(b)
        if not truth:
            continue
        pred = pred_fn(b)
        a, c, d = _match_strokes(pred, truth, tolerance_sec)
        tp += a
        fp += c
        fn += d
    return _f1(tp, fp, fn)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--bundles", nargs="+", required=True,
        help="Held-out session bundle directories with bite_review.json.",
    )
    parser.add_argument(
        "--model", required=True,
        help="Path to visual_labeler.pth.",
    )
    parser.add_argument(
        "--tolerance-sec", type=float, default=0.15,
        help="Stroke-match tolerance window (default 150 ms).",
    )
    parser.add_argument(
        "--min-gain-pct", type=float, default=10.0,
        help="Required absolute F1 gain in percentage points (default 10).",
    )
    args = parser.parse_args(argv)

    bundles = [Path(b) for b in args.bundles]
    model = Path(args.model)
    if model.name != visual_labeler.LAB_ONLY_MODEL_FILENAME:
        print(
            f"warning: model file is not named "
            f"{visual_labeler.LAB_ONLY_MODEL_FILENAME!r}; gate is intended "
            "only for the lab-only visual labeler.",
            file=sys.stderr,
        )

    f1_heur = _aggregate_f1(bundles, _heuristic_strokes, args.tolerance_sec)
    f1_lbl = _aggregate_f1(
        bundles,
        lambda b: _labeler_strokes(b, model),
        args.tolerance_sec,
    )

    gain_pp = (f1_lbl - f1_heur) * 100.0
    print(f"heuristic F1@{int(args.tolerance_sec*1000)}ms: {f1_heur:.3f}")
    print(f"labeler   F1@{int(args.tolerance_sec*1000)}ms: {f1_lbl:.3f}")
    print(f"gain: {gain_pp:+.1f} pp (required >= {args.min_gain_pct:.1f} pp)")

    if gain_pp + 1e-9 >= args.min_gain_pct:
        print("PASS: visual labeler beats heuristic by required margin.")
        return 0
    print("FAIL: visual labeler does not beat heuristic by required margin.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
