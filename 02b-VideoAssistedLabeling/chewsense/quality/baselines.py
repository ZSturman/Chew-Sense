"""Per-user rolling baselines for chew metrics.

A `Baseline` is the user's personal "what is normal for them" record. We
update it after every successful run and use it to make Phase-2 quality flags
personalized rather than absolute.

Stored on disk at:
    $CHEWSENSE_BASELINES_DIR/<user>.json
    (default: ~/.config/chewsense/baselines/<user>.json)

Welford's online algorithm gives mean/std without storing every sample, but we
also keep the last N session aggregates so the baseline can be recomputed if
the schema changes.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path


def _baselines_dir() -> Path:
    env = os.environ.get("CHEWSENSE_BASELINES_DIR")
    if env:
        return Path(env)
    return Path.home() / ".config" / "chewsense" / "baselines"


@dataclass
class _SessionAgg:
    mean_chew_rate_hz: float | None
    rhythm_cv: float | None
    mean_chews_per_bite: float | None
    bite_size_max: float
    n_chews: int


@dataclass
class Baseline:
    user_id: str
    n_sessions: int = 0
    chew_rate_hz_mean: float = 0.0
    chew_rate_hz_std: float = 0.0
    rhythm_cv_mean: float = 0.0
    chews_per_bite_mean: float = 0.0
    bite_size_max: float = 0.0  # ever-observed max
    history: list[dict] = field(default_factory=list)

    def is_ready(self, min_sessions: int) -> bool:
        return self.n_sessions >= min_sessions

    @classmethod
    def load_or_new(cls, user_id: str) -> "Baseline":
        path = _baselines_dir() / f"{user_id}.json"
        if not path.exists():
            return cls(user_id=user_id)
        try:
            data = json.loads(path.read_text())
        except Exception:
            return cls(user_id=user_id)
        return cls(
            user_id=data.get("user_id", user_id),
            n_sessions=int(data.get("n_sessions", 0)),
            chew_rate_hz_mean=float(data.get("chew_rate_hz_mean", 0.0)),
            chew_rate_hz_std=float(data.get("chew_rate_hz_std", 0.0)),
            rhythm_cv_mean=float(data.get("rhythm_cv_mean", 0.0)),
            chews_per_bite_mean=float(data.get("chews_per_bite_mean", 0.0)),
            bite_size_max=float(data.get("bite_size_max", 0.0)),
            history=list(data.get("history", [])),
        )

    def save(self) -> Path:
        d = _baselines_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{self.user_id}.json"
        path.write_text(json.dumps({
            "user_id": self.user_id,
            "n_sessions": self.n_sessions,
            "chew_rate_hz_mean": self.chew_rate_hz_mean,
            "chew_rate_hz_std": self.chew_rate_hz_std,
            "rhythm_cv_mean": self.rhythm_cv_mean,
            "chews_per_bite_mean": self.chews_per_bite_mean,
            "bite_size_max": self.bite_size_max,
            "history": self.history,
        }, indent=2))
        return path

    def update_from_session(self, agg: _SessionAgg, rolling_max_sessions: int) -> None:
        # Keep raw history (rolling)
        self.history.append({
            "mean_chew_rate_hz": agg.mean_chew_rate_hz,
            "rhythm_cv": agg.rhythm_cv,
            "mean_chews_per_bite": agg.mean_chews_per_bite,
            "bite_size_max": agg.bite_size_max,
            "n_chews": agg.n_chews,
        })
        if len(self.history) > rolling_max_sessions:
            self.history = self.history[-rolling_max_sessions:]

        rates = [h["mean_chew_rate_hz"] for h in self.history if h["mean_chew_rate_hz"]]
        cvs = [h["rhythm_cv"] for h in self.history if h["rhythm_cv"]]
        cpbs = [h["mean_chews_per_bite"] for h in self.history if h["mean_chews_per_bite"]]
        bites = [h["bite_size_max"] for h in self.history if h.get("bite_size_max")]

        self.n_sessions = len(self.history)
        if rates:
            m = sum(rates) / len(rates)
            v = sum((r - m) ** 2 for r in rates) / max(1, len(rates) - 1)
            self.chew_rate_hz_mean = m
            self.chew_rate_hz_std = math.sqrt(v)
        if cvs:
            self.rhythm_cv_mean = sum(cvs) / len(cvs)
        if cpbs:
            self.chews_per_bite_mean = sum(cpbs) / len(cpbs)
        if bites:
            self.bite_size_max = max(bites + [self.bite_size_max])


def update_baseline_from_report(
    user_id: str,
    report,                 # QualityReport
    cfg: dict,
) -> Path | None:
    if not cfg["baselines"]["enabled"]:
        return None
    bites = [eq.bite_size_norm for eq in report.episodes if eq.bite_size_norm is not None]
    agg = _SessionAgg(
        mean_chew_rate_hz=report.session.mean_chew_rate_hz,
        rhythm_cv=report.session.rhythm_cv,
        mean_chews_per_bite=report.session.mean_chews_per_bite,
        bite_size_max=max(bites) if bites else 0.0,
        n_chews=report.session.n_chews,
    )
    bl = Baseline.load_or_new(user_id)
    bl.update_from_session(agg, cfg["baselines"]["rolling_max_sessions"])
    return bl.save()
