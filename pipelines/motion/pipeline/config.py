"""
Pipeline configuration management.

Loads defaults from config/defaults.yaml and allows CLI/programmatic overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class AugmentationConfig:
    """Per-axis raw-window augmentation (only used when input_mode includes raw)."""
    enabled: bool = False
    noise_std: float = 0.01      # Gaussian noise std (in g / rad/s)
    rotation_deg: float = 8.0    # max random rotation around random axis
    time_shift_frac: float = 0.05  # max fraction of window to shift
    channel_dropout_p: float = 0.0  # probability of zeroing one channel


@dataclass
class TrainingConfig:
    batch_size: int = 256
    lr: float = 0.001
    epochs: int = 50
    val_ratio: float = 0.2
    hidden_dim: int = 32
    patience: int = 5
    # Imbalance handling
    loss: str = "bce"            # "bce" | "focal"
    pos_weight: str = "auto"     # "auto" (n_neg/n_pos), "none", or float string e.g. "2.5"
    use_weighted_sampler: bool = True
    focal_alpha: float = 0.25
    focal_gamma: float = 2.0
    # Architecture
    # ``mlp`` is retained only for legacy binary eating checkpoints.  New
    # individual-chew training uses ``causal_tcn`` with raw temporal windows.
    arch: str = "causal_tcn"     # "causal_tcn" | "mlp" (legacy)
    # Split policy
    allow_unsplit: bool = False  # If True, fall back to session-aware random split
    # Feature-level augmentation (cheap noise on the 12-dim vector)
    feature_noise_std: float = 0.0
    # Raw-window augmentation (only if features include raw windows; future phase)
    augmentation: AugmentationConfig = field(default_factory=AugmentationConfig)


@dataclass
class FeaturesConfig:
    window_sec: float = 0.75
    step_sec: float = 0.10


@dataclass
class TemporalModelConfig:
    """CoreML-compatible v2 causal TCN and supervision weights."""

    enabled: bool = True
    sample_rate_hz: int = 50
    context_sec: float = 3.0
    inference_step_sec: float = 0.10
    channels: list[int] = field(default_factory=lambda: [24, 24, 32, 32])
    kernel_size: int = 5
    dropout: float = 0.10
    eating_weight: float = 1.0
    event_weight: float = 2.0
    phase_weight: float = 0.5
    confidence_weight: float = 0.25


@dataclass
class SoftLabelsConfig:
    transition_sec: float = 0.6
    long_chew_sec: float = 3.0
    long_ramp_sec: float = 1.5


@dataclass
class EvaluationConfig:
    chew_threshold: float = 0.5
    pred_threshold: float = 0.5


@dataclass
class RuntimeConfig:
    """Runtime inference parameters for the iOS/macOS app."""
    alpha: float = 0.4
    high_threshold: float = 0.6
    low_threshold: float = 0.4
    min_start_windows: int = 3
    min_end_windows: int = 2

    # Temporal event-head decoder. Phase never drives counting.
    chew_event_threshold: float = 0.5
    min_gap_coverage: float = 0.8
    event_refractory_sec: float = 0.25
    confident_event_threshold: float = 0.5

    # Diagnostic eating-bout telemetry. These values never define food entry.
    bout_merge_gap_sec: float = 1.5
    min_bout_duration_sec: float = 1.0


@dataclass
class CollectionTargetConfig:
    """Target coverage for one RealWorldTesting activity preset."""
    activity: str
    activity_label: str
    expected_label_class: str
    target_minutes: float
    aliases: list[str] = field(default_factory=list)


@dataclass
class CollectionConfig:
    """Heuristic active-learning recommendation settings."""
    max_recommendations: int = 5
    high_error_rate: float = 0.35
    moderate_error_rate: float = 0.15
    coverage_min_minutes: int = 3
    coverage_max_minutes: int = 8
    high_error_minutes: int = 8
    moderate_error_minutes: int = 6
    targets: list[CollectionTargetConfig] = field(default_factory=list)


@dataclass
class WandbConfig:
    project: str = "chewsense"
    entity: Optional[str] = None
    enabled: bool = True


@dataclass
class PipelineConfig:
    """Complete pipeline configuration."""
    training: TrainingConfig = field(default_factory=TrainingConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    soft_labels: SoftLabelsConfig = field(default_factory=SoftLabelsConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    temporal_model: TemporalModelConfig = field(default_factory=TemporalModelConfig)
    wandb: WandbConfig = field(default_factory=WandbConfig)
    
    # Paths (set at runtime)
    input_dir: Optional[Path] = None
    output_dir: Optional[Path] = None
    model_path: Optional[Path] = None
    
    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary for W&B logging."""
        return {
            "training": {
                "batch_size": self.training.batch_size,
                "lr": self.training.lr,
                "epochs": self.training.epochs,
                "val_ratio": self.training.val_ratio,
                "hidden_dim": self.training.hidden_dim,
                "patience": self.training.patience,
                "loss": self.training.loss,
                "pos_weight": self.training.pos_weight,
                "use_weighted_sampler": self.training.use_weighted_sampler,
                "focal_alpha": self.training.focal_alpha,
                "focal_gamma": self.training.focal_gamma,
                "arch": self.training.arch,
                "allow_unsplit": self.training.allow_unsplit,
                "feature_noise_std": self.training.feature_noise_std,
                "augmentation": {
                    "enabled": self.training.augmentation.enabled,
                    "noise_std": self.training.augmentation.noise_std,
                    "rotation_deg": self.training.augmentation.rotation_deg,
                    "time_shift_frac": self.training.augmentation.time_shift_frac,
                    "channel_dropout_p": self.training.augmentation.channel_dropout_p,
                },
            },
            "features": {
                "window_sec": self.features.window_sec,
                "step_sec": self.features.step_sec,
            },
            "soft_labels": {
                "transition_sec": self.soft_labels.transition_sec,
                "long_chew_sec": self.soft_labels.long_chew_sec,
                "long_ramp_sec": self.soft_labels.long_ramp_sec,
            },
            "evaluation": {
                "chew_threshold": self.evaluation.chew_threshold,
                "pred_threshold": self.evaluation.pred_threshold,
            },
            "runtime": {
                "alpha": self.runtime.alpha,
                "high_threshold": self.runtime.high_threshold,
                "low_threshold": self.runtime.low_threshold,
                "min_start_windows": self.runtime.min_start_windows,
                "min_end_windows": self.runtime.min_end_windows,
                "chew_event_threshold": self.runtime.chew_event_threshold,
                "min_gap_coverage": self.runtime.min_gap_coverage,
                "event_refractory_sec": self.runtime.event_refractory_sec,
                "confident_event_threshold": self.runtime.confident_event_threshold,
                "bout_merge_gap_sec": self.runtime.bout_merge_gap_sec,
                "min_bout_duration_sec": self.runtime.min_bout_duration_sec,
            },
            "collection": {
                "max_recommendations": self.collection.max_recommendations,
                "high_error_rate": self.collection.high_error_rate,
                "moderate_error_rate": self.collection.moderate_error_rate,
                "coverage_min_minutes": self.collection.coverage_min_minutes,
                "coverage_max_minutes": self.collection.coverage_max_minutes,
                "high_error_minutes": self.collection.high_error_minutes,
                "moderate_error_minutes": self.collection.moderate_error_minutes,
                "targets": [
                    {
                        "activity": target.activity,
                        "activity_label": target.activity_label,
                        "expected_label_class": target.expected_label_class,
                        "target_minutes": target.target_minutes,
                        "aliases": target.aliases,
                    }
                    for target in self.collection.targets
                ],
            },
            "temporal_model": {
                "enabled": self.temporal_model.enabled,
                "sample_rate_hz": self.temporal_model.sample_rate_hz,
                "context_sec": self.temporal_model.context_sec,
                "inference_step_sec": self.temporal_model.inference_step_sec,
                "channels": self.temporal_model.channels,
                "kernel_size": self.temporal_model.kernel_size,
                "dropout": self.temporal_model.dropout,
                "eating_weight": self.temporal_model.eating_weight,
                "event_weight": self.temporal_model.event_weight,
                "phase_weight": self.temporal_model.phase_weight,
                "confidence_weight": self.temporal_model.confidence_weight,
            },
        }


def load_config(config_path: Optional[Path] = None) -> PipelineConfig:
    """
    Load pipeline configuration.
    
    Args:
        config_path: Path to YAML config file. If None, loads defaults.yaml
                     from the config/ directory.
    
    Returns:
        PipelineConfig with all parameters loaded.
    """
    if config_path is None:
        # Find defaults.yaml relative to this file
        pipeline_dir = Path(__file__).parent
        config_path = pipeline_dir.parent / "config" / "defaults.yaml"
    
    config_path = Path(config_path)
    
    if not config_path.exists():
        # Return defaults if no config file
        return PipelineConfig()
    
    with open(config_path, "r") as f:
        data = yaml.safe_load(f) or {}
    
    # Build config from YAML
    training = TrainingConfig(**{k: v for k, v in data.get("training", {}).items() if k != "augmentation"})
    aug_data = data.get("training", {}).get("augmentation", {})
    if aug_data:
        training.augmentation = AugmentationConfig(**aug_data)
    features = FeaturesConfig(**data.get("features", {}))
    soft_labels = SoftLabelsConfig(**data.get("soft_labels", {}))
    evaluation = EvaluationConfig(**data.get("evaluation", {}))
    runtime = RuntimeConfig(**data.get("runtime", {}))
    collection_data = data.get("collection", {})
    collection_targets = [
        CollectionTargetConfig(**target)
        for target in collection_data.get("targets", [])
    ]
    collection_kwargs = {
        k: v for k, v in collection_data.items()
        if k != "targets"
    }
    collection = CollectionConfig(**collection_kwargs)
    collection.targets = collection_targets
    wandb = WandbConfig(**data.get("wandb", {}))

    temporal_model = TemporalModelConfig(**data.get("temporal_model", {}))

    return PipelineConfig(
        training=training,
        features=features,
        soft_labels=soft_labels,
        evaluation=evaluation,
        runtime=runtime,
        collection=collection,
        temporal_model=temporal_model,
        wandb=wandb,
    )


def merge_cli_args(config: PipelineConfig, args: dict[str, Any]) -> PipelineConfig:
    """
    Merge CLI arguments into config, overriding YAML values.
    
    Args:
        config: Base configuration
        args: Dictionary of CLI arguments (from argparse.Namespace)
    
    Returns:
        Updated PipelineConfig
    """
    # Training overrides
    if "batch_size" in args and args["batch_size"] is not None:
        config.training.batch_size = args["batch_size"]
    if "lr" in args and args["lr"] is not None:
        config.training.lr = args["lr"]
    if "epochs" in args and args["epochs"] is not None:
        config.training.epochs = args["epochs"]
    if "val_ratio" in args and args["val_ratio"] is not None:
        config.training.val_ratio = args["val_ratio"]
    if "hidden_dim" in args and args["hidden_dim"] is not None:
        config.training.hidden_dim = args["hidden_dim"]
    if "patience" in args and args["patience"] is not None:
        config.training.patience = args["patience"]
    if "loss" in args and args["loss"] is not None:
        config.training.loss = args["loss"]
    if "pos_weight" in args and args["pos_weight"] is not None:
        config.training.pos_weight = str(args["pos_weight"])
    if "use_weighted_sampler" in args and args["use_weighted_sampler"] is not None:
        config.training.use_weighted_sampler = bool(args["use_weighted_sampler"])
    if "arch" in args and args["arch"] is not None:
        config.training.arch = args["arch"]
    if "allow_unsplit" in args and args["allow_unsplit"] is not None:
        config.training.allow_unsplit = bool(args["allow_unsplit"])
    if "feature_noise_std" in args and args["feature_noise_std"] is not None:
        config.training.feature_noise_std = float(args["feature_noise_std"])
    
    # Feature overrides
    if "window_sec" in args and args["window_sec"] is not None:
        config.features.window_sec = args["window_sec"]
    if "step_sec" in args and args["step_sec"] is not None:
        config.features.step_sec = args["step_sec"]
    
    # Soft label overrides
    if "transition_sec" in args and args["transition_sec"] is not None:
        config.soft_labels.transition_sec = args["transition_sec"]
    if "long_chew_sec" in args and args["long_chew_sec"] is not None:
        config.soft_labels.long_chew_sec = args["long_chew_sec"]
    if "long_ramp_sec" in args and args["long_ramp_sec"] is not None:
        config.soft_labels.long_ramp_sec = args["long_ramp_sec"]
    
    # Runtime overrides
    if "alpha" in args and args["alpha"] is not None:
        config.runtime.alpha = args["alpha"]
    if "high_threshold" in args and args["high_threshold"] is not None:
        config.runtime.high_threshold = args["high_threshold"]
    if "low_threshold" in args and args["low_threshold"] is not None:
        config.runtime.low_threshold = args["low_threshold"]
    if "min_start_windows" in args and args["min_start_windows"] is not None:
        config.runtime.min_start_windows = args["min_start_windows"]
    if "min_end_windows" in args and args["min_end_windows"] is not None:
        config.runtime.min_end_windows = args["min_end_windows"]
    
    return config
