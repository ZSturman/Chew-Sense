"""
Transform stage: Add soft labels to raw CSVs.

Converts hard 0/1 labels to soft continuous labels (0.0-1.0) using
transition ramps. This helps the model learn smoother boundaries.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .config import SoftLabelsConfig, MouthShapeConfig


def detect_sampling_rate(t: np.ndarray) -> float:
    """Detect sampling rate from timestamps."""
    dt = np.diff(t)
    dt = dt[(dt > 0) & (dt < np.percentile(dt, 95))]
    if len(dt) == 0:
        return 100.0  # Default fallback
    return 1.0 / np.median(dt)


def compute_soft_labels(
    labels: np.ndarray,
    sampling_rate: float,
    config: SoftLabelsConfig,
) -> np.ndarray:
    """
    Compute soft continuous labels from hard 0/1 labels.
    
    Args:
        labels: Binary labels (0/1)
        sampling_rate: Sampling rate in Hz
        config: Soft label configuration
    
    Returns:
        Soft labels (0.0 to 1.0)
    """
    n = len(labels)
    
    soft_ramp_samples = int(config.transition_sec * sampling_rate)
    long_chew_samples = int(config.long_chew_sec * sampling_rate)
    long_ramp_samples = int(config.long_ramp_sec * sampling_rate)
    
    chewing_soft = np.zeros(n)
    
    # Find chewing segments
    segments = []
    in_segment = False
    start = 0
    
    for i in range(n):
        if labels[i] == 1 and not in_segment:
            start = i
            in_segment = True
        elif labels[i] == 0 and in_segment:
            segments.append((start, i))
            in_segment = False
    
    if in_segment:
        segments.append((start, n))
    
    # Apply soft label logic
    for start, end in segments:
        length = end - start
        
        if length < long_chew_samples:
            # Short chews: symmetric ramp up/down
            ramp = max(length // 2, 1)
            
            for i in range(start, start + ramp):
                chewing_soft[i] = (i - start) / ramp
            
            for i in range(start + ramp, end):
                chewing_soft[i] = (end - i) / ramp
        else:
            # Long chews: ramp up, plateau, ramp down
            ramp = min(long_ramp_samples, length // 2)
            ramp = max(ramp, 1)
            
            for i in range(start, start + ramp):
                chewing_soft[i] = (i - start) / ramp
            
            for i in range(start + ramp, end - ramp):
                chewing_soft[i] = 1.0
            
            for i in range(end - ramp, end):
                chewing_soft[i] = (end - i) / ramp
    
    return chewing_soft


def transform_csv(
    input_path: Path,
    output_path: Path,
    config: SoftLabelsConfig,
    label_col: str = "label",
    timestamp_col: Optional[str] = None,
    apply_review: bool = True,
    video_labels_root: Optional[Path] = None,
    require_verified_video_labels: bool = False,
) -> Path:
    """
    Transform a raw CSV by adding soft labels.
    
    Args:
        input_path: Path to input CSV
        output_path: Path for output CSV
        config: Soft label configuration
        label_col: Name of the label column
        timestamp_col: Timestamp column (auto-detect if None)
        apply_review: If True, look for review artifacts (`bite_review.json`,
            `review_feedback.csv`, `face_presence.json`) in the CSV's parent
            directory and overlay reviewer labels onto `label_col`. Rows in
            face-missing intervals without manual overrides are dropped before
            soft-label smoothing so the trainer never sees ambiguous data.
        video_labels_root: If set, look for Phase-2 video-derived labels under
            ``<root>/<session_id>/motion_aligned_labels.csv`` and overlay them.
            When present, the labeller's ``eating`` column is preferred over
            the legacy ``label`` column for soft-label computation, and
            ``chew_phase_sin``/``chew_phase_cos`` regression targets are added.
        require_verified_video_labels: Refuse to overlay automatic labels that
            have not completed the authoritative review workflow.

    Returns:
        Path to the output CSV
    """
    df = pd.read_csv(input_path)
    
    # Auto-detect timestamp column
    if timestamp_col is None:
        for c in ["timestamp", "time", "ts", "datetime"]:
            if c in df.columns:
                timestamp_col = c
                break
        if timestamp_col is None:
            raise ValueError(f"No timestamp column found in {input_path}")
    
    # Get timestamps for sampling rate detection
    if not np.issubdtype(df[timestamp_col].dtype, np.number):
        t = pd.to_datetime(df[timestamp_col]).astype("int64") / 1e9
    else:
        t = df[timestamp_col].astype(float).values
    
    sampling_rate = detect_sampling_rate(t)

    # Optional reviewer-label overlay. The reviewer flow in the iOS/macOS
    # debugging app writes review artifacts next to the bundle CSV; if any
    # exist we let them override `label_col` before soft-label smoothing.
    if apply_review:
        from .review_labels import overlay_review_labels
        bundle_dir = Path(input_path).parent
        try:
            df = overlay_review_labels(
                df,
                bundle_dir,
                timestamp_col=timestamp_col,
            )
        except Exception as exc:  # noqa: BLE001 — review overlay is best-effort
            print(f"  review overlay skipped ({exc}).")
        if "label_review" in df.columns:
            review = df["label_review"].to_numpy(dtype=int)
            base = df[label_col].astype(int).to_numpy()
            merged = np.where(review >= 0, review, base)
            df[label_col] = merged
            # Drop ambiguous (face-missing, no manual override) rows
            # entirely — the trainer must never see them.
            if "label_mask" in df.columns:
                keep = df["label_mask"].to_numpy(dtype=int) > 0
                if (~keep).any():
                    print(
                        f"  dropping {int((~keep).sum())} face-missing rows "
                        f"without manual override."
                    )
                    df = df.loc[keep].reset_index(drop=True)
                    # Re-derive timestamp array for soft-label smoothing.
                    if not np.issubdtype(df[timestamp_col].dtype, np.number):
                        t = pd.to_datetime(df[timestamp_col]).astype("int64") / 1e9
                    else:
                        t = df[timestamp_col].astype(float).values
                    sampling_rate = detect_sampling_rate(t)

    # Phase 4: optional video-derived label overlay. The Phase-2 bridge writes
    # `motion_aligned_labels.csv` per session under `derived/video_labels/`.
    # When present, the labeller's `eating` column is the source of truth for
    # the chewing/not-chewing binary label, and chew_phase_sin/cos targets are
    # carried into the transformed CSV for the Phase-5 two-head model.
    if video_labels_root is not None:
        from .video_labels import find_artifacts, overlay_video_labels
        session_id = Path(input_path).parent.name
        if session_id == "" or session_id == ".":
            session_id = Path(input_path).stem
        try:
            art = find_artifacts(
                session_id,
                Path(video_labels_root),
                raw_bundle_dir=Path(input_path).parent,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  video-label lookup failed ({exc}); falling back to legacy label.")
            art = None
        if art is not None and art.has_video_labels:
            if require_verified_video_labels and not art.verified:
                raise ValueError(
                    f"video labels for {session_id} are not verified; "
                    "finalize review before training"
                )
            df = overlay_video_labels(df, art, timestamp_col=timestamp_col)
            # Prefer the labeller's eating column as the binary label upstream
            # of soft-label smoothing. Keep the legacy `label` column intact
            # so any downstream consumer that still keys on it sees the new
            # source of truth too.
            df[label_col] = df["eating"].astype(int)
            df["phase_label_valid"] = (
                (df.get("label_valid", 1).astype(int) > 0)
                & (df["chew_id"].fillna("").astype(str) != "")
            ).astype(int)
            # Recompute sampling rate in case the overlay reordered rows.
            if not np.issubdtype(df[timestamp_col].dtype, np.number):
                t = pd.to_datetime(df[timestamp_col]).astype("int64") / 1e9
            else:
                t = df[timestamp_col].astype(float).values
            sampling_rate = detect_sampling_rate(t)
            print(
                f"  video labels overlaid: {int(df['eating'].sum())} eating rows, "
                f"{int((df['chew_id'] != '').sum())} chew rows."
            )

    # Compute soft labels
    labels = df[label_col].astype(int).values
    soft_labels = compute_soft_labels(labels, sampling_rate, config)
    
    # Add to dataframe
    df["chewing_soft"] = soft_labels
    
    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    
    return output_path


def transform_directory(
    input_dir: Path,
    output_dir: Path,
    config: SoftLabelsConfig,
    archive_dir: Optional[Path] = None,
    label_col: str = "label",
    timestamp_col: Optional[str] = None,
    postfix: str = "_transformed",
    video_labels_root: Optional[Path] = None,
    require_verified_video_labels: bool = True,
) -> list[Path]:
    """
    Transform all CSVs in a directory.
    
    Args:
        input_dir: Directory containing raw CSVs or session folders
        output_dir: Directory for transformed CSVs
        config: Soft label configuration
        archive_dir: If set, move originals here after processing
        label_col: Name of label column
        timestamp_col: Timestamp column (auto-detect if None)
        postfix: Postfix to add to output filenames
        video_labels_root: Derived video-label root. Canonical labels inside
            each session folder are preferred when available.
        require_verified_video_labels: Reject unverified video labels.
    
    Returns:
        List of paths to transformed CSVs
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if archive_dir:
        archive_dir = Path(archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
    
    transformed = []
    
    # Find all CSVs (in session folders or directly in input_dir)
    csv_files = []
    
    for item in input_dir.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            # Session folder - find CSV inside
            csvs = list(item.glob("*.csv"))
            csv_files.extend(csvs)
        elif item.is_file() and item.suffix.lower() == ".csv":
            csv_files.append(item)
    
    for csv_path in sorted(csv_files):
        print(f"Transforming: {csv_path.name}")
        
        # Output name
        out_name = f"{csv_path.stem}{postfix}{csv_path.suffix}"
        out_path = output_dir / out_name
        
        if out_path.exists():
            print(f"  Skipping (already exists): {out_path}")
            transformed.append(out_path)
            continue
        
        try:
            transform_csv(
                input_path=csv_path,
                output_path=out_path,
                config=config,
                label_col=label_col,
                timestamp_col=timestamp_col,
                video_labels_root=video_labels_root,
                require_verified_video_labels=require_verified_video_labels,
            )
            transformed.append(out_path)
            
            # Archive original if requested
            if archive_dir:
                dest = archive_dir / csv_path.name
                if not dest.exists():
                    shutil.copy2(csv_path, dest)
                    print(f"  Archived to: {dest}")
                    
        except Exception as e:
            print(f"  Error: {e}")
    
    print(f"✅ Transformed {len(transformed)} files to: {output_dir}")
    return transformed


def smooth_mouth_shape(
    csv_path: Path,
    output_path: Path,
    config: MouthShapeConfig,
) -> Path:
    """
    Apply optional temporal smoothing to mouth shape columns in an aligned CSV.

    If temporal_smooth_sigma is 0, the file is copied unchanged.

    Args:
        csv_path: Path to aligned CSV (with mouth shape columns)
        output_path: Path for smoothed output CSV
        config: Mouth shape configuration

    Returns:
        Path to the output CSV
    """
    from scipy.ndimage import gaussian_filter1d

    df = pd.read_csv(csv_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if config.temporal_smooth_sigma > 0:
        for col in config.parameters:
            if col in df.columns:
                df[col] = gaussian_filter1d(
                    df[col].values.astype(float),
                    sigma=config.temporal_smooth_sigma,
                )

    df.to_csv(output_path, index=False)
    return output_path
