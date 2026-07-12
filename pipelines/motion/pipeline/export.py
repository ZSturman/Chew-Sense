"""
Export stage: Export trained model to CoreML and generate Swift constants.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import torch
import torch.nn as nn

from .features import FEATURE_NAMES
from .train import ChewNet, TwoHeadChewNet


# Path inside an .mlpackage where the actual CoreML model proto lives. Used
# for SHA-256 fingerprinting so the iOS/macOS app can confirm which exact
# model bytes are bundled.
MLPACKAGE_MODEL_RELPATH = Path("Data/com.apple.CoreML/model.mlmodel")


def compute_mlpackage_sha256(mlpackage_path: Path) -> str:
    """Compute SHA-256 of the model.mlmodel inside an .mlpackage.

    Returns empty string if the expected inner file is missing (caller can
    decide whether that's fatal).
    """
    inner = Path(mlpackage_path) / MLPACKAGE_MODEL_RELPATH
    if not inner.exists():
        return ""
    h = hashlib.sha256()
    with open(inner, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export_coreml(
    model_path: Path,
    output_path: Path,
) -> Path:
    """
    Export PyTorch model to CoreML format.
    
    Supports both binary (1-output) and mouth shape (multi-output) models.
    
    Args:
        model_path: Path to PyTorch checkpoint
        output_path: Path for CoreML output (should end in .mlpackage)
    
    Returns:
        Path to the exported model
    """
    try:
        import coremltools as ct
    except BaseException as exc:
        raise RuntimeError(
            "coremltools is required for CoreML export.\n"
            "Install with: pip install coremltools"
        ) from exc
    
    model_path = Path(model_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Load checkpoint
    ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
    
    input_dim = ckpt["input_dim"]
    hidden_dim = ckpt["hidden_dim"]
    num_outputs = ckpt.get("num_outputs", 1)
    mode = ckpt.get("mode", "binary")

    if mode == "two_head":
        model = TwoHeadChewNet(input_dim=input_dim, hidden_dim=hidden_dim)
    else:
        model = ChewNet(input_dim=input_dim, hidden_dim=hidden_dim, num_outputs=num_outputs)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    
    # Trace model
    example_input = torch.randn(1, input_dim)
    traced = torch.jit.trace(model, example_input)
    traced.eval()
    
    # Convert to CoreML
    mlmodel = ct.convert(
        traced,
        inputs=[ct.TensorType(name="input", shape=example_input.shape)],
    )
    
    # Rename output. Two-head models emit a single (B, 3) tensor we expose as
    # `output` and let the Swift side slice columns [logit, sin, cos].
    spec = mlmodel.get_spec()
    old_name = spec.description.output[0].name
    if mode == "two_head":
        output_name = "output"
    elif num_outputs > 1:
        output_name = "mouth_shape"
    else:
        output_name = "logit"
    ct.utils.rename_feature(spec, old_name, output_name)
    
    # Rebuild with weights
    mlmodel_renamed = ct.models.MLModel(spec, weights_dir=mlmodel.weights_dir)
    mlmodel_renamed.save(str(output_path))
    
    print(f"✅ Exported CoreML model: {output_path} (mode={mode}, outputs={num_outputs})")
    return output_path


def export_normalization_json(
    model_path: Path,
    output_path: Path,
) -> Path:
    """
    Export normalization constants to JSON.
    
    Args:
        model_path: Path to PyTorch checkpoint
        output_path: Path for JSON output
    
    Returns:
        Path to the JSON file
    """
    model_path = Path(model_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
    
    data = {
        "model_id": ckpt.get("model_id"),
        "feature_mean": ckpt["mean"].tolist(),
        "feature_std": ckpt["std"].tolist(),
        "feature_names": FEATURE_NAMES,
        "input_dim": ckpt["input_dim"],
        "hidden_dim": ckpt["hidden_dim"],
        "num_outputs": ckpt.get("num_outputs", 1),
        "mode": ckpt.get("mode", "binary"),
    }
    
    # Mouth shape specific metadata
    if ckpt.get("mode") == "mouth_shape":
        data["label_names"] = ckpt.get("label_names", [])
        if ckpt.get("label_mean") is not None:
            data["label_mean"] = ckpt["label_mean"].tolist()
            data["label_std"] = ckpt["label_std"].tolist()
    
    # Include W&B info if available
    if "wandb_run_id" in ckpt:
        data["wandb_run_id"] = ckpt["wandb_run_id"]
    if "wandb_run_url" in ckpt:
        data["wandb_run_url"] = ckpt["wandb_run_url"]
    if "dataset_hash" in ckpt:
        data["dataset_hash"] = ckpt["dataset_hash"]
    if "config" in ckpt:
        data["runtime_config"] = ckpt["config"].get("runtime", {})
    
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"✅ Exported normalization JSON: {output_path}")
    return output_path


def format_swift_array(values: list[float], indent: str = "    ") -> str:
    """Format a list of floats as a Swift array literal."""
    formatted = [f"{v:.10f}" for v in values]
    return f"[\n{indent}    " + f",\n{indent}    ".join(formatted) + f"\n{indent}]"


def format_swift_string(value: Any) -> str:
    """Format a Python value as a Swift string literal."""
    return json.dumps("" if value is None else str(value))


def format_swift_string_array(values: list[Any]) -> str:
    """Format Python values as a Swift [String] literal."""
    return "[" + ", ".join(format_swift_string(value) for value in values) + "]"


def generate_swift_constants(
    model_path: Path,
    output_path: Path,
    runtime_config: Optional[dict] = None,
    model_id: Optional[str] = None,
    model_package_sha256: Optional[str] = None,
    dataset_status: Optional[str] = None,
) -> Path:
    """
    Generate Swift normalization constants from trained model.
    
    This file should be imported into the iOS/macOS app and replaces
    any hardcoded normalization arrays.
    
    Args:
        model_path: Path to PyTorch checkpoint
        output_path: Path for Swift output
        runtime_config: Optional runtime config to include
    
    Returns:
        Path to the Swift file
    """
    model_path = Path(model_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
    
    mean = ckpt["mean"].tolist()
    std = ckpt["std"].tolist()
    num_outputs = ckpt.get("num_outputs", 1)
    mode = ckpt.get("mode", "binary")
    label_names = ckpt.get("label_names", [])
    model_id = model_id or ckpt.get("model_id") or "unknown"
    
    # Get W&B info for traceability
    wandb_run_id = ckpt.get("wandb_run_id")
    wandb_run_url = ckpt.get("wandb_run_url")
    dataset_hash = ckpt.get("dataset_hash")
    
    # Get runtime config from checkpoint or parameter
    if runtime_config is None and "config" in ckpt:
        runtime_config = ckpt["config"].get("runtime", {})
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Build Swift file
    swift_code = f'''//
//  NormalizationConstants.swift
//  ChewSense
//
//  AUTO-GENERATED by pipeline/export.py
//  Do not edit manually - regenerate from pipeline after training.
//
//  Generated: {timestamp}
'''
    
    if wandb_run_id:
        swift_code += f"//  W&B Run ID: {wandb_run_id}\n"
    if wandb_run_url:
        swift_code += f"//  W&B Run URL: {wandb_run_url}\n"
    if dataset_hash:
        swift_code += f"//  Dataset Hash: {dataset_hash}\n"
    
    swift_code += '''//

import Foundation

/// Normalization constants for ChewNet feature preprocessing.
/// These values are computed from the training set and must match the model.
struct NormalizationConstants {
    
    /// Feature means from training data (12 features)
    /// Order: mean_accel_mag, var_accel_mag, rms_accel_mag, mean_gyro_mag, rms_gyro_mag,
    ///        rms_jerk, zcr_accel_mag, chew_bandpower_1_3hz, bandpower_0_8_1_5hz,
    ///        bandpower_2_4hz, spectral_centroid, spectral_rolloff_0_85
'''
    
    swift_code += f"    static let FEATURE_MEAN: [Double] = {format_swift_array(mean)}\n\n"
    swift_code += "    /// Feature standard deviations from training data (12 features)\n"
    swift_code += f"    static let FEATURE_STD: [Double] = {format_swift_array(std)}\n\n"
    
    # Add output metadata
    swift_code += f"    /// Number of model outputs\n"
    swift_code += f"    static let NUM_OUTPUTS: Int = {num_outputs}\n\n"
    swift_code += f"    /// Model mode: \"binary\", \"mouth_shape\", or \"two_head\"\n"
    swift_code += f'    static let MODE: String = "{mode}"\n\n'
    swift_code += f"    /// Local model registry id\n"
    swift_code += f'    static let MODEL_ID: String = "{model_id}"\n\n'
    swift_code += f"    /// Dataset hash used for training, when available\n"
    swift_code += f'    static let DATASET_HASH: String = "{dataset_hash or ""}"\n\n'
    swift_code += f"    /// Dataset lineage status for this bundled CoreML export\n"
    swift_code += f'    static let DATASET_STATUS: String = "{dataset_status or ""}"\n\n'
    swift_code += f"    /// SHA-256 of ChewNet.mlpackage/Data/com.apple.CoreML/model.mlmodel\n"
    swift_code += f'    static let MODEL_PACKAGE_SHA256: String = "{model_package_sha256 or ""}"\n\n'
    swift_code += f"    /// Timestamp this file was generated by the pipeline\n"
    swift_code += f'    static let GENERATED_AT: String = "{timestamp}"\n\n'
    swift_code += f"    /// W&B run metadata, when available\n"
    swift_code += f'    static let WANDB_RUN_ID: String = "{wandb_run_id or ""}"\n'
    swift_code += f'    static let WANDB_RUN_URL: String = "{wandb_run_url or ""}"\n\n'
    
    if mode == "mouth_shape" and label_names:
        swift_code += "    /// Output parameter names (order matches model output)\n"
        names_swift = ', '.join(f'"{n}"' for n in label_names)
        swift_code += f"    static let OUTPUT_NAMES: [String] = [{names_swift}]\n\n"
    elif mode == "two_head":
        # Phase 5: two-head model emits [eating_logit, chew_phase_sin, chew_phase_cos].
        output_names = ckpt.get("output_names", ["eating_logit", "chew_phase_sin", "chew_phase_cos"])
        swift_code += "    /// Output column order in the two-head ChewNet (length 3)\n"
        names_swift = ', '.join(f'"{n}"' for n in output_names)
        swift_code += f"    static let OUTPUT_NAMES: [String] = [{names_swift}]\n\n"
    
    swift_code += r'''    /// Normalize features using training statistics
    /// - Parameter features: Raw feature vector (12 elements)
    /// - Returns: Normalized feature vector
    static func normalize(_ features: [Double]) -> [Double] {
        guard features.count == FEATURE_MEAN.count else {
            fatalError("Expected \(FEATURE_MEAN.count) features, got \\(features.count)")
        }
        return zip(zip(features, FEATURE_MEAN), FEATURE_STD).map { (($0.0 - $0.1) / $1) }
    }
}

'''
    
    # Add runtime config if provided
    if runtime_config:
        swift_code += '''/// Runtime detection parameters.
/// These control the chewing state machine behavior and can be tuned without retraining.
struct RuntimeConfig {
'''
        alpha = runtime_config.get("alpha", 0.4)
        high = runtime_config.get("high_threshold", 0.6)
        low = runtime_config.get("low_threshold", 0.4)
        min_start = runtime_config.get("min_start_windows", 3)
        min_end = runtime_config.get("min_end_windows", 2)
        min_chew_period = runtime_config.get("min_chew_period_sec", 0.35)
        min_gap = runtime_config.get("min_gap_sec", 1.5)
        min_bite_dur = runtime_config.get("min_bite_duration_sec", 1.0)
        rushed = runtime_config.get("rushed_chews_per_bite", 5)
        bite_rate_win = runtime_config.get("bite_rate_window_sec", 300.0)

        swift_code += f'''    /// EMA smoothing factor (0 = no smoothing, 1 = no memory)
    static let alpha: Double = {alpha}
    
    /// Probability threshold to START a chewing episode
    static let highThreshold: Double = {high}
    
    /// Probability threshold to END a chewing episode
    static let lowThreshold: Double = {low}
    
    /// Consecutive windows above highThreshold to confirm chewing start
    static let minStartWindows: Int = {min_start}
    
    /// Consecutive windows below lowThreshold to confirm chewing end
    static let minEndWindows: Int = {min_end}

    // ------------------------------------------------------------------
    // Chew-stroke / bite segmentation (motion-only metrics layer)
    // ------------------------------------------------------------------

    /// Minimum spacing between two chew strokes (seconds).
    /// Roughly 1 / max chew frequency, default 0.35 s ≈ 2.86 Hz upper bound.
    static let minChewPeriodSec: Double = {min_chew_period}

    /// Two chewing episodes whose gap is less than this are merged into a
    /// single bite (seconds).
    static let minGapSec: Double = {min_gap}

    /// Episodes shorter than this are dropped as noise (seconds).
    static let minBiteDurationSec: Double = {min_bite_dur}

    /// Bites with fewer than this many chew strokes are flagged as rushed.
    static let rushedChewsPerBite: Int = {rushed}

    /// Rolling window for `bite_rate_per_minute` (seconds).
    static let biteRateWindowSec: Double = {bite_rate_win}
}}
'''
    
    with open(output_path, "w") as f:
        f.write(swift_code)
    
    print(f"✅ Generated Swift constants: {output_path}")
    return output_path


def generate_swift_collection_recommendations(
    collection_plan: dict | Path | None,
    output_path: Path,
) -> Path:
    """Generate the app's bundled closed-loop collection recommendations."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if collection_plan is None:
        plan: dict[str, Any] = {}
    elif isinstance(collection_plan, Path):
        try:
            with open(collection_plan, "r") as f:
                plan = json.load(f)
        except (OSError, json.JSONDecodeError):
            plan = {}
    else:
        plan = collection_plan

    recommendations = plan.get("recommendations") if isinstance(plan, dict) else []
    if not isinstance(recommendations, list):
        recommendations = []

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "//",
        "//  CollectionRecommendations.swift",
        "//  ChewSense",
        "//",
        "//  AUTO-GENERATED by pipeline/export.py",
        "//  Do not edit manually - regenerate from pipeline after retraining.",
        "//",
        f"//  Generated: {timestamp}",
        "//",
        "",
        "import Foundation",
        "",
        "enum CollectionRecommendations {",
        "    static let current = CollectionPlan(",
        f"        version: {int(plan.get('version', 1) or 1)},",
        f"        modelID: {format_swift_string(plan.get('model_id', ''))},",
        f"        datasetHash: {format_swift_string(plan.get('dataset_hash', ''))},",
        f"        generatedAt: {format_swift_string(plan.get('generated_at', ''))},",
        f"        sourceSplit: {format_swift_string(plan.get('source_split', 'none'))},",
        "        recommendations: [",
    ]

    for item in recommendations:
        if not isinstance(item, dict):
            continue
        reason_codes = item.get("reason_codes")
        if not isinstance(reason_codes, list):
            reason_codes = []
        lines.extend([
            "            CollectionRecommendation(",
            f"                id: {format_swift_string(item.get('id', ''))},",
            f"                activity: {format_swift_string(item.get('activity', 'custom'))},",
            f"                activityLabel: {format_swift_string(item.get('activity_label', 'Custom activity'))},",
            f"                expectedLabelClass: {format_swift_string(item.get('expected_label_class', 'unknown'))},",
            f"                priority: {int(item.get('priority', 3) or 3)},",
            f"                requestedMinutes: {int(item.get('requested_minutes', 0) or 0)},",
            f"                reasonCodes: {format_swift_string_array(reason_codes)},",
            f"                reason: {format_swift_string(item.get('reason', ''))},",
            f"                currentMinutes: {float(item.get('current_minutes', 0.0) or 0.0):.2f},",
            f"                targetMinutes: {float(item.get('target_minutes', 0.0) or 0.0):.2f}",
            "            ),",
        ])

    lines.extend([
        "        ]",
        "    )",
        "}",
        "",
    ])

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    print(f"✅ Generated Swift collection recommendations: {output_path}")
    return output_path
