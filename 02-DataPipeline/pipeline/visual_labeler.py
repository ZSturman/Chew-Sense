"""Retired lab-only visual auto-labeler retained for fixture parity.

Production computer-vision labeling is owned by
``02b-VideoAssistedLabeling``. This module must not be registered or deployed.

The legacy model predicts chew strokes and
bite-segment probability from a video, used to bootstrap stroke / bite
ground-truth labels for the unified review UI in
`04-RealWorldTesting/.../SessionReviewView`.

This model is **never shipped**. It is a dataset tool whose only job is
to reduce reviewer time by pre-filling high-quality stroke seeds and
bite proposals. The shipped chewing detector remains `ChewNet` (trained
on AirPods motion alone). `pipeline/deploy.py` and `pipeline/registry.py`
explicitly refuse to publish `visual_labeler.pth`.

Architecture
------------
Per-frame mouth/jaw features from `pipeline/mouth_shape.py` (shape
[T, F=4]) → 3 dilated 1D-conv blocks (kernel=5, dilations 1/2/4) → 1
bidirectional GRU (hidden=64) → two sigmoid heads:

    * stroke_logits  : per-frame probability of a chew-stroke peak
    * bite_logits    : per-frame probability of being inside a bite

Targets are derived from the unified `bite_review.json` artifact:
strokes → Gaussian-smoothed impulse train (σ = 1 frame); bites → 0/1
mask. Frames marked `face_present=False` in `face_presence.json` are
masked out of both losses.

CLI
---

    python -m pipeline.visual_labeler train --bundles data/raw_sessions \
        --out models/visual_labeler.pth
    python -m pipeline.visual_labeler infer --bundle <session_dir>
    python -m pipeline.visual_labeler seed  --bundle <session_dir>

`seed` runs inference and writes `stroke_seeds.json` (canonical
ChewStroke schema) + `bite_seeds.json` (BiteEvent schema) into the
bundle. `pipeline/stroke_seeds.py` delegates here when a trained model
exists.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np

from .events import BiteEvent, ChewStroke

# Lab-only sentinel. `pipeline/deploy.py` and `pipeline/registry.py`
# refuse to publish artifacts whose path matches this constant.
LAB_ONLY_MODEL_FILENAME = "visual_labeler.pth"

DEFAULT_FRAME_RATE = 30.0
NUM_FEATURES = 4   # mouth_openness, mouth_width, jaw_displacement, lip_compression
STROKE_THRESHOLD = 0.5
BITE_THRESHOLD = 0.5


# ----------------------------------------------------------------------
# Feature loading
# ----------------------------------------------------------------------

def _load_bundle_features(
    bundle_dir: Path,
) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray, float]]:
    """Return (features [T,F], timestamps [T], face_present_mask [T], fps)
    for a session bundle, or None if the video can't be processed."""
    from .mouth_shape import (
        MEDIAPIPE_AVAILABLE,
        extract_mouth_shape_from_video,
        mouth_shape_to_dataframe,
        write_face_presence_json,
    )

    if not MEDIAPIPE_AVAILABLE:
        return None

    bundle_dir = Path(bundle_dir)
    video = next(
        (p for ext in ("*.mov", "*.mp4", "*.m4v") for p in bundle_dir.glob(ext)),
        None,
    )
    if video is None:
        return None

    mouth_frames, presence, fps = extract_mouth_shape_from_video(
        video, return_presence=True
    )

    # Persist face-presence track so the review UI and bite_metrics can
    # consume it. (Idempotent: written every time we touch the bundle.)
    write_face_presence_json(bundle_dir, presence, fps)

    if not presence:
        return None

    # Build a uniformly-sampled feature matrix at video fps. Frames with
    # no face become zero rows (and are masked out via `face_mask`).
    presence_by_idx = {p.frame_idx: p for p in presence}
    mouth_by_idx = {f.frame_idx: f for f in mouth_frames}
    num_frames = max(presence_by_idx) + 1
    features = np.zeros((num_frames, NUM_FEATURES), dtype=np.float32)
    timestamps = np.zeros(num_frames, dtype=np.float32)
    face_mask = np.zeros(num_frames, dtype=bool)
    for idx in range(num_frames):
        p = presence_by_idx.get(idx)
        if p is None:
            timestamps[idx] = idx / fps
            continue
        timestamps[idx] = p.timestamp_sec
        face_mask[idx] = p.face_present
        m = mouth_by_idx.get(idx)
        if m is not None:
            features[idx, 0] = m.mouth_openness
            features[idx, 1] = m.mouth_width
            features[idx, 2] = m.jaw_displacement
            features[idx, 3] = m.lip_compression

    return features, timestamps, face_mask, fps


# ----------------------------------------------------------------------
# Targets from `bite_review.json`
# ----------------------------------------------------------------------

def _targets_from_review(
    bundle_dir: Path,
    timestamps: np.ndarray,
    fps: float,
) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """Return (stroke_target [T], bite_target [T]) from the reviewer's
    `bite_review.json`, or None if the bundle has no review file."""
    review = Path(bundle_dir) / "bite_review.json"
    if not review.exists():
        return None
    try:
        payload = json.loads(review.read_text())
    except Exception:
        return None

    T = timestamps.shape[0]
    stroke_target = np.zeros(T, dtype=np.float32)
    bite_target = np.zeros(T, dtype=np.float32)

    # Strokes → Gaussian-smoothed impulse train (σ = 1 frame).
    sigma = 1.0
    radius = 3
    for s in payload.get("strokes", []):
        t = float(s.get("timestamp", 0.0))
        idx = int(round(t * fps))
        for k in range(max(0, idx - radius), min(T, idx + radius + 1)):
            stroke_target[k] = max(
                stroke_target[k],
                float(np.exp(-((k - idx) ** 2) / (2 * sigma * sigma))),
            )

    for b in payload.get("bites", []):
        s = float(b.get("start_time", 0.0))
        e = float(b.get("end_time", 0.0))
        i0 = max(0, int(round(s * fps)))
        i1 = min(T, int(round(e * fps)))
        if i1 > i0:
            bite_target[i0:i1] = 1.0

    return stroke_target, bite_target


# ----------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------

def _build_model(num_features: int = NUM_FEATURES, hidden: int = 64):
    """Construct the 1D-CNN + bi-GRU + dual-head model. Imported lazily
    so the module is importable without torch installed (e.g. on systems
    that only run the heuristic seeder)."""
    import torch
    import torch.nn as nn

    class VisualLabelerModel(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.conv = nn.Sequential(
                nn.Conv1d(num_features, 32, kernel_size=5, padding=2, dilation=1),
                nn.ReLU(),
                nn.Conv1d(32, 32, kernel_size=5, padding=4, dilation=2),
                nn.ReLU(),
                nn.Conv1d(32, 32, kernel_size=5, padding=8, dilation=4),
                nn.ReLU(),
            )
            self.gru = nn.GRU(
                input_size=32,
                hidden_size=hidden,
                batch_first=True,
                bidirectional=True,
            )
            self.stroke_head = nn.Linear(hidden * 2, 1)
            self.bite_head = nn.Linear(hidden * 2, 1)

        def forward(self, x):  # x: [B, T, F]
            # Conv expects [B, F, T].
            h = self.conv(x.transpose(1, 2)).transpose(1, 2)
            h, _ = self.gru(h)
            return (
                self.stroke_head(h).squeeze(-1),
                self.bite_head(h).squeeze(-1),
            )

    return VisualLabelerModel()


# ----------------------------------------------------------------------
# Training
# ----------------------------------------------------------------------

def train(
    bundle_dirs: list[Path],
    out_path: Path,
    epochs: int = 20,
    lr: float = 1e-3,
    device: Optional[str] = None,
) -> Path:
    """Train the visual labeler on bundles that have both a video and a
    `bite_review.json`. Bundles without review labels are skipped."""
    import torch
    import torch.nn.functional as F

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")

    samples: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
    for bundle in bundle_dirs:
        loaded = _load_bundle_features(bundle)
        if loaded is None:
            continue
        feats, ts, face_mask, fps = loaded
        targets = _targets_from_review(bundle, ts, fps)
        if targets is None:
            print(f"  Skipping {bundle.name}: no bite_review.json")
            continue
        stroke_t, bite_t = targets
        samples.append((feats, stroke_t, bite_t, face_mask))

    if not samples:
        raise RuntimeError(
            "No labeled bundles found. Need bundles with both a video and "
            "bite_review.json (run review in the iOS app, save reviewed session)."
        )

    print(f"Training on {len(samples)} labeled bundles, device={device}")

    model = _build_model().to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)

    for epoch in range(epochs):
        total = 0.0
        for feats, stroke_t, bite_t, face_mask in samples:
            x = torch.from_numpy(feats).unsqueeze(0).to(device)
            ys = torch.from_numpy(stroke_t).unsqueeze(0).to(device)
            yb = torch.from_numpy(bite_t).unsqueeze(0).to(device)
            mask = torch.from_numpy(face_mask).unsqueeze(0).to(device).float()

            stroke_logits, bite_logits = model(x)
            l_stroke = F.binary_cross_entropy_with_logits(
                stroke_logits, ys, weight=mask, reduction="sum"
            )
            l_bite = F.binary_cross_entropy_with_logits(
                bite_logits, yb, weight=mask, reduction="sum"
            )
            denom = mask.sum().clamp_min(1.0)
            loss = (l_stroke + l_bite) / denom
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += float(loss.item())
        print(f"  epoch {epoch + 1}/{epochs}  avg_loss={total / len(samples):.4f}")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.name != LAB_ONLY_MODEL_FILENAME:
        # Force the canonical name so the deploy/registry guards can
        # recognise it. We accept any directory but enforce the basename.
        out_path = out_path.with_name(LAB_ONLY_MODEL_FILENAME)
    torch.save({
        "state_dict": model.state_dict(),
        "num_features": NUM_FEATURES,
        "lab_only": True,
    }, out_path)
    print(f"Saved visual labeler -> {out_path}")
    return out_path


# ----------------------------------------------------------------------
# Inference
# ----------------------------------------------------------------------

def _peak_indices(probs: np.ndarray, min_separation: int, threshold: float) -> list[int]:
    if probs.size < 3:
        return []
    peaks: list[int] = []
    last = -10**9
    for i in range(1, probs.size - 1):
        v = float(probs[i])
        if v <= threshold:
            continue
        if v <= probs[i - 1] or v < probs[i + 1]:
            continue
        if (i - last) < min_separation:
            continue
        peaks.append(i)
        last = i
    return peaks


def _bite_intervals_from_mask(
    mask: np.ndarray,
    timestamps: np.ndarray,
    min_duration_sec: float,
) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    in_run = False
    start = 0
    for i, on in enumerate(mask):
        if on and not in_run:
            in_run = True
            start = i
        elif not on and in_run:
            in_run = False
            t0 = float(timestamps[start])
            t1 = float(timestamps[i - 1]) if i - 1 < len(timestamps) else t0
            if t1 - t0 >= min_duration_sec:
                intervals.append((t0, t1))
    if in_run:
        t0 = float(timestamps[start])
        t1 = float(timestamps[-1])
        if t1 - t0 >= min_duration_sec:
            intervals.append((t0, t1))
    return intervals


def infer_bundle(
    bundle_dir: Path,
    model_path: Path,
    min_chew_period_sec: float = 0.35,
    min_bite_duration_sec: float = 1.0,
    device: Optional[str] = None,
) -> Optional[dict]:
    """Run the trained labeler on a bundle. Returns a dict with strokes,
    bites and face-presence info, or None if inference can't run."""
    import torch

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")

    loaded = _load_bundle_features(bundle_dir)
    if loaded is None:
        return None
    feats, ts, face_mask, fps = loaded

    ckpt = torch.load(model_path, map_location=device)
    model = _build_model(num_features=ckpt.get("num_features", NUM_FEATURES))
    model.load_state_dict(ckpt["state_dict"])
    model.to(device).eval()

    with torch.no_grad():
        x = torch.from_numpy(feats).unsqueeze(0).to(device)
        stroke_logits, bite_logits = model(x)
        stroke_probs = torch.sigmoid(stroke_logits).squeeze(0).cpu().numpy()
        bite_probs = torch.sigmoid(bite_logits).squeeze(0).cpu().numpy()

    # Mask predictions where the face is missing — we can't trust them.
    stroke_probs = stroke_probs * face_mask
    bite_probs = bite_probs * face_mask

    min_sep = max(1, int(round(min_chew_period_sec * fps)))
    peak_idxs = _peak_indices(stroke_probs, min_sep, STROKE_THRESHOLD)
    strokes = [
        ChewStroke(
            timestamp=float(ts[i]),
            confidence=float(stroke_probs[i]),
            source="auto",
        )
        for i in peak_idxs
    ]

    bite_mask = bite_probs > BITE_THRESHOLD
    bite_intervals = _bite_intervals_from_mask(bite_mask, ts, min_bite_duration_sec)
    bites = [
        BiteEvent(
            start_time=t0,
            end_time=t1,
            confidence=float(np.mean(bite_probs[
                int(round(t0 * fps)):int(round(t1 * fps))
            ])) if t1 > t0 else 0.0,
            chew_count=sum(1 for s in strokes if t0 <= s.timestamp <= t1),
        )
        for t0, t1 in bite_intervals
    ]

    return {
        "strokes": strokes,
        "bites": bites,
        "fps": fps,
        "video": next(
            (p.name for ext in ("*.mov", "*.mp4", "*.m4v")
             for p in Path(bundle_dir).glob(ext)),
            None,
        ),
    }


def seed_bundle(
    bundle_dir: Path,
    model_path: Path,
    overwrite: bool = False,
    min_chew_period_sec: float = 0.35,
    min_bite_duration_sec: float = 1.0,
) -> Optional[Path]:
    """Run inference and write `stroke_seeds.json` + `bite_seeds.json`."""
    bundle_dir = Path(bundle_dir)
    seeds_path = bundle_dir / "stroke_seeds.json"
    bites_path = bundle_dir / "bite_seeds.json"

    if seeds_path.exists() and bites_path.exists() and not overwrite:
        return seeds_path

    result = infer_bundle(
        bundle_dir,
        model_path,
        min_chew_period_sec=min_chew_period_sec,
        min_bite_duration_sec=min_bite_duration_sec,
    )
    if result is None:
        return None

    stroke_payload = {
        "video": result["video"],
        "n_strokes": len(result["strokes"]),
        "source": "visual_labeler",
        "strokes": [asdict(s) for s in result["strokes"]],
    }
    seeds_path.write_text(json.dumps(stroke_payload, indent=2))

    bite_payload = {
        "video": result["video"],
        "n_bites": len(result["bites"]),
        "source": "visual_labeler",
        "bites": [b.to_dict() if hasattr(b, "to_dict") else asdict(b)
                  for b in result["bites"]],
    }
    bites_path.write_text(json.dumps(bite_payload, indent=2))

    print(f"  Wrote {len(result['strokes'])} stroke seeds, "
          f"{len(result['bites'])} bite seeds -> {bundle_dir}")
    return seeds_path


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Lab-only visual auto-labeler for ChewSense review."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_train = sub.add_parser("train", help="Train the visual labeler.")
    p_train.add_argument("--bundles", required=True,
                         help="Directory containing one session bundle per subdir.")
    p_train.add_argument("--out", default="models/visual_labeler.pth")
    p_train.add_argument("--epochs", type=int, default=20)

    p_infer = sub.add_parser("infer", help="Print inference for a bundle.")
    p_infer.add_argument("--bundle", required=True)
    p_infer.add_argument("--model", default="models/visual_labeler.pth")

    p_seed = sub.add_parser(
        "seed",
        help="Write stroke_seeds.json + bite_seeds.json into a bundle.",
    )
    p_seed.add_argument("--bundle", required=True)
    p_seed.add_argument("--model", default="models/visual_labeler.pth")
    p_seed.add_argument("--overwrite", action="store_true")

    args = p.parse_args()

    if args.cmd == "train":
        bundles_root = Path(args.bundles)
        bundle_dirs = [d for d in bundles_root.iterdir() if d.is_dir()]
        train(bundle_dirs, Path(args.out), epochs=args.epochs)
    elif args.cmd == "infer":
        result = infer_bundle(Path(args.bundle), Path(args.model))
        if result is None:
            print("No video / mediapipe unavailable.")
            return
        print(json.dumps({
            "n_strokes": len(result["strokes"]),
            "n_bites": len(result["bites"]),
            "fps": result["fps"],
        }, indent=2))
    elif args.cmd == "seed":
        seed_bundle(
            Path(args.bundle),
            Path(args.model),
            overwrite=args.overwrite,
        )


if __name__ == "__main__":
    main()
