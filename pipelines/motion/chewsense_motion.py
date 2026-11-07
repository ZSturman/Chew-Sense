#!/usr/bin/env python3
"""ChewSense motion pipeline CLI.

A modular, composable pipeline for chewing detection model development.

Usage:
    chewsense-motion sample
    chewsense-motion from-app
    chewsense-motion from-raw
    chewsense-motion from-features
    chewsense-motion deploy

Each command supports --help for detailed options.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

# Ensure pipeline module is importable
sys.path.insert(0, str(Path(__file__).parent))

from pipeline.config import load_config, merge_cli_args, PipelineConfig
from pipeline.ingest import validate_sessions, create_manifest, save_manifest
from pipeline.transform import transform_directory
from pipeline.features import extract_features_directory
from pipeline.train import train_model
from pipeline.evaluate import evaluate_model
from pipeline.field_tests import import_field_test_bundles, balance_summary
from pipeline.recommendations import generate_collection_plan
from pipeline.registry import latest_registry_entry, register_model
from pipeline.splits import create_or_update_split_manifest, load_split_manifest
from pipeline.paths import repository_root, workspace_root


def get_repo_root() -> Path:
    return repository_root()


def get_workspace_root() -> Path:
    return workspace_root()


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments to a parser."""
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Path to config YAML file (default: config/defaults.yaml)"
    )
    parser.add_argument(
        "--no-wandb", action="store_true",
        help="Disable Weights & Biases logging"
    )
    
    # Training overrides
    parser.add_argument("--batch-size", type=int, help="Training batch size")
    parser.add_argument("--lr", type=float, help="Learning rate")
    parser.add_argument("--epochs", type=int, help="Max training epochs")
    parser.add_argument("--hidden-dim", type=int, help="Hidden layer dimension")
    parser.add_argument("--patience", type=int, help="Early stopping patience")
    parser.add_argument("--loss", choices=["bce", "focal"], help="Training loss")
    parser.add_argument("--pos-weight", help='pos_weight: "auto", "none", or a float')
    parser.add_argument("--no-weighted-sampler", action="store_true",
                        help="Disable WeightedRandomSampler (default: on)")
    parser.add_argument(
        "--arch",
        choices=["causal_tcn", "mlp"],
        help="Model architecture (mlp is legacy eating-only compatibility)",
    )
    parser.add_argument("--allow-unsplit", action="store_true",
                        help="Allow training without a locked split manifest")
    parser.add_argument("--feature-noise-std", type=float,
                        help="Gaussian noise std added to feature vectors at train time")
    
    # Feature overrides
    parser.add_argument("--window-sec", type=float, help="Feature window duration")
    parser.add_argument("--step-sec", type=float, help="Feature window step")
    
    # Runtime overrides (logged with model)
    parser.add_argument("--alpha", type=float, help="EMA smoothing factor")
    parser.add_argument("--high-threshold", type=float, help="Chewing start threshold")
    parser.add_argument("--low-threshold", type=float, help="Chewing end threshold")
    parser.add_argument("--min-start-windows", type=int, help="Windows to confirm chewing start")
    parser.add_argument("--min-end-windows", type=int, help="Windows to confirm chewing end")


def load_and_merge_config(args: argparse.Namespace) -> PipelineConfig:
    """Load config and merge CLI overrides."""
    config = load_config(args.config)
    
    if args.no_wandb:
        config.wandb.enabled = False
    
    # Convert args to dict with underscores
    args_dict = {
        "batch_size": getattr(args, "batch_size", None),
        "lr": args.lr if hasattr(args, "lr") else None,
        "epochs": args.epochs if hasattr(args, "epochs") else None,
        "hidden_dim": getattr(args, "hidden_dim", None),
        "patience": args.patience if hasattr(args, "patience") else None,
        "window_sec": getattr(args, "window_sec", None),
        "step_sec": getattr(args, "step_sec", None),
        "alpha": args.alpha if hasattr(args, "alpha") else None,
        "high_threshold": getattr(args, "high_threshold", None),
        "low_threshold": getattr(args, "low_threshold", None),
        "min_start_windows": getattr(args, "min_start_windows", None),
        "min_end_windows": getattr(args, "min_end_windows", None),
        "loss": getattr(args, "loss", None),
        "pos_weight": getattr(args, "pos_weight", None),
        "use_weighted_sampler": (False if getattr(args, "no_weighted_sampler", False) else None),
        "arch": getattr(args, "arch", None),
        "allow_unsplit": (True if getattr(args, "allow_unsplit", False) else None),
        "feature_noise_std": getattr(args, "feature_noise_std", None),
    }
    
    return merge_cli_args(config, args_dict)


def prompt_yes_no(prompt: str, default: bool = True) -> bool:
    """Prompt user for yes/no confirmation."""
    if not sys.stdin.isatty():
        return default
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        ans = input(prompt + suffix).strip().lower()
        if ans == "":
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Please enter 'y' or 'n'.")


def default_split_manifest_path(pipeline_root: Path) -> Path:
    return pipeline_root / "manifests" / "splits" / "locked_splits.json"


def default_temporal_split_manifest_path(pipeline_root: Path) -> Path:
    return pipeline_root / "manifests" / "splits" / "temporal_v2_locked_splits.json"


def dataset_manifest_path(pipeline_root: Path, label: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_label = label.replace("/", "-").replace(" ", "-")
    return pipeline_root / "manifests" / "datasets" / f"{safe_label}_{stamp}.json"


def make_run_workspace(pipeline_root: Path, label: str) -> tuple[Path, Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_label = label.replace("/", "-").replace(" ", "-")
    run_dir = pipeline_root / "derived" / "motion" / "runs" / f"{safe_label}_{stamp}"
    transformed_dir = run_dir / "transformed"
    features_dir = run_dir / "features"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, transformed_dir, features_dir


def latest_features_dir(pipeline_root: Path) -> Path:
    runs_dir = pipeline_root / "derived" / "motion" / "runs"
    candidates = [
        path for path in runs_dir.glob("*/features")
        if (path / "X.npy").exists() and (path / "y.npy").exists()
    ]
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime)

    legacy_dir = pipeline_root / "derived" / "motion" / "features"
    if (legacy_dir / "X.npy").exists() and (legacy_dir / "y.npy").exists():
        return legacy_dir
    return legacy_dir


def save_metrics(metrics: dict, pipeline_root: Path, label: str) -> Path:
    out = pipeline_root / "reports" / "motion" / f"{label}_metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(metrics, f, indent=2)
    return out


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def verify_registry_evaluation_inputs(
    registry_entry: Path,
    features_dir: Path,
    split_manifest_path: Path,
) -> None:
    """Prove an evaluation uses the exact registered temporal dataset lineage."""

    registry_entry = Path(registry_entry)
    features_dir = Path(features_dir)
    registered_dataset = _load_json_object(registry_entry / "dataset_manifest.json")
    feature_dataset = _load_json_object(features_dir / "dataset_manifest.json")
    registered_split = _load_json_object(registry_entry / "split_manifest.json")
    selected_split = _load_json_object(split_manifest_path)
    metadata = _load_json_object(registry_entry / "metadata.json")
    if registered_dataset != feature_dataset:
        raise ValueError("feature dataset manifest differs from the registered manifest")
    hashes = {
        registered_dataset.get("dataset_hash"),
        feature_dataset.get("dataset_hash"),
        registered_split.get("dataset_hash"),
        selected_split.get("dataset_hash"),
    }
    if None in hashes or len(hashes) != 1:
        raise ValueError("registry, feature, and split dataset hashes do not match exactly")
    if registered_split.get("version") != 2 or selected_split.get("version") != 2:
        raise ValueError("registry evaluation requires a version-2 locked split manifest")
    if registered_split.get("splits") != selected_split.get("splits"):
        raise ValueError("selected split assignments differ from the registered locked splits")

    import torch

    checkpoint_path = registry_entry / "chewnet.pth"
    try:
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    except Exception as exc:
        raise ValueError(f"cannot read registered checkpoint {checkpoint_path}: {exc}") from exc
    dataset_hash = registered_dataset.get("dataset_hash")
    if (
        checkpoint.get("mode") != "temporal_tcn"
        or checkpoint.get("temporal_contract_version") != "2.0.0"
        or checkpoint.get("dataset_hash") != dataset_hash
        or metadata.get("dataset_hash") != dataset_hash
    ):
        raise ValueError("checkpoint/metadata do not match the registered temporal dataset")

    arrays = feature_dataset.get("arrays")
    if not isinstance(arrays, dict) or not arrays:
        raise ValueError("temporal dataset manifest does not record array hashes")
    for name, record in arrays.items():
        path = features_dir / str(name)
        expected = record.get("sha256") if isinstance(record, dict) else None
        if not path.is_file() or not expected or _file_sha256(path) != expected:
            raise ValueError(f"temporal dataset array hash mismatch: {path}")


def record_registry_evaluation(
    registry_entry: Path,
    split_name: str,
    metrics: dict,
) -> Path:
    """Atomically attach one evaluated split to an exact registry entry."""

    output = Path(registry_entry) / "metrics.json"
    current = _load_json_object(output) if output.exists() else {}
    current[split_name] = metrics
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    temporary.replace(output)
    return output


def _require_legacy_mlp(config: PipelineConfig, command: str) -> bool:
    if config.training.arch == "mlp":
        return True
    print(
        f"\n❌ '{command}' is a legacy aggregate-feature eating-only workflow.\n"
        "   Use 'from-raw --arch causal_tcn' with canonical schema-v2 sessions,\n"
        "   or pass '--arch mlp' explicitly only to reproduce a historical binary model."
    )
    return False


def lazy_deploy_to_app(*args, **kwargs):
    from pipeline.deploy import deploy_to_app

    return deploy_to_app(*args, **kwargs)


# Heavy artifacts inside a field-test bundle that we don't need for training.
# Removed during archive when --strip-archive-mov is set.
_HEAVY_BUNDLE_GLOBS = ("*.mov", "*.mp4", "*.MOV", "*.MP4")


def _archive_imported_bundles(
    import_manifest: dict,
    input_dir: Path,
    strip_heavy: bool = False,
) -> int:
    """Move successfully-imported bundles into <input_dir>/_archive/<stamp>/.

    Prevents the next iteration from re-importing the same bundles (which
    would create duplicate sessions in curated/) and keeps the active
    field_tests/ directory limited to NEW bundles. The labeled CSVs inside
    each bundle are preserved in the archive (lossless), and the curated
    session folders that feed training are unaffected.

    If `strip_heavy` is True, large media files (.mov/.mp4) inside the
    archived bundle are deleted; CSV/JSON labels remain.
    """
    bundles = {
        Path(item.get("source_bundle", "")).resolve()
        for item in import_manifest.get("imported_sessions", [])
        if item.get("source_bundle")
    }
    bundles = {b for b in bundles if b.exists() and b != input_dir.resolve()}
    if not bundles:
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_root = input_dir / "_archive" / stamp
    archive_root.mkdir(parents=True, exist_ok=True)

    moved = 0
    for bundle in sorted(bundles):
        # Don't archive things already inside _archive.
        if any(part == "_archive" for part in bundle.parts):
            continue
        target = archive_root / bundle.name
        try:
            bundle.rename(target)
        except OSError:
            # Cross-device or rename failed; fall back to copy + remove.
            import shutil as _shutil
            _shutil.copytree(bundle, target)
            _shutil.rmtree(bundle)
        moved += 1
        if strip_heavy:
            removed_bytes = 0
            for pattern in _HEAVY_BUNDLE_GLOBS:
                for heavy in target.rglob(pattern):
                    try:
                        removed_bytes += heavy.stat().st_size
                        heavy.unlink()
                    except OSError:
                        pass
            if removed_bytes:
                print(f"   🧹 stripped {removed_bytes/1e6:.1f} MB of media from {target.name}")

    print(f"   📦 archived {moved} bundle(s) → {archive_root}")
    return moved


def _prune_directory(
    root: Path,
    keep: int,
    *,
    label: str,
    dry_run: bool = False,
) -> int:
    """Keep the `keep` most-recently-modified subdirectories of `root`; delete the rest."""
    if not root.exists() or keep < 0:
        return 0
    subdirs = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")]
    subdirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    victims = subdirs[keep:]
    if not victims:
        print(f"   {label}: kept {len(subdirs)} (≤ {keep}); nothing to prune.")
        return 0
    import shutil as _shutil
    total_bytes = 0
    for victim in victims:
        try:
            for f in victim.rglob("*"):
                if f.is_file():
                    total_bytes += f.stat().st_size
        except OSError:
            pass
        if dry_run:
            print(f"   {label}: would delete {victim.name}")
        else:
            _shutil.rmtree(victim, ignore_errors=True)
            print(f"   {label}: deleted {victim.name}")
    verb = "would free" if dry_run else "freed"
    print(f"   {label}: {verb} ~{total_bytes/1e6:.1f} MB across {len(victims)} dir(s).")
    return len(victims)


def lazy_export_coreml(*args, **kwargs):
    from pipeline.export import export_coreml

    return export_coreml(*args, **kwargs)


def lazy_export_normalization_json(*args, **kwargs):
    from pipeline.export import export_normalization_json

    return export_normalization_json(*args, **kwargs)


def lazy_generate_swift_constants(*args, **kwargs):
    from pipeline.export import generate_swift_constants

    return generate_swift_constants(*args, **kwargs)


def evaluate_with_locked_validation(
    features_dir: Path,
    model_path: Path,
    config: PipelineConfig,
    split_path: Path,
    curated_dir: Path | None = None,
) -> dict:
    try:
        return evaluate_model(
            features_dir,
            model_path,
            config,
            split_manifest_path=split_path,
            split_name="validation_locked",
            curated_dir=curated_dir,
        )
    except ValueError as exc:
        print(f"Validation split unavailable ({exc}); evaluating all rows instead.")
        return evaluate_model(features_dir, model_path, config, curated_dir=curated_dir)


# =============================================================================
# COMMAND: sample
# =============================================================================

def cmd_sample(args: argparse.Namespace) -> int:
    """Run pipeline with sample data (for testing/demo)."""
    print("\n🧪 Running pipeline with SAMPLE data")
    print("=" * 50)
    
    config = load_and_merge_config(args)
    if not _require_legacy_mlp(config, "sample"):
        return 2
    repo_root = get_repo_root()
    pipeline_root = get_workspace_root()
    
    # Paths for sample workflow
    sample_dir = repo_root / "fixtures" / "sessions"
    run_dir, transformed_dir, features_dir = make_run_workspace(pipeline_root, "sample")
    model_path = pipeline_root / "models" / "exports" / "checkpoints" / "chewnet_sample.pth"
    print(f"   Run workspace: {run_dir}")
    
    # Check sample data exists
    if not sample_dir.exists() or not list(sample_dir.glob("*/*.csv")):
        print(f"\n❌ No sample data found in: {sample_dir}")
        print("   Sample data should be pre-populated in the repository.")
        return 1
    
    # 1. Validate
    print("\n📋 Step 1: Validating sample data...")
    sessions = validate_sessions(sample_dir)
    manifest = create_manifest(sessions)
    print(f"   Found {len(sessions)} sessions")
    print(f"   Dataset hash: {manifest.dataset_hash}")
    manifest_path = dataset_manifest_path(pipeline_root, "sample")
    save_manifest(manifest, manifest_path)
    split_path = default_split_manifest_path(pipeline_root)
    split_manifest = create_or_update_split_manifest(sessions, split_path)
    
    # 2. Transform
    print("\n🔄 Step 2: Transforming (adding soft labels)...")
    transform_directory(
        input_dir=sample_dir,
        output_dir=transformed_dir,
        config=config.soft_labels,
        postfix="_transformed",
    )
    
    # 3. Extract features
    print("\n📊 Step 3: Extracting features...")
    extract_features_directory(
        input_dir=transformed_dir,
        output_dir=features_dir,
        config=config.features,
    )
    
    # 4. Train
    if prompt_yes_no("\n🎯 Train model?"):
        result = train_model(
            features_dir=features_dir,
            output_path=model_path,
            config=config,
            dataset_hash=manifest.dataset_hash,
            data_source="sample",
            split_manifest_path=split_path,
        )
        
        # 5. Evaluate
        metrics = None
        if prompt_yes_no("\n📈 Evaluate model?"):
            metrics = evaluate_with_locked_validation(features_dir, model_path, config, split_path)
            save_metrics(metrics, pipeline_root, "sample")

        registry_dir = register_model(
            model_path=model_path,
            repo_root=repo_root,
            config=config,
            dataset_manifest=manifest_path,
            split_manifest=split_manifest,
            metrics=metrics,
            model_id=result.get("model_id"),
        )
        print(f"   Registry entry: {registry_dir}")
        
        # 6. Export
        if prompt_yes_no("\n📦 Export to CoreML?", default=False):
            exports_dir = pipeline_root / "models" / "exports"
            lazy_export_coreml(model_path, exports_dir / "ChewNet.mlpackage")
            lazy_export_normalization_json(model_path, exports_dir / "chewnet_norm.json")
            lazy_generate_swift_constants(
                model_path,
                exports_dir / "NormalizationConstants.swift",
                model_id=result.get("model_id"),
            )
    
    print("\n✅ Sample pipeline complete!")
    return 0


# =============================================================================
# COMMAND: from-app
# =============================================================================

def cmd_from_app(args: argparse.Namespace) -> int:
    """Guide user through collecting and processing their own data."""
    print("\n📱 Collecting Your Own Data")
    print("=" * 50)
    
    print("""
This workflow guides you through:
1. Recording sessions with the ChewSense Data Collection app
2. Exporting and placing data in the correct location
3. Running the training pipeline

STEP 1: Install the App
-----------------------
Download from the App Store:
https://apps.apple.com/us/app/chew-sense-collect-and-label/id6755277802

STEP 2: Required Hardware
-------------------------
You MUST use AirPods with motion sensors:
  ✅ AirPods Pro (1st or 2nd generation)
  ✅ AirPods Max
  ✅ AirPods 4 (with Active Noise Cancellation)
  ❌ AirPods 3 (no motion sensors)
  ❌ AirPods 2 or earlier

STEP 3: Record Sessions
-----------------------
Record a mix of eating and not-eating sessions:
  • Aim for at least 5-10 minutes of each
  • Label sessions accurately during recording
  • Include variety: different foods, activities, head movements

STEP 4: Export Sessions
-----------------------
Use the app's export feature to share session folders.
Each session folder contains:
  • <SessionName>.csv    (motion data + labels)
  • <SessionName>.mov    (video, optional)
  • _metadata.txt

STEP 5: Place Data
------------------
Copy exported session folders to:
""")
    
    pipeline_root = get_workspace_root()
    user_data_dir = pipeline_root / "sessions" / "raw"
    print(f"  {user_data_dir}")
    
    print("""
STEP 6: Run Pipeline
--------------------
Once data is in place, run:

    chewsense-motion from-raw

This will validate, transform, extract features, and train a model.
""")
    
    if prompt_yes_no("Have you placed data in the user directory?", default=False):
        print("\nGreat! Running from-raw workflow...")
        args.input = user_data_dir
        return cmd_from_raw(args)
    else:
        print("\nOK! Come back when you have data ready.")
        return 0


# =============================================================================
# COMMAND: from-raw
# =============================================================================

def cmd_from_raw(args: argparse.Namespace) -> int:
    """Process raw labeled CSVs through full pipeline."""
    print("\n📁 Processing Raw Labeled Data")
    print("=" * 50)
    
    config = load_and_merge_config(args)
    pipeline_root = get_workspace_root()
    
    input_dir = Path(args.input) if args.input else pipeline_root / "sessions" / "raw"
    
    if not input_dir.exists():
        print(f"\n❌ Input directory not found: {input_dir}")
        return 1

    if config.training.arch == "causal_tcn":
        return _cmd_from_raw_temporal(args, config, input_dir)
    
    run_dir, transformed_dir, features_dir = make_run_workspace(pipeline_root, "from_raw")
    model_path = pipeline_root / "models" / "exports" / "checkpoints" / "chewnet.pth"
    print(f"   Run workspace: {run_dir}")
    
    # 1. Validate
    print(f"\n📋 Step 1: Validating data in {input_dir}...")
    try:
        sessions = validate_sessions(input_dir)
    except Exception as e:
        print(f"❌ Validation failed: {e}")
        return 1
    
    manifest = create_manifest(sessions)
    print(f"   Found {len(sessions)} sessions:")
    print(f"   - Eating: {manifest.eating_count}")
    print(f"   - Not-eating: {manifest.not_eating_count}")
    print(f"   - Total duration: {manifest.total_duration_sec:.1f}s")
    print(f"   - Dataset hash: {manifest.dataset_hash}")
    
    # Save manifest
    manifest_path = dataset_manifest_path(pipeline_root, "from_raw")
    save_manifest(manifest, manifest_path)
    split_path = default_split_manifest_path(pipeline_root)
    split_manifest = create_or_update_split_manifest(sessions, split_path)
    
    # 2. Transform
    print("\n🔄 Step 2: Transforming (adding soft labels)...")
    transform_directory(
        input_dir=input_dir,
        output_dir=transformed_dir,
        config=config.soft_labels,
    )
    
    # 3. Extract features
    print("\n📊 Step 3: Extracting features...")
    extract_features_directory(
        input_dir=transformed_dir,
        output_dir=features_dir,
        config=config.features,
    )
    
    # 4. Train
    if prompt_yes_no("\n🎯 Train model?"):
        result = train_model(
            features_dir=features_dir,
            output_path=model_path,
            config=config,
            dataset_hash=manifest.dataset_hash,
            data_source="user",
            split_manifest_path=split_path,
        )
        
        metrics = None
        if prompt_yes_no("\n📈 Evaluate model?"):
            metrics = evaluate_with_locked_validation(features_dir, model_path, config, split_path)
            save_metrics(metrics, pipeline_root, "from_raw")

        registry_dir = register_model(
            model_path=model_path,
            repo_root=get_repo_root(),
            config=config,
            dataset_manifest=manifest_path,
            split_manifest=split_manifest,
            metrics=metrics,
            model_id=result.get("model_id"),
        )
        print(f"   Registry entry: {registry_dir}")
        
        if prompt_yes_no("\n📦 Export and deploy to Xcode?", default=False):
            lazy_deploy_to_app(
                model_path=model_path,
                repo_root=get_repo_root(),
            )
    
    print("\n✅ Pipeline complete!")
    return 0


def _cmd_from_raw_temporal(
    args: argparse.Namespace,
    config: PipelineConfig,
    input_dir: Path,
) -> int:
    """Materialize and optionally train the schema-v2 causal TCN path."""

    from pipeline.temporal_dataset import (
        TemporalDatasetError,
        create_locked_temporal_splits,
        discover_v2_sessions,
        materialize_temporal_dataset,
    )

    pipeline_root = get_workspace_root()
    run_dir, _, features_dir = make_run_workspace(pipeline_root, "from_raw_temporal")
    model_path = pipeline_root / "models" / "exports" / "checkpoints" / "chewnet.pth"
    print(f"   Temporal run workspace: {run_dir}")
    try:
        session_dirs = discover_v2_sessions(input_dir)
        result = materialize_temporal_dataset(session_dirs, features_dir)
    except (OSError, TemporalDatasetError) as exc:
        print(f"❌ Temporal materialization failed: {exc}")
        return 1

    reviewed = sum(item.event_supervision for item in result.sessions)
    coarse_only = len(result.sessions) - reviewed
    print(f"   Sessions: {len(result.sessions)} ({reviewed} reviewed, {coarse_only} eating-only)")
    print(f"   Windows: {result.rows}")
    print(f"   Dataset hash: {result.dataset_hash}")
    print(f"   Dataset manifest: {result.manifest_path}")
    if getattr(args, "materialize_only", False):
        return 0

    split_path = default_temporal_split_manifest_path(pipeline_root)
    try:
        split_manifest = create_locked_temporal_splits(result.manifest_path, split_path)
    except TemporalDatasetError as exc:
        if not config.training.allow_unsplit:
            print(f"❌ Locked split creation failed: {exc}")
            print("   Collect class-covered sessions or use --allow-unsplit for development only.")
            return 2
        split_manifest = None
        split_path = None
        print(f"   Development split override: {exc}")

    if not prompt_yes_no("\n🎯 Train temporal chew model?"):
        return 0
    train_result = train_model(
        features_dir=features_dir,
        output_path=model_path,
        config=config,
        dataset_hash=result.dataset_hash,
        data_source="canonical_v2",
        split_manifest_path=split_path,
    )
    metrics = None
    if prompt_yes_no("\n📈 Evaluate model?"):
        metrics = evaluate_model(
            features_dir,
            model_path,
            config,
            split_manifest_path=split_path,
            split_name="validation_locked" if split_path else "all",
        )
        save_metrics(metrics, pipeline_root, "from_raw_temporal")
    registry_dir = register_model(
        model_path=model_path,
        repo_root=get_repo_root(),
        config=config,
        dataset_manifest=result.manifest_path,
        split_manifest=split_manifest,
        metrics={"validation_locked": metrics} if metrics else None,
        model_id=train_result.get("model_id"),
    )
    print(f"   Registry entry: {registry_dir}")
    return 0


def cmd_materialize_temporal(args: argparse.Namespace) -> int:
    """Materialize exact TCN windows without training or mutating sessions."""

    from pipeline.temporal_dataset import (
        TemporalDatasetError,
        create_locked_temporal_splits,
        discover_v2_sessions,
        materialize_temporal_dataset,
    )

    input_root = Path(args.input) if args.input else get_workspace_root() / "sessions" / "raw"
    output_dir = (
        Path(args.output)
        if args.output
        else make_run_workspace(get_workspace_root(), "materialize_temporal")[2]
    )
    try:
        sessions = discover_v2_sessions(input_root)
        result = materialize_temporal_dataset(
            sessions,
            output_dir,
            inference_step_seconds=args.inference_step_sec,
            max_gap_seconds=args.max_gap_sec,
            skip_invalid=args.skip_invalid,
        )
        split_path = None
        if args.split_manifest:
            split_path = Path(args.split_manifest)
            create_locked_temporal_splits(result.manifest_path, split_path)
    except (OSError, TemporalDatasetError) as exc:
        print(f"❌ Temporal materialization failed: {exc}")
        return 1
    print(f"Materialized {result.rows} windows from {len(result.sessions)} sessions")
    rejected = json.loads(result.manifest_path.read_text()).get("rejected_sessions", [])
    for item in rejected:
        print(f"   excluded {item['session_dir']}: {item['reason']}")
    print(f"Dataset: {result.output_dir}")
    print(f"Dataset SHA-256: {result.dataset_hash}")
    if split_path is not None:
        print(f"Locked splits: {split_path}")
    return 0


# =============================================================================
# COMMAND: from-features
# =============================================================================

def cmd_from_features(args: argparse.Namespace) -> int:
    """Train from pre-extracted features (skip transform/extraction)."""
    print("\n📊 Training from Pre-Extracted Features")
    print("=" * 50)
    
    config = load_and_merge_config(args)
    if not _require_legacy_mlp(config, "from-features"):
        return 2
    pipeline_root = get_workspace_root()
    
    features_dir = Path(args.input) if args.input else latest_features_dir(pipeline_root)
    model_path = pipeline_root / "models" / "exports" / "checkpoints" / "chewnet.pth"
    
    # Check features exist
    x_path = features_dir / "X.npy"
    y_path = features_dir / "y.npy"
    
    if not x_path.exists() or not y_path.exists():
        print(f"\n❌ Feature files not found in: {features_dir}")
        print("   Expected: X.npy and y.npy")
        print("\n   Run 'chewsense-motion from-raw' to extract features first.")
        return 1
    
    print(f"\n📂 Features directory: {features_dir}")

    # Train
    split_path = default_split_manifest_path(pipeline_root)
    result = train_model(
        features_dir=features_dir,
        output_path=model_path,
        config=config,
        data_source="user",
        split_manifest_path=split_path if split_path.exists() else None,
    )
    
    metrics = None
    if prompt_yes_no("\n📈 Evaluate model?"):
        metrics = evaluate_model(features_dir, model_path, config)
        save_metrics(metrics, pipeline_root, "from_features")

    registry_dir = register_model(
        model_path=model_path,
        repo_root=get_repo_root(),
        config=config,
        metrics=metrics,
        model_id=result.get("model_id"),
    )
    print(f"   Registry entry: {registry_dir}")
    
    if prompt_yes_no("\n📦 Export and deploy to Xcode?", default=False):
        lazy_deploy_to_app(
            model_path=model_path,
            repo_root=get_repo_root(),
        )
    
    print("\n✅ Training complete!")
    return 0


# =============================================================================
# COMMAND: deploy
# =============================================================================

def cmd_deploy(args: argparse.Namespace) -> int:
    """Deploy trained model to Xcode project."""
    print("\n🚀 Deploying Model to Xcode Project")
    print("=" * 50)
    
    pipeline_root = get_workspace_root()
    model_path = Path(args.model) if args.model else pipeline_root / "models" / "exports" / "checkpoints" / "chewnet.pth"
    
    if not model_path.exists():
        print(f"\n❌ Model not found: {model_path}")
        print("   Train a model first with 'chewsense-motion sample' or 'chewsense-motion from-raw'")
        return 1

    from pipeline.promotion import (
        assess_registry_entry,
        registry_entry_for_model,
        write_promotion_report,
    )

    registry_entry = registry_entry_for_model(get_repo_root(), model_path)
    if registry_entry is None:
        print(
            "\nRefusing to deploy: this checkpoint is not present in the versioned "
            "model registry."
        )
        return 2
    promotion = assess_registry_entry(registry_entry)
    if not promotion.passed:
        if not getattr(args, "development_override", False):
            print("\nRefusing to deploy: the model has not passed the Live promotion gate.")
            for reason in promotion.reasons:
                print(f"   - {reason}")
            print("   Use --development-override only for a non-production device build.")
            write_promotion_report(registry_entry, promotion)
            return 2
        promotion.development_override = True
        print("\nDevelopment override: deploying an unpromoted model.")
    report_path = write_promotion_report(registry_entry, promotion)
    print(f"   Promotion report: {report_path}")

    # Optional pre-deploy gate: refuse to deploy a regressed model unless --force.
    if not getattr(args, "skip_compare", False):
        verdict = _run_comparator_gate(force=args.force)
        if verdict == "regressed" and not args.force:
            print(
                "\n❌ Refusing to deploy: comparator verdict is `regressed`.\n"
                "   Re-run with `--force` to override, or train a better candidate."
            )
            return 2

    result = lazy_deploy_to_app(
        model_path=model_path,
        repo_root=get_repo_root(),
        force=args.force,
        skip_coreml=getattr(args, "skip_coreml", False),
    )

    if result.get("sha_unchanged"):
        # Surface this loudly: the user's app will look identical to before.
        return 3
    return 0


def _run_comparator_gate(force: bool) -> str | None:
    """Run the model comparator and return its verdict."""
    repo_root = get_repo_root()
    compare_path = repo_root / "tools" / "evaluation" / "compare.py"
    if not compare_path.exists():
        return None
    sys.path.insert(0, str(compare_path.parent))
    try:
        import compare  # type: ignore
    except Exception as exc:
        print(f"   ⚠️  Could not load comparator ({exc}); skipping gate.")
        return None
    finally:
        if str(compare_path.parent) in sys.path:
            sys.path.remove(str(compare_path.parent))

    report = compare.compute_comparison()
    if "error" in report:
        print(f"   ℹ️  Comparator: {report['error']}")
        return None
    print(f"   ℹ️  Comparator verdict: {report['verdict']}"
          f"  (baseline={report['baseline']}, candidate={report['candidate']})")
    try:
        out = compare.write_report(report)
        print(f"      ↪ report: {out}")
    except OSError as exc:
        print(f"      ⚠️  could not write report: {exc}")
    return report.get("verdict")


# =============================================================================
# COMMAND: compare
# =============================================================================

def cmd_compare(args: argparse.Namespace) -> int:
    """Compare the two newest registry entries and emit a markdown report."""
    repo_root = get_repo_root()
    compare_path = repo_root / "tools" / "evaluation" / "compare.py"
    if not compare_path.exists():
        print(f"❌ Comparator not found at {compare_path}")
        return 1
    sys.path.insert(0, str(compare_path.parent))
    try:
        import compare  # type: ignore
    finally:
        if str(compare_path.parent) in sys.path:
            sys.path.remove(str(compare_path.parent))
    return compare.main(
        [
            *(["--baseline", args.baseline] if args.baseline else []),
            *(["--candidate", args.candidate] if args.candidate else []),
            *(["--json"] if args.json else []),
        ]
    )


# =============================================================================
# COMMAND: clean
# =============================================================================

def cmd_clean(args: argparse.Namespace) -> int:
    """Prune accumulating artifacts so the iteration loop doesn't blow up disk.

    Pruned (run with `--dry-run` first to preview):

    - data/derived/runs/<label>_<stamp>/   (per-iteration features + transformed CSVs)
    - model_registry/runs/<model_id>/      (per-train registry entries)
    - wandb/run-<stamp>-<id>/              (W&B local run dirs)
    - data/user/field_tests/_archive/<stamp>/ (already-imported bundles)

    Curated training data (data/curated/accepted_sessions/), split manifests,
    the active checkpoint (models/chewnet.pth), the deployed Xcode artifacts,
    and any non-archived field-test bundles are NEVER touched. So you can
    re-train at any time using everything the loop has accumulated.
    """
    pipeline_root = get_workspace_root()
    print("\n🧹 Pruning iteration artifacts")
    print("=" * 50)
    print(f"   keep-runs={args.keep_runs}  keep-registry={args.keep_registry}"
          f"  keep-wandb={args.keep_wandb}  keep-archive={args.keep_archive}"
          f"  dry-run={args.dry_run}")

    _prune_directory(
        pipeline_root / "derived" / "motion" / "runs",
        args.keep_runs,
        label="derived/runs",
        dry_run=args.dry_run,
    )
    _prune_directory(
        pipeline_root / "models" / "registry",
        args.keep_registry,
        label="model_registry/runs",
        dry_run=args.dry_run,
    )
    _prune_directory(
        pipeline_root / "reports" / "wandb",
        args.keep_wandb,
        label="wandb",
        dry_run=args.dry_run,
    )
    _prune_directory(
        pipeline_root / "sessions" / "field-tests" / "_archive",
        args.keep_archive,
        label="field_tests/_archive",
        dry_run=args.dry_run,
    )

    if args.strip_archive_mov:
        archive_root = pipeline_root / "sessions" / "field-tests" / "_archive"
        if archive_root.exists():
            removed_bytes = 0
            for pattern in _HEAVY_BUNDLE_GLOBS:
                for heavy in archive_root.rglob(pattern):
                    try:
                        size = heavy.stat().st_size
                        if not args.dry_run:
                            heavy.unlink()
                        removed_bytes += size
                    except OSError:
                        pass
            verb = "would free" if args.dry_run else "freed"
            print(f"   archive media: {verb} ~{removed_bytes/1e6:.1f} MB of .mov/.mp4")

    print("\n✅ Done.")
    return 0


# =============================================================================
# COMMAND: from-field-test
# =============================================================================

def cmd_from_field_test(args: argparse.Namespace) -> int:
    """Import real-world app field-test bundles and optionally retrain."""
    print("\n🧭 Importing Field-Test Bundles")
    print("=" * 50)

    config = load_and_merge_config(args)
    if not _require_legacy_mlp(config, "from-field-test"):
        return 2
    pipeline_root = get_workspace_root()
    repo_root = get_repo_root()

    input_dir = Path(args.input) if args.input else pipeline_root / "sessions" / "field-tests"
    curated_dir = Path(args.curated_dir) if args.curated_dir else pipeline_root / "sessions" / "curated"
    manifest_dir = pipeline_root / "manifests" / "datasets"

    if not input_dir.exists():
        print(f"\n❌ Input directory not found: {input_dir}")
        print("   Export field-test bundles from the app and place them here first.")
        return 1

    import_manifest = import_field_test_bundles(
        input_dir=input_dir,
        output_dir=curated_dir,
        manifest_dir=manifest_dir,
        activity=getattr(args, "activity", None),
    )
    print(f"   Bundles found: {import_manifest['bundles_found']}")
    print(f"   Imported sessions: {import_manifest['imported_count']}")
    print(f"   Import manifest: {import_manifest['manifest_path']}")
    if import_manifest.get("activity"):
        print(f"   Activity tag: {import_manifest['activity']}")

    # Surface gold-lock candidates the user flagged inside the recording app.
    # We DO NOT auto-promote these into gold_locked — that remains an explicit
    # operator decision per `pipeline/splits.py::gold_locked_assignment`. We
    # only print the exact `--gold-locked` invocation needed to lock them.
    gold_candidates = [
        item["session_id"]
        for item in import_manifest.get("imported_sessions", [])
        if item.get("gold_lock_candidate")
    ]
    if gold_candidates:
        print("\n🔒 Gold-lock candidates flagged in app:")
        for sid in gold_candidates:
            print(f"     - {sid}")
        flags = " ".join(f"--gold-locked {sid}" for sid in gold_candidates)
        print("\n   To promote them into the locked validation set, rerun with:")
        print(f"     chewsense-motion from-field-test {flags}")

    if import_manifest["imported_count"] == 0:
        print("\nNo usable field-test windows were imported.")
        return 0

    # Archive every successfully-imported bundle so the next iteration only
    # picks up NEW recordings. Curated/accepted_sessions still contains the
    # labeled CSVs from this and every previous iteration, so retraining keeps
    # using all of the data the user has ever corrected (or recorded).
    if not getattr(args, "no_archive", False):
        _archive_imported_bundles(
            import_manifest,
            input_dir,
            strip_heavy=getattr(args, "strip_archive_mov", False),
        )

    if not prompt_yes_no("\n🎯 Train using curated accepted sessions?", default=True):
        return 0

    run_dir, transformed_dir, features_dir = make_run_workspace(pipeline_root, "from_field_test")
    model_path = pipeline_root / "models" / "exports" / "checkpoints" / "chewnet.pth"
    print(f"   Run workspace: {run_dir}")

    print(f"\n📋 Validating curated sessions in {curated_dir}...")
    sessions = validate_sessions(curated_dir)
    manifest = create_manifest(sessions)
    manifest_path = dataset_manifest_path(pipeline_root, "from_field_test")
    save_manifest(manifest, manifest_path)

    # ---- Dataset balance gate -------------------------------------------------
    summary = balance_summary(curated_dir)
    print("\n📊 Curated dataset balance:")
    print(f"   sessions={summary['sessions']}  rows pos={summary['positive_rows']} "
          f"neg={summary['negative_rows']}  pos_pct={summary['positive_pct']:.1f}%")
    for activity, counts in sorted(summary["per_activity"].items()):
        print(f"   - {activity}: sessions={counts['sessions']} pos={counts['pos']} neg={counts['neg']}")
    pos_pct = summary["positive_pct"]
    if pos_pct < 10.0 and not getattr(args, "force_balance", False):
        print(
            "\n❌ Aborting: positive rate < 10%. Class collapse is very likely.\n"
            "   Add more chewing (positive) sessions, or rerun with --force-balance to override."
        )
        return 2
    if pos_pct > 90.0 and not getattr(args, "force_balance", False):
        print(
            "\n❌ Aborting: positive rate > 90%. Model will not learn negatives.\n"
            "   Add hard-negative sessions (head-shake, walking, talking, drinking),\n"
            "   or rerun with --force-balance to override."
        )
        return 2

    field_regression_ids = [
        item["session_id"] for item in import_manifest.get("imported_sessions", [])
    ]
    gold_ids = list(getattr(args, "gold_locked", None) or [])
    split_path = default_split_manifest_path(pipeline_root)
    split_manifest = create_or_update_split_manifest(
        sessions,
        split_path,
        field_regression_session_ids=field_regression_ids,
        gold_session_ids=gold_ids or None,
    )

    print("\n🔄 Transforming curated sessions...")
    transform_directory(
        input_dir=curated_dir,
        output_dir=transformed_dir,
        config=config.soft_labels,
    )

    print("\n📊 Extracting features...")
    extract_features_directory(
        input_dir=transformed_dir,
        output_dir=features_dir,
        config=config.features,
    )

    result = train_model(
        features_dir=features_dir,
        output_path=model_path,
        config=config,
        dataset_hash=manifest.dataset_hash,
        data_source="field_test",
        split_manifest_path=split_path,
    )

    metrics = evaluate_with_locked_validation(features_dir, model_path, config, split_path, curated_dir=curated_dir)
    field_metrics = None
    try:
        field_metrics = evaluate_model(
            features_dir,
            model_path,
            config,
            split_manifest_path=split_path,
            split_name="field_regression",
            curated_dir=curated_dir,
        )
    except ValueError:
        pass

    gold_metrics = None
    try:
        gold_metrics = evaluate_model(
            features_dir,
            model_path,
            config,
            split_manifest_path=split_path,
            split_name="gold_locked",
            curated_dir=curated_dir,
        )
    except ValueError:
        pass

    all_metrics = {
        "validation_locked": metrics,
        "field_regression": field_metrics,
        "gold_locked": gold_metrics,
    }
    save_metrics(all_metrics, pipeline_root, "from_field_test")
    collection_plan = generate_collection_plan(
        metrics=all_metrics,
        curated_dir=curated_dir,
        config=config,
        model_id=result.get("model_id"),
        dataset_hash=manifest.dataset_hash,
    )
    registry_dir = register_model(
        model_path=model_path,
        repo_root=repo_root,
        config=config,
        dataset_manifest=manifest_path,
        split_manifest=split_manifest,
        metrics=all_metrics,
        collection_plan=collection_plan,
        model_id=result.get("model_id"),
    )
    print(f"   Registry entry: {registry_dir}")
    print("   Next collection recommendations:")
    for item in collection_plan.get("recommendations", []):
        print(
            f"     - P{item['priority']} {item['requested_minutes']} min "
            f"{item['activity_label']}: {item['reason']}"
        )
    print("\n✅ Field-test import and retraining complete!")
    return 0


# =============================================================================
# COMMAND: evaluate
# =============================================================================

def cmd_evaluate(args: argparse.Namespace) -> int:
    """Evaluate a trained model from the modular CLI."""
    config = load_and_merge_config(args)
    pipeline_root = get_workspace_root()
    registry_entry = Path(args.registry_entry) if args.registry_entry else None
    if registry_entry is not None:
        if not registry_entry.is_dir():
            print(f"❌ Registry entry not found: {registry_entry}")
            return 2
        if args.model is not None:
            print("❌ --model cannot be combined with --registry-entry; the registered checkpoint is exact")
            return 2
        if args.features is None:
            print("❌ --features is required with --registry-entry")
            return 2
        model_path = registry_entry / "chewnet.pth"
        features_dir = Path(args.features)
        split_path = (
            Path(args.split_manifest)
            if args.split_manifest
            else registry_entry / "split_manifest.json"
        )
        try:
            verify_registry_evaluation_inputs(registry_entry, features_dir, split_path)
        except ValueError as exc:
            print(f"❌ Registry evaluation lineage failed: {exc}")
            return 2
    else:
        model_path = Path(args.model) if args.model else pipeline_root / "models" / "exports" / "checkpoints" / "chewnet.pth"
        features_dir = Path(args.features) if args.features else latest_features_dir(pipeline_root)
        split_path = Path(args.split_manifest) if args.split_manifest else default_split_manifest_path(pipeline_root)

    metrics = evaluate_model(
        features_dir=features_dir,
        model_path=model_path,
        config=config,
        split_manifest_path=split_path if split_path.exists() else None,
        split_name=args.split,
    )

    if registry_entry is not None:
        from pipeline.promotion import assess_registry_entry, write_promotion_report

        metrics_path = record_registry_evaluation(registry_entry, args.split, metrics)
        promotion = assess_registry_entry(registry_entry)
        promotion_path = write_promotion_report(registry_entry, promotion)
        print(f"\nRecorded exact registry metrics: {metrics_path}")
        print(f"Promotion passed: {promotion.passed} ({promotion_path})")
        for reason in promotion.reasons:
            print(f"   - {reason}")
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(metrics, f, indent=2)
        print(f"\nSaved metrics: {args.output}")
    return 0


# =============================================================================
# COMMAND: registry
# =============================================================================

def cmd_registry(args: argparse.Namespace) -> int:
    """Inspect local model registry entries."""
    repo_root = get_repo_root()
    if args.latest:
        entry = latest_registry_entry(repo_root)
        if entry is None:
            print("No model registry entries found.")
            return 1
        metadata_path = entry / "metadata.json"
        print(f"Latest registry entry: {entry}")
        if metadata_path.exists():
            with open(metadata_path, "r") as f:
                metadata = json.load(f)
            print(json.dumps(metadata, indent=2))
        return 0

    print("Use --latest to show the newest registry entry.")
    return 0


def cmd_eating_experiment(args: argparse.Namespace) -> int:
    """Train/evaluate motion-only chewing-bout detectors on human coarse labels."""

    from pipeline.eating.experiment import ExperimentConfig, run_experiment
    from pipeline.eating.models import MODEL_NAMES

    models = tuple(m.strip() for m in args.models.split(",") if m.strip())
    unknown = sorted(set(models) - set(MODEL_NAMES))
    if unknown:
        print(f"Unknown models {unknown}; choose from {', '.join(MODEL_NAMES)}")
        return 2
    config = ExperimentConfig(
        window_s=args.window_s,
        stride_s=args.stride_s,
        guard_s=args.guard_s,
        folds=args.folds,
        models=models,
        tcn_epochs=args.tcn_epochs,
        seed=args.seed,
        evaluate_test=not args.dev_only,
        note=args.note or "",
    )
    try:
        run_dir = run_experiment(
            workspace=get_workspace_root(),
            config=config,
            repo_root=get_repo_root(),
            output_root=Path(args.output) if args.output else None,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Eating experiment failed: {exc}")
        return 1
    print(f"Report: {run_dir / 'report.html'}")
    return 0


def cmd_eating_report(args: argparse.Namespace) -> int:
    """Regenerate an eating experiment report without retraining."""

    from pipeline.eating.experiment import rebuild_report

    root = Path(args.output) if args.output else get_workspace_root() / "experiments" / "eating"
    if args.run:
        run_dir = root / "runs" / args.run
    else:
        latest = root / "latest.json"
        if not latest.exists():
            print(f"No eating experiment has been run yet ({latest} is missing).")
            return 1
        run_dir = Path(json.loads(latest.read_text())["run_dir"])
    try:
        path = rebuild_report(run_dir, workspace=get_workspace_root())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Report rebuild failed: {exc}")
        return 1
    print(f"Report: {path}")
    return 0


LIVE_EATING_SPEC = Path("apps/live-ios/ChewSenseLive/EatingDetector/eating-detector.json")
LIVE_EATING_FIXTURE = Path("apps/live-ios/ChewSenseLiveTests/Fixtures/eating_detector_parity.json")


def _eating_root(args: argparse.Namespace) -> Path:
    return Path(args.output) if getattr(args, "output", None) else get_workspace_root() / "experiments" / "eating"


def _eating_run_dir(root: Path, run: str | None) -> Path:
    if run:
        return root / "runs" / run
    latest = root / "latest.json"
    if not latest.exists():
        raise FileNotFoundError(f"no locked-test eating run exists yet ({latest} is missing)")
    return Path(json.loads(latest.read_text())["run_dir"])


def cmd_eating_export(args: argparse.Namespace) -> int:
    """Export a run's selected model as a deployable on-device specification."""

    from pipeline.eating.runtime import export_spec, write_spec

    root = _eating_root(args)
    try:
        run_dir = _eating_run_dir(root, args.run)
        spec = export_spec(run_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Export failed: {exc}")
        return 1
    target = root / "deploy" / spec["model_id"] / "eating-detector.json"
    digest = write_spec(spec, target)
    record = {
        "model_id": spec["model_id"],
        "run_id": spec["provenance"]["run_id"],
        "spec_path": str(target),
        "spec_sha256": digest,
        "exported_at": datetime.now().astimezone().isoformat(),
        "status": spec["status"],
    }
    (target.parent / "export.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"Exported {spec['model_id']} -> {target}")
    if args.install_live:
        live = get_repo_root() / LIVE_EATING_SPEC
        live_digest = write_spec(spec, live)
        print(f"Bundled in Live as {live} (sha256 {live_digest[:12]}); Live keeps its existing detector "
              "unless the experimental mode is chosen in Settings.")
    return 0


def cmd_eating_parity(args: argparse.Namespace) -> int:
    """Check the Swift runtime against the Python reference on recorded sessions."""

    import subprocess

    import pandas as pd

    from pipeline.eating.dataset import discover_sessions
    from pipeline.eating.parity import check_live_bundle, run_parity
    from pipeline.eating.runtime import load_spec

    repo = get_repo_root()
    spec_path = Path(args.spec) if args.spec else repo / LIVE_EATING_SPEC
    try:
        spec = load_spec(spec_path)
    except (OSError, ValueError) as exc:
        print(f"Cannot read specification {spec_path}: {exc}")
        return 1
    if args.live_check:
        try:
            result = check_live_bundle(spec, Path(args.live_check))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            print(f"Live check audit failed: {exc}")
            return 1
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "pass" else 1
    workspace = get_workspace_root()
    if args.write_live_fixture:
        from pipeline.eating.parity import live_fixture

        fixture = repo / LIVE_EATING_FIXTURE
        fixture.write_text(json.dumps(live_fixture(spec), separators=(",", ":")) + "\n")
        print(f"Wrote synthetic Live XCTest fixture {fixture}")
    tool = Path(args.swift_tool) if args.swift_tool else repo / "build" / "eating-parity" / "eating-parity"
    if not args.swift_tool:
        built = subprocess.run([str(repo / "tools" / "eating-parity" / "build"), str(tool)],
                               capture_output=True, text=True)
        if built.returncode != 0:
            print(f"Building the Swift parity tool failed:\n{built.stderr}")
            return 1
    sessions, rejected = discover_sessions(
        canonical_roots=[workspace / "sessions" / "raw", workspace / "loop" / "assisted" / "sessions" / "raw"],
        field_test_roots=[workspace / "sessions" / "field-tests"],
        loop_root=workspace / "loop",
    )
    run_dir = workspace / "experiments" / "eating" / "runs" / spec["provenance"]["run_id"]
    frame = pd.read_csv(run_dir / "predictions.csv.gz") if (run_dir / "predictions.csv.gz").exists() else None
    out_dir = workspace / "experiments" / "eating" / "deploy" / spec["model_id"]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        summary = run_parity(spec=spec, sessions=sessions, swift_tool=tool,
                             work_dir=Path(args.work_dir) if args.work_dir else Path("/tmp") / f"chewsense-parity-{stamp}",
                             run_frame=frame)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Parity check failed to run: {exc}")
        return 1
    summary["spec_path"] = str(spec_path)
    summary["rejected_recordings"] = rejected
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / f"parity-{stamp}.json"
    report.write_text(json.dumps(summary, indent=2, default=float) + "\n")
    (out_dir / "parity-latest.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    print(f"Parity {summary['status'].upper()}: {summary['recordings_passed']}/{summary['recordings']} recordings, "
          f"{summary['scored_windows']} scored windows")
    print(f"  features bit-identical: {summary['feature_values_bit_identical']:.6f}; "
          f"max probability difference {summary['max_probability_abs_diff']:.3g}; "
          f"state disagreements {summary['state_disagreements']}")
    print(f"  Swift: {summary['swift_runtime']['ms_per_window']:.3f} ms per window on this Mac (simulated stream)")
    print(f"Wrote {report}")
    return 0 if summary["status"] == "pass" else 1


def _assist_root() -> Path:
    return get_workspace_root() / "experiments" / "assist"


def cmd_assist_propose(args: argparse.Namespace) -> int:
    """Propose chewing spans from independent video and motion evidence (never labels)."""

    from pipeline.eating.assist import empirical_summary, propose_for_session_dir
    from pipeline.eating.dataset import SessionRejected
    from pipeline.eating.runtime import load_spec

    repo = get_repo_root()
    workspace = get_workspace_root()
    spec_path = Path(args.spec) if args.spec else repo / LIVE_EATING_SPEC
    try:
        spec = load_spec(spec_path)
    except (OSError, ValueError) as exc:
        print(f"Cannot read eating-detector specification {spec_path}: {exc}")
        return 1
    latest_eval = _assist_root() / "latest.json"
    empirical = empirical_summary(json.loads(latest_eval.read_text()) if latest_eval.exists() else None)
    jobs: list[tuple[Path, Path, Path]] = []
    if args.loop:
        assisted = workspace / "loop" / "assisted"
        roles = {}
        for record in sorted((workspace / "loop" / "state" / "sessions").glob("*.json")):
            data = json.loads(record.read_text())
            roles[data.get("session_id") or record.stem] = data.get("role")
        for session_dir in sorted((assisted / "sessions" / "raw").iterdir()):
            if not (session_dir / "session.json").exists():
                continue
            if roles.get(session_dir.name) == "diagnostic":
                print(f"  skip {session_dir.name}: diagnostic (timing) recording")
                continue
            jobs.append((session_dir, assisted / "derived" / "vision" / session_dir.name,
                         assisted / "derived" / "assist" / session_dir.name))
    elif args.session_dir and args.vision_dir:
        session_dir = Path(args.session_dir)
        output = Path(args.output) if args.output else (
            session_dir.parent.parent.parent / "derived" / "assist" / session_dir.name)
        jobs.append((session_dir, Path(args.vision_dir), output))
    else:
        print("Give --loop, or --session-dir with --vision-dir")
        return 2
    failures = 0
    for session_dir, vision_dir, output in jobs:
        try:
            path, payload = propose_for_session_dir(session_dir, vision_dir, spec, output,
                                                    spec_path=spec_path, empirical=empirical)
        except (FileNotFoundError, SessionRejected, ValueError) as exc:
            failures += 1
            print(f"  {session_dir.name}: no proposals ({exc})")
            continue
        summary = payload["summary"]
        print(f"  {session_dir.name}: {summary['coverage']:.0%} agreement, {summary['review_items']} spans to review, "
              f"{summary['label_conflicts']} conflicts with existing labels -> {path}")
    return 1 if failures and failures == len(jobs) else 0


def cmd_assist_eval(args: argparse.Namespace) -> int:
    """Measure agreement proposals against existing human labels on held-out predictions."""

    import pandas as pd

    from pipeline.eating.assist import AssistConfig, evaluate_assist
    from pipeline.eating.dataset import discover_sessions

    workspace = get_workspace_root()
    root = workspace / "experiments" / "eating"
    try:
        run_dir = _eating_run_dir(root, args.run)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    metrics = json.loads((run_dir / "metrics.json").read_text())
    selected = metrics.get("selected_model") or "hgb"
    predictions = pd.read_csv(run_dir / "predictions.csv.gz")
    predictions = predictions.rename(columns={f"p_{selected}": "probability"})[["session_id", "t_end", "probability"]]
    sessions, _ = discover_sessions(
        canonical_roots=[workspace / "sessions" / "raw", workspace / "loop" / "assisted" / "sessions" / "raw"],
        field_test_roots=[workspace / "sessions" / "field-tests"],
        loop_root=workspace / "loop",
    )
    roots = [Path(p) for p in args.vision_root] if args.vision_root else [
        workspace / "loop" / "assisted" / "derived" / "vision",
        workspace / "derived" / "vision",
        workspace / "experiments" / "video-teacher-20261103" / "vision",
    ]
    vision = {}
    for session in sessions:
        for vroot in roots:
            if (vroot / session.session_id / "mouth_cycles.json").exists() and (vroot / session.session_id / "signals" / "ts.npy").exists():
                vision[session.session_id] = vroot / session.session_id
                break
    if not vision:
        print("No recordings have both human labels and Vision output")
        return 1
    config = AssistConfig(motion_threshold=float(metrics["results"][selected]["threshold"]))
    result = evaluate_assist([s for s in sessions if s.session_id in vision], vision, predictions, config)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result.update({
        "evaluation_id": f"assist-{stamp}",
        "motion_run": run_dir.name,
        "motion_predictions": "out-of-fold (development) and locked-test predictions; no recording scored by a model trained on it",
        "vision_dirs": {k: str(v) for k, v in vision.items()},
        "generated_at": datetime.now().astimezone().isoformat(),
    })
    out = _assist_root()
    out.mkdir(parents=True, exist_ok=True)
    (out / f"assist-{stamp}.json").write_text(json.dumps(result, indent=2, default=float) + "\n")
    (out / "latest.json").write_text(json.dumps(result, indent=2, default=float) + "\n")
    chew, neg, cov = result["auto_chewing_precision"], result["auto_not_chewing_precision"], result["coverage"]
    print(f"Recordings {result['recordings']}, scored windows {result['windows_scored']}")
    print(f"  agreement coverage {cov['rate']:.0%}; proposed chewing correct {result['totals']['auto_chewing_correct']}/"
          f"{result['totals']['auto_chewing']} (95% CI from {chew['low']:.3f}); proposed not-chewing correct "
          f"{result['totals']['auto_not_correct']}/{result['totals']['auto_not']} (from {neg['low']:.3f})")
    print(f"  false positives {result['false_positive_windows']}, false negatives {result['false_negative_windows']}, "
          f"review windows {result['review_windows']} ({result['video_unavailable_windows']} without a visible face)")
    print(f"  modelled review effort {result['effort_model']['assisted_s']:.0f} s vs {result['effort_model']['manual_s']:.0f} s manual "
          "(modelled, not measured)")
    print(f"Wrote {out / f'assist-{stamp}.json'}")
    return 0


def cmd_pipeline_status(args: argparse.Namespace) -> int:
    """Write the one-file summary Review shows: recordings, labels, splits, model, errors, next steps."""

    from pipeline.eating.status import build_status, summarize, write_status

    workspace = get_workspace_root()
    try:
        status = build_status(workspace=workspace, repo=get_repo_root())
    except (OSError, ValueError, KeyError) as exc:
        print(f"Pipeline status failed: {exc}")
        return 1
    path = write_status(status, Path(args.output) if args.output else workspace / "experiments" / "eating" / "pipeline_status.json")
    for line in summarize(status):
        print(line)
    print(f"Wrote {path}")
    return 0


def cmd_behavior_eval(args: argparse.Namespace) -> int:
    """Validate chewing-behaviour measurements against independent references (development data)."""

    import pandas as pd

    from pipeline.behavior.measures import registry, session_behavior
    from pipeline.behavior.validate import evaluate_behavior
    from pipeline.eating.dataset import discover_sessions
    from pipeline.eating.runtime import load_spec

    workspace = get_workspace_root()
    root = workspace / "experiments" / "eating"
    try:
        run_dir = _eating_run_dir(root, args.run)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    metrics = json.loads((run_dir / "metrics.json").read_text())
    selected = metrics.get("selected_model") or "hgb"
    predictions = pd.read_csv(run_dir / "predictions.csv.gz").rename(columns={f"p_{selected}": "probability"})
    sessions, _ = discover_sessions(
        canonical_roots=[workspace / "sessions" / "raw", workspace / "loop" / "assisted" / "sessions" / "raw"],
        field_test_roots=[workspace / "sessions" / "field-tests"],
        loop_root=workspace / "loop",
    )
    vision_roots = [workspace / "loop" / "assisted" / "derived" / "vision", workspace / "derived" / "vision",
                    workspace / "experiments" / "video-teacher-20261103" / "vision"]
    vision = {}
    for session in sessions:
        for vroot in vision_roots:
            if (vroot / session.session_id / "signals" / "ts.npy").exists():
                vision[session.session_id] = vroot / session.session_id
                break
    splits = json.loads((root / "splits.json").read_text())["assignments"]
    result = evaluate_behavior(
        sessions=sessions, vision_dirs=vision, predictions=predictions,
        threshold=float(metrics["results"][selected]["threshold"]),
        test_split_ids={k for k, v in splits.items() if v["split"] == "test"},
    )
    spec = load_spec(get_repo_root() / LIVE_EATING_SPEC)
    examples = {}
    for session in sessions:
        if session.activity == "meal_legacy" and splits.get(session.session_id, {}).get("split") == "dev" and len(examples) < 3:
            examples[session.session_id] = session_behavior(spec, session.motion_t, session.imu, session.duration_s)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result.update({
        "evaluation_id": f"behavior-{stamp}",
        "generated_at": datetime.now().astimezone().isoformat(),
        "motion_run": run_dir.name,
        "registry": registry(),
        "example_meals": examples,
        "example_note": "development meals scored by the exported final model, which trained on them; "
                        "illustrates the output format only",
    })
    out = get_workspace_root() / "experiments" / "behavior"
    out.mkdir(parents=True, exist_ok=True)
    for name in (f"behavior-{stamp}.json", "latest.json"):
        (out / name).write_text(json.dumps(result, indent=2, default=float) + "\n")
    timing, cadence, cycles = result["bout_timing"], result["cadence"], result["video_cycle_confidence"]
    print(f"Bout timing (development, out-of-fold): {timing['matched_bouts']}/{timing['reference_bouts']} human bouts found; "
          f"median |onset error| {timing['median_abs_onset_error_s']} s, |offset error| {timing['median_abs_offset_error_s']} s; "
          f"pauses detected {timing['pauses_detected']}/{timing['reference_pauses']}, median |pause error| {timing['median_abs_pause_error_s']} s")
    if cadence.get("windows"):
        print(f"Cadence vs video: {cadence['windows']} windows, median |difference| {cadence['median_abs_difference_hz']} Hz, "
              f"{cadence['within_0_2_hz']:.0%} within 0.2 Hz; video range {cadence['video_hz_range']} Hz")
    if cycles.get("cycles"):
        print(f"Video cycle confidence: {cycles['cycles']} cycles; leave-one-recording-out AUC "
              f"{cycles['leave_one_recording_out']['auc_roc']:.2f} vs {cycles['fixed_constants']['auc_roc']:.2f} for the former constants")
    print(f"Wrote {out / f'behavior-{stamp}.json'}")
    return 0


def cmd_video_teacher(args: argparse.Namespace) -> int:
    """Measure how well ungated Vision output recovers human coarse chewing spans."""

    from pipeline.eating.video_teacher import cadence_consistency, evaluate_video_teacher

    workspace = get_workspace_root()
    eating_root = workspace / "experiments" / "eating"
    run_dir = eating_root / "runs" / args.run if args.run else None
    if run_dir is None and (eating_root / "latest.json").exists():
        run_dir = Path(json.loads((eating_root / "latest.json").read_text())["run_dir"])
    predictions = run_dir / "predictions.csv.gz" if run_dir else None
    threshold = 0.5
    if run_dir and (run_dir / "metrics.json").exists():
        metrics = json.loads((run_dir / "metrics.json").read_text())
        selected = metrics.get("selected_model") or "hgb"
        threshold = metrics["results"][selected]["threshold"]
        column = f"p_{selected}"
    else:
        column = "p_hgb"
    try:
        result = evaluate_video_teacher(
            workspace=workspace,
            vision_root=Path(args.vision_root),
            predictions_path=predictions,
            motion_column=column,
            motion_threshold=threshold,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Video-teacher evaluation failed: {exc}")
        return 1
    result["motion_run"] = run_dir.name if run_dir else None
    result["cadence_consistency"] = cadence_consistency(workspace=workspace, vision_root=Path(args.vision_root))
    out = Path(args.output) if args.output else Path(args.vision_root).parent / "video_teacher.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True, default=float) + "\n")
    video = result["video_cycle_rate"]
    print(f"Recordings: {len(result['recordings'])}")
    print(f"Video chew-cycle rate vs human spans: AUC-ROC {video['auc_roc']}, AUC-PR {video['auc_pr']}")
    agreement = result.get("motion_agreement")
    if agreement:
        print(f"Video and motion agree on {agreement['agree_fraction']:.0%} of windows; "
              f"accuracy when they agree {agreement['accuracy_when_agree']:.0%}")
    cadence = result["cadence_consistency"]
    if cadence.get("windows"):
        print(f"Cadence (exploratory): motion vs video dominant chewing frequency differ by a median "
              f"{cadence['median_abs_difference_hz']:.2f} Hz over {cadence['windows']} windows")
    print(f"Wrote {out}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print dataset balance + split sizes + last gold metrics."""
    pipeline_root = get_workspace_root()
    repo_root = get_repo_root()

    print("\n📊 ChewSense status")
    print("=" * 50)

    # Curated balance + activities
    curated_dir = pipeline_root / "sessions" / "curated"
    summary = balance_summary(curated_dir)
    print(f"\nCurated sessions: {summary['sessions']}")
    print(f"  positive rows: {summary['positive_rows']}")
    print(f"  negative rows: {summary['negative_rows']}")
    print(f"  positive pct:  {summary['positive_pct']:.1f}%")
    if summary["per_activity"]:
        print("  per-activity:")
        for activity, counts in sorted(summary["per_activity"].items()):
            print(f"    - {activity}: sessions={counts['sessions']} "
                  f"pos={counts['pos']} neg={counts['neg']}")

    # Split manifest
    split_path = default_split_manifest_path(pipeline_root)
    if split_path.exists():
        m = load_split_manifest(split_path)
        splits = m.get("splits", {})
        print(f"\nLocked split manifest: {split_path}")
        for name in ("train", "validation_locked", "test_locked", "field_regression", "gold_locked"):
            count = len(splits.get(name, []))
            print(f"  {name:>20}: {count} sessions")
    else:
        print(f"\nNo split manifest at {split_path}")

    # Latest registry entry + metrics
    entry = latest_registry_entry(repo_root)
    if entry is not None:
        print(f"\nLatest registry entry: {entry.name}")
        metrics_path = entry / "metrics.json"
        if metrics_path.exists():
            with open(metrics_path, "r") as f:
                metrics = json.load(f)

            def _print_split_metrics(label: str, data):
                if not isinstance(data, dict):
                    return
                acc = data.get("accuracy")
                f1 = data.get("f1")
                auc = data.get("roc_auc")
                if acc is None and f1 is None and auc is None:
                    return
                pieces = []
                if acc is not None:
                    pieces.append(f"acc={acc:.4f}")
                if auc is not None:
                    pieces.append(f"auc={auc:.4f}")
                if f1 is not None:
                    pieces.append(f"f1={f1:.4f}")
                print(f"  {label:>20}: {' '.join(pieces)}")

            print("  metrics:")
            for split_name in ("gold_locked", "validation_locked", "field_regression", "test_locked", "all"):
                if split_name in metrics:
                    _print_split_metrics(split_name, metrics[split_name])
            # Also handle flat metrics dict (single-split file)
            if "accuracy" in metrics or "f1" in metrics:
                _print_split_metrics("(flat)", metrics)
    else:
        print("\nNo registry entries yet.")
    return 0




def main() -> int:
    parser = argparse.ArgumentParser(
        description="ChewSense Pipeline CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  chewsense-motion sample
  chewsense-motion from-app
  chewsense-motion from-raw
  chewsense-motion materialize-temporal --input workspace/sessions/raw
  chewsense-motion from-field-test
  chewsense-motion evaluate
  chewsense-motion registry --latest
  chewsense-motion eating-experiment
  chewsense-motion deploy --force
        """,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # sample command
    p_sample = subparsers.add_parser(
        "sample",
        help="Reproduce the legacy aggregate eating-only sample (requires --arch mlp)",
    )
    add_common_args(p_sample)
    p_sample.set_defaults(func=cmd_sample)
    
    # from-app command
    p_from_app = subparsers.add_parser(
        "from-app",
        help="Guide for collecting and processing your own data",
    )
    add_common_args(p_from_app)
    p_from_app.set_defaults(func=cmd_from_app)
    
    # from-raw command
    p_from_raw = subparsers.add_parser(
        "from-raw",
        help="Process raw labeled CSVs through full pipeline",
    )
    p_from_raw.add_argument(
        "--input", type=Path,
        help="Directory containing raw session folders or CSVs",
    )
    p_from_raw.add_argument(
        "--materialize-only", action="store_true",
        help="For causal_tcn, write v2 temporal arrays and stop before split/training",
    )
    add_common_args(p_from_raw)
    p_from_raw.set_defaults(func=cmd_from_raw)

    # materialize-temporal command
    p_temporal = subparsers.add_parser(
        "materialize-temporal",
        help="Build causal-TCN arrays from canonical schema-v2 sessions",
    )
    p_temporal.add_argument(
        "--input", type=Path,
        help="One schema-v2 session or a directory containing session folders",
    )
    p_temporal.add_argument(
        "--output", type=Path,
        help="Output directory for X/y/timestamps/session_ids arrays",
    )
    p_temporal.add_argument(
        "--split-manifest", type=Path,
        help="Also create class-covered chronological locked splits at this path",
    )
    p_temporal.add_argument(
        "--inference-step-sec", type=float, default=0.10,
        help="Inference timestamp spacing (fixed runtime default: 0.10)",
    )
    p_temporal.add_argument(
        "--max-gap-sec", type=float, default=0.08,
        help="Largest interpolated IMU gap considered valid (default: 0.08)",
    )
    p_temporal.add_argument(
        "--skip-invalid", action="store_true",
        help="Exclude invalid sessions (recorded in the manifest) instead of aborting",
    )
    p_temporal.set_defaults(func=cmd_materialize_temporal)
    
    # from-features command
    p_from_features = subparsers.add_parser(
        "from-features",
        help="Train a legacy aggregate eating-only model (requires --arch mlp)",
    )
    p_from_features.add_argument(
        "--input", type=Path,
        help="Directory containing X.npy and y.npy",
    )
    add_common_args(p_from_features)
    p_from_features.set_defaults(func=cmd_from_features)

    # from-field-test command
    p_from_field_test = subparsers.add_parser(
        "from-field-test",
        help="Legacy aggregate field-test import/retrain (requires --arch mlp)",
    )
    p_from_field_test.add_argument(
        "--input", type=Path,
        help="Directory containing exported field-test bundles",
    )
    p_from_field_test.add_argument(
        "--curated-dir", type=Path,
        help="Directory to write imported curated sessions",
    )
    p_from_field_test.add_argument(
        "--no-archive", action="store_true",
        help="Do not move imported bundles into <input>/_archive/ after success",
    )
    p_from_field_test.add_argument(
        "--strip-archive-mov", action="store_true",
        help="After archiving, delete .mov/.mp4 inside the archived bundle to save disk",
    )
    p_from_field_test.add_argument(
        "--activity",
        help='Activity tag for these bundles (e.g. "head_shake", "walking", "talking", '
             '"drinking", "eating"). Stamped into each session\'s metadata and the manifest.',
    )
    p_from_field_test.add_argument(
        "--force-balance", action="store_true",
        help="Bypass the dataset balance gate (positives must be 10\u201390%% by default)",
    )
    p_from_field_test.add_argument(
        "--gold-locked", action="append", default=None,
        help="Add a session_id to the gold_locked split (repeat for multiple). "
             "Gold sessions must already be present in curated/. Once added, never reassigned.",
    )
    add_common_args(p_from_field_test)
    p_from_field_test.set_defaults(func=cmd_from_field_test)

    # evaluate command
    p_evaluate = subparsers.add_parser(
        "evaluate",
        help="Evaluate a trained model from feature arrays",
    )
    p_evaluate.add_argument("--model", type=Path, help="Path to model checkpoint")
    p_evaluate.add_argument("--features", type=Path, help="Directory containing X.npy/y.npy")
    p_evaluate.add_argument(
        "--registry-entry",
        type=Path,
        help="Evaluate and atomically attach metrics to this exact registry entry",
    )
    p_evaluate.add_argument(
        "--split",
        default="all",
        choices=["all", "train", "validation_locked", "test_locked", "field_regression", "gold_locked"],
        help="Split to evaluate when session_ids.npy and a split manifest are available",
    )
    p_evaluate.add_argument("--split-manifest", type=Path, help="Path to split manifest")
    p_evaluate.add_argument("--output", type=Path, help="Optional metrics JSON output")
    add_common_args(p_evaluate)
    p_evaluate.set_defaults(func=cmd_evaluate)

    # registry command
    p_registry = subparsers.add_parser(
        "registry",
        help="Inspect local model registry entries",
    )
    p_registry.add_argument("--latest", action="store_true", help="Show latest registry entry")
    p_registry.set_defaults(func=cmd_registry)
    
    # deploy command
    p_deploy = subparsers.add_parser(
        "deploy",
        help="Deploy trained model to Xcode project",
    )
    p_deploy.add_argument(
        "--model", type=Path,
        help="Path to trained model checkpoint",
    )
    p_deploy.add_argument(
        "--force", action="store_true",
        help="Overwrite existing files and bypass the regression gate",
    )
    p_deploy.add_argument(
        "--skip-coreml", action="store_true",
        help="Skip the .mlpackage rebuild and reuse exports/ChewNet.mlpackage (debug only)",
    )
    p_deploy.add_argument(
        "--skip-compare", action="store_true",
        help="Skip the regression gate from tools/evaluation/compare.py",
    )
    p_deploy.add_argument(
        "--development-override", action="store_true",
        help="Allow an unpromoted model in a non-production device build",
    )
    p_deploy.set_defaults(func=cmd_deploy)

    # compare command
    p_compare = subparsers.add_parser(
        "compare",
        help="Compare two model registry entries (defaults: 2nd-newest vs newest)",
    )
    p_compare.add_argument("--baseline", help="Registry entry id to use as baseline")
    p_compare.add_argument("--candidate", help="Registry entry id to use as candidate")
    p_compare.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    p_compare.set_defaults(func=cmd_compare)

    # clean command
    p_clean = subparsers.add_parser(
        "clean",
        help="Prune old per-iteration artifacts to keep disk usage bounded",
    )
    p_clean.add_argument("--keep-runs", type=int, default=3,
                         help="Number of derived/motion/runs subdirs to keep (default: 3)")
    p_clean.add_argument("--keep-registry", type=int, default=5,
                         help="Number of models/registry subdirs to keep (default: 5)")
    p_clean.add_argument("--keep-wandb", type=int, default=3,
                         help="Number of wandb/run-* subdirs to keep (default: 3)")
    p_clean.add_argument("--keep-archive", type=int, default=5,
                         help="Number of field_tests/_archive/ subdirs to keep (default: 5)")
    p_clean.add_argument("--strip-archive-mov", action="store_true",
                         help="Also delete .mov/.mp4 files inside any kept archive bundles")
    p_clean.add_argument("--dry-run", action="store_true",
                         help="Preview what would be deleted without removing anything")
    p_clean.set_defaults(func=cmd_clean)

    # status command
    p_eating = subparsers.add_parser(
        "eating-experiment",
        help="Train and evaluate motion-only chewing-bout detectors on human coarse labels",
    )
    p_eating.add_argument("--window-s", type=float, default=3.0, help="Causal window length (TCN requires 3)")
    p_eating.add_argument("--stride-s", type=float, default=0.5, help="Window spacing in seconds")
    p_eating.add_argument("--guard-s", type=float, default=1.0,
                          help="Seconds around label transitions excluded from scoring/training")
    p_eating.add_argument("--folds", type=int, default=5, help="Grouped development folds")
    p_eating.add_argument("--models", default="prior,motion_energy,logreg,hgb,tcn",
                          help="Comma-separated candidates")
    p_eating.add_argument("--tcn-epochs", type=int, default=12, help="Fixed TCN training epochs")
    p_eating.add_argument("--seed", type=int, default=0)
    p_eating.add_argument("--note", help="Free-text note stored in the run index")
    p_eating.add_argument("--dev-only", action="store_true",
                          help="Exploratory run: development cross-validation only; the locked test set is not touched")
    p_eating.add_argument("--output", help="Experiment root (default: <workspace>/experiments/eating)")
    p_eating.set_defaults(func=cmd_eating_experiment)

    p_eating_report = subparsers.add_parser(
        "eating-report",
        help="Rebuild the HTML report of an eating experiment (default: latest)",
    )
    p_eating_report.add_argument("--run", help="Run ID under <root>/runs/")
    p_eating_report.add_argument("--output", help="Experiment root (default: <workspace>/experiments/eating)")
    p_eating_report.set_defaults(func=cmd_eating_report)

    p_eating_export = subparsers.add_parser(
        "eating-export",
        help="Export a run's selected eating detector as a versioned on-device specification",
    )
    p_eating_export.add_argument("--run", help="Run ID (default: latest run with a locked-test result)")
    p_eating_export.add_argument("--output", help="Experiment root (default: <workspace>/experiments/eating)")
    p_eating_export.add_argument("--install-live", action="store_true",
                                 help="Also bundle the specification in the Live app source (not promoted)")
    p_eating_export.set_defaults(func=cmd_eating_export)

    p_eating_parity = subparsers.add_parser(
        "eating-parity",
        help="Compare Live's Swift eating-detector runtime with the Python reference on recordings",
    )
    p_eating_parity.add_argument("--spec", help="Specification (default: the one bundled in Live)")
    p_eating_parity.add_argument("--swift-tool", help="Prebuilt parity tool (default: build from Live sources)")
    p_eating_parity.add_argument("--work-dir", help="Scratch folder for binary inputs/outputs (default: /tmp)")
    parity_input = p_eating_parity.add_mutually_exclusive_group()
    parity_input.add_argument("--live-check", help="Audit one saved Live export without reading the dataset or labels")
    parity_input.add_argument("--write-live-fixture", action="store_true",
                              help="Also regenerate Live's synthetic XCTest parity fixture for this specification")
    p_eating_parity.set_defaults(func=cmd_eating_parity)

    p_assist = subparsers.add_parser(
        "assist-propose",
        help="Propose chewing spans where video and AirPods motion agree (proposals, never labels)",
    )
    p_assist.add_argument("--loop", action="store_true", help="All recordings in <workspace>/loop/assisted")
    p_assist.add_argument("--session-dir", help="One canonical recording folder")
    p_assist.add_argument("--vision-dir", help="Its ungated Vision output folder")
    p_assist.add_argument("--output", help="Proposal folder (default: <workspace>/derived/assist/<session>)")
    p_assist.add_argument("--spec", help="Eating-detector specification (default: the one bundled in Live)")
    p_assist.set_defaults(func=cmd_assist_propose)

    p_assist_eval = subparsers.add_parser(
        "assist-eval",
        help="Measure agreement proposals against human labels using held-out motion predictions",
    )
    p_assist_eval.add_argument("--run", help="Eating run for motion predictions (default: latest locked-test run)")
    p_assist_eval.add_argument("--vision-root", action="append", help="Folder(s) of Vision outputs by session ID")
    p_assist_eval.set_defaults(func=cmd_assist_eval)

    p_status_file = subparsers.add_parser(
        "pipeline-status",
        help="Summarize recordings, labels, splits, model, errors and next steps for Review",
    )
    p_status_file.add_argument("--output", help="Default: <workspace>/experiments/eating/pipeline_status.json")
    p_status_file.set_defaults(func=cmd_pipeline_status)

    p_behavior = subparsers.add_parser(
        "behavior-eval",
        help="Validate bout timing, pauses, cadence and video cycle confidence (development data)",
    )
    p_behavior.add_argument("--run", help="Eating run for out-of-fold predictions (default: latest)")
    p_behavior.set_defaults(func=cmd_behavior_eval)

    p_video_teacher = subparsers.add_parser(
        "video-teacher-eval",
        help="Compare ungated Vision chew proposals and the motion detector with human coarse spans",
    )
    p_video_teacher.add_argument("--vision-root", required=True,
                                 help="Folder of `chewsense-vision run` outputs named by session ID")
    p_video_teacher.add_argument("--run", help="Eating experiment run ID for motion predictions (default: latest)")
    p_video_teacher.add_argument("--output", help="JSON output path (default: next to --vision-root)")
    p_video_teacher.set_defaults(func=cmd_video_teacher)

    p_status = subparsers.add_parser(
        "status",
        help="Print dataset balance, split sizes, last metrics for the latest model",
    )
    p_status.set_defaults(func=cmd_status)

    # Parse and run
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        print("\nQuick start: chewsense-motion sample")
        return 0
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
