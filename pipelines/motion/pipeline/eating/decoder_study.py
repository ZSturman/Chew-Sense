"""Bout-decoder settings: chosen on development data, confirmed once on the locked test.

No model is retrained. A setting is studied on a run's development
out-of-fold predictions, written down (pre-registered) before any test result
is seen, and then confirmed once on the saved test predictions of the
test-evaluated run that Live's exported detector came from. Every confirmation
is recorded as a locked-test consultation in ``index.json``.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from . import evaluate as ev
from .dataset import EatingSession

DEV, TEST = "dev", "test"


def _decode_split(frame: pd.DataFrame, split: str, column: str, config: ev.DecoderConfig,
                  sessions: Mapping[str, EatingSession]) -> dict[str, Any]:
    rows = []
    part = frame[(frame["split"] == split) & frame[column].notna()]
    for session_id, group in part.groupby("session_id"):
        if session_id not in sessions:
            continue
        group = group.sort_values("t_end")
        rows.append(ev.bout_metrics(sessions[session_id], group["t_end"].to_numpy(float),
                                    group[column].to_numpy(float), config))
    if not rows:
        return {"recordings": 0}
    result = ev.aggregate_bouts(rows)
    return {
        "recordings": len(rows),
        **{k: (None if v is None else round(float(v), 4)) for k, v in result.items()
           if isinstance(v, (int, float, np.floating)) or v is None},
    }


def _config(threshold: float, min_start_windows: int) -> ev.DecoderConfig:
    return dataclasses.replace(ev.DecoderConfig.from_threshold(threshold), min_start_windows=min_start_windows)


def development_grid(run_dir: Path, sessions: Sequence[EatingSession],
                     min_start_options: Sequence[int] = (3, 4, 5, 6, 8)) -> dict[str, Any]:
    """Score decoder persistence settings on development out-of-fold predictions only."""

    metrics = json.loads((Path(run_dir) / "metrics.json").read_text())
    selected = metrics["selected_model"]
    threshold = float(metrics["results"][selected]["threshold"])
    frame = pd.read_csv(Path(run_dir) / "predictions.csv.gz")
    by_id = {s.session_id: s for s in sessions}
    rows = [{"min_start_windows": n, **_decode_split(frame, DEV, f"p_{selected}", _config(threshold, n), by_id)}
            for n in min_start_options]
    return {"run_id": metrics["run_id"], "selected_model": selected, "threshold": threshold,
            "split": DEV, "stride_s": (metrics.get("config") or {}).get("stride_s"), "settings": rows}


def confirm_on_test(test_run_dir: Path, sessions: Sequence[EatingSession], preregistration: Mapping[str, Any]
                    ) -> dict[str, Any]:
    """Score the current and the pre-registered decoder once on saved locked-test predictions."""

    metrics = json.loads((Path(test_run_dir) / "metrics.json").read_text())
    selected = metrics["selected_model"]
    threshold = float(metrics["results"][selected]["threshold"])
    frame = pd.read_csv(Path(test_run_dir) / "predictions.csv.gz")
    by_id = {s.session_id: s for s in sessions}
    current = int(preregistration["current_min_start_windows"])
    candidate = int(preregistration["candidate_min_start_windows"])
    return {
        "test_run_id": metrics["run_id"],
        "selected_model": selected,
        "threshold": threshold,
        "current": {"min_start_windows": current,
                    **_decode_split(frame, TEST, f"p_{selected}", _config(threshold, current), by_id)},
        "candidate": {"min_start_windows": candidate,
                      **_decode_split(frame, TEST, f"p_{selected}", _config(threshold, candidate), by_id)},
    }


def record_consultation(eating_root: Path, entry: Mapping[str, Any]) -> int:
    """Append a non-run test consultation and return the new total."""

    path = Path(eating_root) / "index.json"
    index = json.loads(path.read_text())
    index.setdefault("other_test_consultations", []).append(dict(entry))
    index["test_consultations"] = (sum(1 for run in index.get("runs") or [] if run.get("test_evaluated", True))
                                   + len(index["other_test_consultations"]))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2, default=float) + "\n")
    tmp.rename(path)
    return index["test_consultations"]


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")
