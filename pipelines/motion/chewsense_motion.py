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
import json
import sys
from datetime import datetime
from pathlib import Path

# Ensure pipeline module is importable
sys.path.insert(0, str(Path(__file__).parent))

from pipeline.config import load_config, merge_cli_args, PipelineConfig
from pipeline.ingest import validate_sessions, create_manifest, save_manifest
from pipeline.transform import transform_directory
from pipeline.video_labels import resolve_derived_root
from pipeline.features import extract_features_directory
from pipeline.train import train_model
from pipeline.evaluate import evaluate_model
from pipeline.field_tests import import_field_test_bundles, balance_summary, session_activity_map
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
    parser.add_argument("--arch", choices=["mlp", "cnn"], help="Model architecture")
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


def enable_two_head_when_reviewed(config: PipelineConfig, sessions: list) -> None:
    if any(
        getattr(session, "has_video_labels", False)
        and getattr(session, "verified", False)
        and getattr(session, "chew_cycle_count", 0) > 0
        for session in sessions
    ):
        config.features.extended_chew_phase = True
        config.chew_phase_head.enabled = True
        print("   Reviewed chew cycles found: enabling two-head eating + phase training.")


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
    enable_two_head_when_reviewed(config, sessions)
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
    
    print(f"""
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
    video_labels_root = resolve_derived_root(
        input_dir,
        (
            Path(args.video_labels_root)
            if getattr(args, "video_labels_root", None)
            else pipeline_root / "derived" / "vision"
        ),
    )
    require_verified = not getattr(args, "allow_unverified_video_labels", False)
    
    if not input_dir.exists():
        print(f"\n❌ Input directory not found: {input_dir}")
        return 1
    
    run_dir, transformed_dir, features_dir = make_run_workspace(pipeline_root, "from_raw")
    model_path = pipeline_root / "models" / "exports" / "checkpoints" / "chewnet.pth"
    print(f"   Run workspace: {run_dir}")
    
    # 1. Validate
    print(f"\n📋 Step 1: Validating data in {input_dir}...")
    try:
        sessions = validate_sessions(
            input_dir,
            video_labels_root=video_labels_root,
            require_verified_video_labels=require_verified,
        )
    except Exception as e:
        print(f"❌ Validation failed: {e}")
        return 1
    
    manifest = create_manifest(sessions)
    enable_two_head_when_reviewed(config, sessions)
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
        video_labels_root=video_labels_root,
        require_verified_video_labels=require_verified,
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


# =============================================================================
# COMMAND: from-features
# =============================================================================

def cmd_from_features(args: argparse.Namespace) -> int:
    """Train from pre-extracted features (skip transform/extraction)."""
    print("\n📊 Training from Pre-Extracted Features")
    print("=" * 50)
    
    config = load_and_merge_config(args)
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
    pipeline_root = get_workspace_root()
    repo_root = get_repo_root()

    input_dir = Path(args.input) if args.input else pipeline_root / "sessions" / "field-tests"
    curated_dir = Path(args.curated_dir) if args.curated_dir else pipeline_root / "sessions" / "curated"
    manifest_dir = pipeline_root / "manifests" / "datasets"
    video_labels_root = Path(
        getattr(args, "video_labels_root", None)
        or pipeline_root / "derived" / "vision"
    )
    require_verified = not getattr(args, "allow_unverified_video_labels", False)

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
    sessions = validate_sessions(
        curated_dir,
        video_labels_root=video_labels_root,
        require_verified_video_labels=require_verified,
    )
    manifest = create_manifest(sessions)
    enable_two_head_when_reviewed(config, sessions)
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
        video_labels_root=video_labels_root,
        require_verified_video_labels=require_verified,
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

    if args.output:
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


# =============================================================================
# COMMAND: status
# =============================================================================

def cmd_bite_metrics(args: argparse.Namespace) -> int:
    """Run the chew/bite evaluation harness over one or more bundles."""
    from pipeline import bite_metrics as bm

    bundle_dirs: list[Path] = []
    if args.bundle:
        bundle_dirs.extend(Path(p) for p in args.bundle)
    if args.sweep:
        bundle_dirs.extend(bm.discover_bundles(Path(args.sweep)))

    if not bundle_dirs:
        print("No bundles supplied. Use --bundle <dir> or --sweep <root>.")
        return 1

    # Optionally regenerate predictions from runtime predictions.csv first.
    if args.detect:
        from pipeline.bite_detector import detect_bites_from_bundle

        for d in bundle_dirs:
            try:
                detect_bites_from_bundle(d)
            except Exception as e:  # pragma: no cover
                print(f"  [detect] {d.name}: {e}")

    agg = bm.evaluate_bundles(bundle_dirs)
    md = bm.render_report_md(agg)

    reports_dir = get_workspace_root() / "reports" / "evaluation"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = Path(args.output) if args.output else reports_dir / f"{ts}_chewbite.md"
    out_path.write_text(md)
    print(f"Wrote {out_path}")
    print()
    print(md)
    return 0


def cmd_meal_report(args: argparse.Namespace) -> int:
    """Assemble bites into a meal-level summary (Phase 6).

    Reads `predictions.csv` from a field-test bundle (or
    `bite_events.csv` if `--bites` is passed), runs the meal analyzer
    and chew-quality scorer, then writes JSON + markdown summaries to the
    workspace reports directory.
    """
    bundle = Path(args.bundle).resolve() if args.bundle else None
    bites_csv = Path(args.bites).resolve() if args.bites else None
    if not bundle and not bites_csv:
        print("Pass either --bundle <dir> or --bites <bite_events.csv>.")
        return 1

    from pipeline.bite_detector import detect_bites_from_bundle
    from pipeline.meal_analyzer import MealAnalyzer
    from pipeline.chew_quality import ChewQualityEvaluator
    from pipeline.events import BiteEvent, ChewStroke  # type: ignore

    # --- Load bites ----------------------------------------------------
    if bundle is not None:
        bites = detect_bites_from_bundle(bundle)
        session_id = bundle.name
    else:
        import csv
        with open(bites_csv, newline="") as f:
            rows = list(csv.DictReader(f))
        bites = [
            BiteEvent(
                start_time=float(r["start_time"]),
                end_time=float(r["end_time"]),
                confidence=float(r.get("confidence", 1.0)),
                chew_count=int(r.get("chew_count", 0) or 0),
                chew_strokes=[],
            )
            for r in rows
        ]
        session_id = bites_csv.parent.name

    if not bites:
        print(f"No bites found for session {session_id}.")
        return 1

    analyzer = MealAnalyzer()
    metrics = analyzer.analyze_meal(bites)
    phases = analyzer.detect_phases(bites)
    quality = ChewQualityEvaluator().evaluate_meal(metrics, bites)

    # --- Serialize -----------------------------------------------------
    def _enum_to_str(v):
        return v.value if hasattr(v, "value") else v

    from dataclasses import asdict
    payload = {
        "session_id": session_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "metrics": asdict(metrics),
        "phases": [asdict(p) for p in phases],
        "quality": {k: _enum_to_str(v) for k, v in asdict(quality).items()},
    }

    out_dir = Path(args.output_dir) if args.output_dir else (
        get_workspace_root() / "reports" / "meals"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{session_id}.json"
    md_path = out_dir / f"{session_id}.md"

    json_path.write_text(json.dumps(payload, indent=2))

    md_lines = [
        f"# Meal report — {session_id}",
        "",
        f"_Generated {payload['generated_at']}_",
        "",
        "## Summary",
        f"- Bites: **{metrics.bite_count}**",
        f"- Chews: **{metrics.chew_count}**",
        f"- Duration: **{metrics.total_duration:.1f} s** "
        f"({metrics.eating_efficiency*100:.0f}% chewing)",
        f"- Avg chews / bite: **{metrics.avg_chews_per_bite:.1f}**",
        f"- Bite rate: **{metrics.bite_rate_per_minute:.1f}** / min",
        f"- Rushed bites: **{metrics.rushed_bite_count}** "
        f"({metrics.rushed_bite_fraction*100:.0f}%)",
        f"- Pace trend: **{metrics.pace_trend}** "
        f"({metrics.first_half_pace:.1f}s → {metrics.second_half_pace:.1f}s IBI)",
        "",
        "## Quality",
        f"- Overall: **{quality.overall_score:.0f}** "
        f"({_enum_to_str(quality.overall_rating)})",
        f"- Pace: {_enum_to_str(quality.eating_pace_rating)}",
        f"- Thoroughness: {_enum_to_str(quality.thoroughness_rating)} "
        f"(well-chewed {quality.well_chewed_percent:.0f}%)",
        f"- Rhythm: {_enum_to_str(quality.rhythm_rating)} "
        f"(pauses: {quality.mindful_pauses})",
    ]
    if quality.recommendations:
        md_lines += ["", "## Recommendations"]
        md_lines += [f"- {r}" for r in quality.recommendations]
    if phases:
        md_lines += ["", "## Phases"]
        for i, ph in enumerate(phases, 1):
            label = ph.phase_label or "—"
            md_lines.append(
                f"{i}. {ph.start_time:.1f}–{ph.end_time:.1f}s · "
                f"{ph.bite_count} bites · pace {ph.avg_pace:.1f}s · {label}"
            )

    md_path.write_text("\n".join(md_lines) + "\n")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
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
  chewsense-motion from-field-test
  chewsense-motion evaluate
  chewsense-motion registry --latest
  chewsense-motion deploy --force
        """,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # sample command
    p_sample = subparsers.add_parser(
        "sample",
        help="Run pipeline with sample data (for demo/testing)",
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
        "--video-labels-root", type=Path,
        help="Derived video-label root; canonical labels inside sessions take precedence",
    )
    p_from_raw.add_argument(
        "--allow-unverified-video-labels", action="store_true",
        help="Allow automatic video labels into training before final review",
    )
    add_common_args(p_from_raw)
    p_from_raw.set_defaults(func=cmd_from_raw)
    
    # from-features command
    p_from_features = subparsers.add_parser(
        "from-features",
        help="Train from pre-extracted features (skip transform/extraction)",
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
        help="Import real-world app feedback bundles and retrain from curated sessions",
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
        "--video-labels-root", type=Path,
        help="Derived video-label root; canonical labels inside sessions take precedence",
    )
    p_from_field_test.add_argument(
        "--allow-unverified-video-labels", action="store_true",
        help="Allow automatic video labels into training before final review",
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
    p_status = subparsers.add_parser(
        "status",
        help="Print dataset balance, split sizes, last metrics for the latest model",
    )
    p_status.set_defaults(func=cmd_status)

    # bite-metrics command
    p_bite = subparsers.add_parser(
        "bite-metrics",
        help="Evaluate chew-stroke / bite metrics for one or more field-test bundles",
    )
    p_bite.add_argument(
        "--bundle", action="append", default=None,
        help="Path to a single bundle directory (repeat for multiple)",
    )
    p_bite.add_argument(
        "--sweep", type=Path,
        help="Root directory containing field-test bundle subdirectories",
    )
    p_bite.add_argument(
        "--detect", action="store_true",
        help="Regenerate bite_events.csv / chew_events.csv from predictions.csv before evaluating",
    )
    p_bite.add_argument(
        "--output", type=Path,
        help="Output markdown path (default: workspace/reports/evaluation/...)",
    )
    p_bite.set_defaults(func=cmd_bite_metrics)

    # meal-report command
    p_meal = subparsers.add_parser(
        "meal-report",
        help="Assemble bites into a meal-level summary (JSON + markdown).",
    )
    p_meal.add_argument(
        "--bundle", type=Path,
        help="Path to a field-test bundle dir containing predictions.csv",
    )
    p_meal.add_argument(
        "--bites", type=Path,
        help="Path to an existing bite_events.csv (skip bite detection)",
    )
    p_meal.add_argument(
        "--output-dir", type=Path,
        help="Override output directory (default: workspace/reports/meals/)",
    )
    p_meal.set_defaults(func=cmd_meal_report)

    
    # Parse and run
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        print("\nQuick start: chewsense-motion sample")
        return 0
    
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
