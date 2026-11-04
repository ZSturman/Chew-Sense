"""Candidate chewing-bout detectors, simplest first.

``prior``          constant training positive rate (the no-skill reference).
``motion_energy``  logistic regression on gyro RMS only.  If this matches the
                   richer models, they are mostly detecting "moving more".
``logreg``         L2 logistic regression on all interpretable features.
``hgb``            histogram gradient boosting on the same features.
``tcn``            the existing causal ``TemporalChewNet`` trained on the
                   eating head only, from raw 7x150 windows.  Its event and
                   phase heads receive no supervision here and must not be
                   used.  It is the only candidate already compatible with
                   the Live CoreML runtime contract.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..temporal import CONTEXT_SAMPLES, TemporalChewNet
from .features import ENERGY_FEATURE, feature_names


MODEL_NAMES = ("prior", "motion_energy", "logreg", "hgb", "tcn")
FEATURE_MODELS = {"prior", "motion_energy", "logreg", "hgb"}


class PriorModel:
    def fit(self, features: np.ndarray, y: np.ndarray) -> "PriorModel":
        self.rate_ = float(np.mean(y))
        return self

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        return np.full(len(features), self.rate_, dtype=np.float64)


class SklearnModel:
    def __init__(self, name: str, seed: int = 0) -> None:
        self.name = name
        names = feature_names()
        self.columns = [names.index(ENERGY_FEATURE)] if name == "motion_energy" else list(range(len(names)))
        if name in {"motion_energy", "logreg"}:
            self.estimator = make_pipeline(
                StandardScaler(),
                LogisticRegression(C=0.5, class_weight="balanced", max_iter=5000),
            )
        elif name == "hgb":
            self.estimator = HistGradientBoostingClassifier(
                learning_rate=0.05,
                max_iter=250,
                max_leaf_nodes=15,
                min_samples_leaf=40,
                l2_regularization=1.0,
                class_weight="balanced",
                random_state=seed,
            )
        else:
            raise ValueError(f"unknown feature model {name}")

    def fit(self, features: np.ndarray, y: np.ndarray) -> "SklearnModel":
        self.estimator.fit(features[:, self.columns], y)
        return self

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        return self.estimator.predict_proba(features[:, self.columns])[:, 1]

    def explain(self) -> list[dict[str, Any]]:
        """Standardized coefficients for linear models (largest magnitude first)."""

        if self.name not in {"motion_energy", "logreg"}:
            return []
        names = [feature_names()[i] for i in self.columns]
        coefficients = self.estimator[-1].coef_[0]
        order = np.argsort(-np.abs(coefficients))
        return [{"feature": names[i], "coefficient": float(coefficients[i])} for i in order]


@dataclass
class TCNEatingModel:
    """TemporalChewNet with only the eating head trained (coarse supervision)."""

    epochs: int = 12
    batch_size: int = 128
    learning_rate: float = 1e-3
    seed: int = 0
    history: list[dict[str, float]] = field(default_factory=list)

    def _normalize(self, windows: np.ndarray) -> torch.Tensor:
        result = windows.astype(np.float32, copy=True)
        result[:, :6, :] = (result[:, :6, :] - self.mean_[None, :, None]) / self.std_[None, :, None]
        result[:, :6, :] *= result[:, 6:7, :]
        return torch.from_numpy(result)

    def fit(
        self,
        windows: np.ndarray,
        y: np.ndarray,
        *,
        monitor: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> "TCNEatingModel":
        """Train for a fixed number of epochs.

        ``monitor`` (held-out windows and labels) is only *logged* per epoch
        for loss curves; it never drives early stopping or model selection,
        because in cross-validation it is the evaluation fold.
        """

        if windows.shape[2] != CONTEXT_SAMPLES:
            raise ValueError("the TCN contract requires 3-second (150-sample) windows")
        torch.manual_seed(self.seed)
        rng = np.random.default_rng(self.seed)
        valid = windows[:, 6, :] >= 0.5
        self.mean_ = np.array([windows[:, c, :][valid].mean() for c in range(6)], dtype=np.float32)
        std = np.array([windows[:, c, :][valid].std() for c in range(6)], dtype=np.float32)
        self.std_ = np.where(std > 1e-8, std, 1.0).astype(np.float32)
        x = self._normalize(windows)
        target = torch.from_numpy(y.astype(np.float32))
        positives = max(float(y.sum()), 1.0)
        pos_weight = torch.tensor((len(y) - positives) / positives)
        self.model_ = TemporalChewNet()
        optimizer = torch.optim.Adam(self.model_.parameters(), lr=self.learning_rate)
        monitor_x = self._normalize(monitor[0]) if monitor is not None else None
        monitor_y = torch.from_numpy(monitor[1].astype(np.float32)) if monitor is not None else None
        self.history = []
        for epoch in range(1, self.epochs + 1):
            self.model_.train()
            order = rng.permutation(len(x))
            total = 0.0
            for start in range(0, len(order), self.batch_size):
                batch = torch.from_numpy(order[start : start + self.batch_size])
                optimizer.zero_grad()
                logits = self.model_(x[batch])[:, 0]
                loss = F.binary_cross_entropy_with_logits(logits, target[batch], pos_weight=pos_weight)
                loss.backward()
                optimizer.step()
                total += float(loss.detach()) * len(batch)
            record = {"epoch": epoch, "train_loss": total / len(x)}
            if monitor_x is not None and len(monitor_x):
                self.model_.eval()
                with torch.no_grad():
                    logits = self._logits(monitor_x)
                    record["heldout_loss"] = float(
                        F.binary_cross_entropy_with_logits(logits, monitor_y, pos_weight=pos_weight)
                    )
            self.history.append(record)
        self.model_.eval()
        return self

    def _logits(self, x: torch.Tensor) -> torch.Tensor:
        outputs = [self.model_(x[i : i + 1024])[:, 0] for i in range(0, len(x), 1024)]
        return torch.cat(outputs) if outputs else torch.zeros(0)

    def predict_proba(self, windows: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            return torch.sigmoid(self._logits(self._normalize(windows))).numpy().astype(np.float64)

    def checkpoint(self) -> dict[str, Any]:
        return {
            "mode": "temporal_tcn_eating_only",
            "warning": "event and phase heads are untrained; use eating_logit only",
            "model_state_dict": self.model_.state_dict(),
            "mean": self.mean_,
            "std": self.std_,
            "history": self.history,
        }


def make_model(name: str, *, seed: int = 0, tcn_epochs: int = 12):
    if name == "prior":
        return PriorModel()
    if name in {"motion_energy", "logreg", "hgb"}:
        return SklearnModel(name, seed=seed)
    if name == "tcn":
        return TCNEatingModel(epochs=tcn_epochs, seed=seed)
    raise ValueError(f"unknown model {name}")


def fit_model(model, name: str, features: np.ndarray, windows: np.ndarray, y: np.ndarray, monitor=None):
    if name == "tcn":
        return model.fit(windows, y, monitor=monitor)
    return model.fit(features, y)


def predict(model, name: str, features: np.ndarray, windows: np.ndarray) -> np.ndarray:
    return model.predict_proba(windows if name == "tcn" else features)


def inference_latency_ms(model, name: str, features: np.ndarray, windows: np.ndarray, repeats: int = 200) -> float:
    """Median single-window latency on this machine, feature time excluded."""

    if not len(features):
        return float("nan")
    samples = []
    for i in range(min(repeats, len(features))):
        start = time.perf_counter()
        predict(model, name, features[i : i + 1], windows[i : i + 1])
        samples.append((time.perf_counter() - start) * 1000.0)
    return float(np.median(samples))
