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

from .features import FEATURE_NAMES
from .train import ChewNet
from .temporal import (
    CONTEXT_SAMPLES,
    MODEL_INPUT_CHANNELS,
    MODEL_OUTPUTS,
    TemporalChewNet,
)


class CoreMLRuntimeUnavailable(RuntimeError):
    """CoreML conversion worked, but this host cannot compile/run predictions."""


def verify_temporal_coreml_parity(
    model_path: Path,
    mlpackage_path: Path,
    windows: np.ndarray,
    *,
    tolerance: float = 1e-3,
) -> dict[str, float | int]:
    """Compare normalized PyTorch and CoreML temporal outputs exactly.

    CoreML exports use a fixed batch of one, so each supplied raw ``(7,150)``
    window is replayed independently.  The returned probability error applies
    sigmoid to the eating and chew-event logits, matching the Swift runtime.
    """

    try:
        import coremltools as ct
    except BaseException as exc:
        raise RuntimeError("coremltools is required for parity verification") from exc

    checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
    if checkpoint.get("mode") != "temporal_tcn":
        raise ValueError("CoreML temporal parity requires a temporal_tcn checkpoint")
    raw = np.asarray(windows, dtype=np.float32)
    if raw.ndim == 2:
        raw = raw[None, ...]
    expected_shape = (
        int(checkpoint.get("input_channels", len(MODEL_INPUT_CHANNELS))),
        int(checkpoint.get("context_samples", CONTEXT_SAMPLES)),
    )
    if raw.ndim != 3 or tuple(raw.shape[1:]) != expected_shape:
        raise ValueError(f"windows must have shape (N, {expected_shape[0]}, {expected_shape[1]})")

    mean = np.asarray(checkpoint["mean"], dtype=np.float32).reshape(1, 6, 1)
    std = np.asarray(checkpoint["std"], dtype=np.float32).reshape(1, 6, 1)
    normalized = raw.copy()
    normalized[:, :6, :] = (normalized[:, :6, :] - mean) / std
    normalized[:, :6, :] *= normalized[:, 6:7, :]

    model = TemporalChewNet(
        input_channels=expected_shape[0],
        channels=checkpoint.get("channels", [24, 24, 32, 32]),
        kernel_size=checkpoint.get("kernel_size", 5),
        dropout=checkpoint.get("dropout", 0.0),
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    with torch.no_grad():
        pytorch_output = model(torch.from_numpy(normalized)).numpy()

    try:
        coreml_model = ct.models.MLModel(str(mlpackage_path))
    except RuntimeError as exc:
        raise CoreMLRuntimeUnavailable(f"CoreML runtime could not load the model: {exc}") from exc
    coreml_rows: list[np.ndarray] = []
    for window in normalized:
        try:
            prediction = coreml_model.predict({"input": window[None, ...]})
        except RuntimeError as exc:
            raise CoreMLRuntimeUnavailable(
                f"CoreML runtime could not execute the model: {exc}"
            ) from exc
        if "output" not in prediction:
            raise ValueError("CoreML temporal model has no 'output' feature")
        coreml_rows.append(np.asarray(prediction["output"], dtype=np.float32).reshape(-1))
    coreml_output = np.stack(coreml_rows)
    if coreml_output.shape != pytorch_output.shape:
        raise ValueError(
            f"CoreML output shape {coreml_output.shape} != PyTorch {pytorch_output.shape}"
        )

    raw_error = float(np.max(np.abs(coreml_output - pytorch_output)))
    pytorch_prob = 1.0 / (1.0 + np.exp(-pytorch_output[:, :2]))
    coreml_prob = 1.0 / (1.0 + np.exp(-coreml_output[:, :2]))
    probability_error = float(np.max(np.abs(coreml_prob - pytorch_prob)))
    if raw_error > tolerance or probability_error > tolerance:
        raise AssertionError(
            "PyTorch/CoreML parity failed: "
            f"raw={raw_error:.6g}, probability={probability_error:.6g}, tolerance={tolerance}"
        )
    return {
        "windows": len(raw),
        "max_raw_output_error": raw_error,
        "max_probability_error": probability_error,
        "tolerance": tolerance,
    }


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
    
    Supports the v2 temporal model and legacy binary eating-only checkpoints.
    
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
    
    num_outputs = ckpt.get("num_outputs", 1)
    mode = ckpt.get("mode", "binary")

    if mode == "temporal_tcn":
        model = TemporalChewNet(
            input_channels=ckpt.get("input_channels", len(MODEL_INPUT_CHANNELS)),
            channels=ckpt.get("channels", [24, 24, 32, 32]),
            kernel_size=ckpt.get("kernel_size", 5),
            dropout=ckpt.get("dropout", 0.0),
        )
        example_input = torch.randn(
            1,
            ckpt.get("input_channels", len(MODEL_INPUT_CHANNELS)),
            ckpt.get("context_samples", CONTEXT_SAMPLES),
        )
    elif mode == "binary":
        input_dim = ckpt["input_dim"]
        hidden_dim = ckpt["hidden_dim"]
        model = ChewNet(input_dim=input_dim, hidden_dim=hidden_dim)
        example_input = torch.randn(1, input_dim)
    else:
        raise ValueError(
            f"checkpoint mode {mode!r} is retired; retrain from canonical v2 temporal windows"
        )
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    
    # Trace model
    traced = torch.jit.trace(model, example_input)
    traced.eval()
    
    # Convert to CoreML
    mlmodel = ct.convert(
        traced,
        inputs=[ct.TensorType(name="input", shape=example_input.shape)],
    )
    
    # Temporal models expose their five-column output tensor; legacy binary
    # checkpoints expose one eating logit.
    spec = mlmodel.get_spec()
    old_name = spec.description.output[0].name
    if mode == "temporal_tcn":
        output_name = "output"
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
    
    mode = ckpt.get("mode", "binary")
    data = {
        "model_id": ckpt.get("model_id"),
        "feature_mean": ckpt["mean"].tolist(),
        "feature_std": ckpt["std"].tolist(),
        "feature_names": (
            list(MODEL_INPUT_CHANNELS[:6]) if mode == "temporal_tcn" else FEATURE_NAMES
        ),
        "input_dim": ckpt.get("input_dim"),
        "hidden_dim": ckpt.get("hidden_dim"),
        "input_channels": ckpt.get("input_channels"),
        "context_samples": ckpt.get("context_samples"),
        "output_names": ckpt.get("output_names"),
        "num_outputs": ckpt.get("num_outputs", 1),
        "mode": mode,
    }
    
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
    chew_event_promoted: bool = False,
    food_entry_promoted: bool = False,
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
    
    /// Training-set normalization. Temporal models store six raw-axis values;
    /// legacy models store twelve aggregate-feature values.
'''
    
    swift_code += f"    static let FEATURE_MEAN: [Double] = {format_swift_array(mean)}\n\n"
    swift_code += "    /// Training-set standard deviations\n"
    swift_code += f"    static let FEATURE_STD: [Double] = {format_swift_array(std)}\n\n"
    
    # Add output metadata
    swift_code += "    /// Number of model outputs\n"
    swift_code += f"    static let NUM_OUTPUTS: Int = {num_outputs}\n\n"
    swift_code += "    /// Model mode; new chew models use \"temporal_tcn\"\n"
    swift_code += f'    static let MODE: String = "{mode}"\n\n'
    swift_code += "    /// Local model registry id\n"
    swift_code += f'    static let MODEL_ID: String = "{model_id}"\n\n'
    swift_code += "    /// Dataset hash used for training, when available\n"
    swift_code += f'    static let DATASET_HASH: String = "{dataset_hash or ""}"\n\n'
    swift_code += "    /// Dataset lineage status for this bundled CoreML export\n"
    swift_code += f'    static let DATASET_STATUS: String = "{dataset_status or ""}"\n\n'
    swift_code += "    /// SHA-256 of ChewNet.mlpackage/Data/com.apple.CoreML/model.mlmodel\n"
    swift_code += f'    static let MODEL_PACKAGE_SHA256: String = "{model_package_sha256 or ""}"\n\n'
    swift_code += "    /// Timestamp this file was generated by the pipeline\n"
    swift_code += f'    static let GENERATED_AT: String = "{timestamp}"\n\n'
    swift_code += "    /// W&B run metadata, when available\n"
    swift_code += f'    static let WANDB_RUN_ID: String = "{wandb_run_id or ""}"\n'
    swift_code += f'    static let WANDB_RUN_URL: String = "{wandb_run_url or ""}"\n\n'
    swift_code += "    /// Exact capabilities passed by promotion for this model build\n"
    swift_code += (
        f"    static let CHEW_EVENT_HEAD_PROMOTED: Bool = "
        f"{str(bool(chew_event_promoted)).lower()}\n"
    )
    swift_code += (
        f"    static let FOOD_ENTRY_HEAD_PROMOTED: Bool = "
        f"{str(bool(food_entry_promoted)).lower()}\n\n"
    )
    
    if mode == "temporal_tcn":
        output_names = ckpt.get("output_names", list(MODEL_OUTPUTS))
        names_swift = ', '.join(f'"{n}"' for n in output_names)
        temporal_config = ckpt.get("config", {}).get("temporal_model", {})
        swift_code += f"    static let OUTPUT_NAMES: [String] = [{names_swift}]\n"
        swift_code += (
            f"    static let SAMPLE_RATE_HZ: Int = "
            f"{temporal_config.get('sample_rate_hz', 50)}\n"
        )
        swift_code += (
            f"    static let CONTEXT_SAMPLES: Int = "
            f"{ckpt.get('context_samples', CONTEXT_SAMPLES)}\n"
        )
        swift_code += "    static let INPUT_CHANNELS: Int = 7\n\n"
    
    swift_code += r'''    /// Normalize features using training statistics
    /// - Parameter features: Raw feature vector (12 elements)
    /// - Returns: Normalized feature vector
    static func normalize(_ features: [Double]) -> [Double] {
        guard features.count == FEATURE_MEAN.count else {
            fatalError("Expected \(FEATURE_MEAN.count) features, got \\(features.count)")
        }
        return zip(zip(features, FEATURE_MEAN), FEATURE_STD).map { (($0.0 - $0.1) / $1) }
    }

    /// Normalize six raw IMU axes while leaving the gap-validity mask intact.
    static func normalizeTemporal(_ input: [Double]) -> [Double] {
        guard input.count == 7 * 150, FEATURE_MEAN.count == 6 else { return input }
        var output = input
        for channel in 0..<6 {
            for sample in 0..<150 {
                let index = channel * 150 + sample
                let validity = input[6 * 150 + sample]
                output[index] = validity >= 0.5
                    ? (input[index] - FEATURE_MEAN[channel]) / FEATURE_STD[channel]
                    : 0
            }
        }
        return output
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
        event_threshold = runtime_config.get("chew_event_threshold", 0.5)
        min_gap_coverage = runtime_config.get("min_gap_coverage", 0.8)
        refractory = runtime_config.get("event_refractory_sec", 0.25)
        confident_event = runtime_config.get("confident_event_threshold", 0.5)
        bout_gap = runtime_config.get("bout_merge_gap_sec", 1.5)
        min_bout_duration = runtime_config.get("min_bout_duration_sec", 1.0)

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

    /// Learned chew-event peak decoder. Phase is auxiliary only.
    static let chewEventThreshold: Double = {event_threshold}
    static let minGapCoverage: Double = {min_gap_coverage}
    static let eventRefractorySec: Double = {refractory}
    static let confidentEventThreshold: Double = {confident_event}

    // ------------------------------------------------------------------
    // Legacy chewing-bout segmentation (never interpreted as food entry)
    // ------------------------------------------------------------------

    /// Compatibility-only bandpower counter; never drives coaching.
    static let legacyHeuristicMinChewPeriodSec: Double = 0.35

    /// Two chewing episodes whose gap is less than this are merged into one
    /// diagnostic chewing bout (seconds).
    static let boutMergeGapSec: Double = {bout_gap}

    /// Episodes shorter than this are dropped as noise (seconds).
    static let minBoutDurationSec: Double = {min_bout_duration}
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
