"""Session-based split manifest helpers.

Locked split manifests keep validation and test sessions stable while new
reviewed data is added to training by default.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from .ingest import SessionInfo


SPLIT_VERSION = 1


def session_id_from_path(path: Path) -> str:
    """Return the stable session id used across raw and transformed files."""
    stem = Path(path).stem
    if stem.endswith("_transformed"):
        stem = stem[: -len("_transformed")]
    return stem


def session_id_from_info(session: SessionInfo) -> str:
    return session_id_from_path(session.csv_path)


def _session_time(info: SessionInfo) -> tuple[float, str]:
    """Best-effort chronological key without changing existing assignments."""
    session_id = session_id_from_info(info)
    manifest_path = info.path / "session.json"
    if manifest_path.exists():
        try:
            created_at = json.loads(manifest_path.read_text()).get("created_at")
            if created_at:
                parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                return parsed.timestamp(), session_id
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    match = re.search(r"(\d{8})-(\d{6})", session_id)
    if match:
        try:
            parsed = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
            return parsed.replace(tzinfo=timezone.utc).timestamp(), session_id
        except ValueError:
            pass
    try:
        return info.path.stat().st_mtime, session_id
    except OSError:
        return 0.0, session_id


def _chronological_order(sessions: Iterable[SessionInfo]) -> list[str]:
    return [
        session_id_from_info(info)
        for info in sorted(sessions, key=_session_time)
    ]


def load_split_manifest(path: Path) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def save_split_manifest(manifest: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    return path


def create_or_update_split_manifest(
    sessions: list[SessionInfo],
    output_path: Path,
    validation_ratio: float = 0.2,
    test_ratio: float = 0.1,
    field_regression_session_ids: Iterable[str] | None = None,
    gold_session_ids: Iterable[str] | None = None,
    require_verified_for_locked: bool = False,
) -> dict:
    """Create or update a locked split manifest.

    Existing validation/test/gold assignments are preserved. New sessions
    default to train so routine field-test imports do not churn validation
    metrics. `gold_locked` membership is **never** auto-assigned and only
    grows via `gold_session_ids` (which must already be present in `sessions`).

    When ``require_verified_for_locked`` is true and a verified subset exists,
    only sessions with ``SessionInfo.verified == True`` are eligible for the
    initial validation_locked/test_locked pools. This keeps Phase-2 unreviewed
    sessions out of evaluation while still letting them flow into training.
    """
    output_path = Path(output_path)
    current_ids = {session_id_from_info(s) for s in sessions}
    verified_ids = {
        session_id_from_info(s)
        for s in sessions
        if getattr(s, "verified", False)
    }
    now = datetime.now(timezone.utc).isoformat()

    if output_path.exists():
        manifest = load_split_manifest(output_path)
        splits = manifest.setdefault("splits", {})
        train = set(splits.get("train", [])) & current_ids
        validation = set(splits.get("validation_locked", [])) & current_ids
        test = set(splits.get("test_locked", [])) & current_ids
        gold = set(splits.get("gold_locked", [])) & current_ids
        assigned = train | validation | test  # gold can overlap train deliberately
        train |= current_ids - assigned
        if current_ids and (not validation or not test) and len(current_ids) >= 3:
            chronological = [
                sid for sid in _chronological_order(sessions) if sid in train
            ]
            if not test:
                n_test = max(1, int(round(len(current_ids) * test_ratio)))
                test = set(chronological[-n_test:])
                train -= test
                chronological = [sid for sid in chronological if sid not in test]
            if not validation:
                n_val = max(1, int(round(len(current_ids) * validation_ratio)))
                validation = set(chronological[-n_val:])
                train -= validation
        manifest["updated_at"] = now
    else:
        ordered = _chronological_order(sessions)
        n_total = len(ordered)
        eligible = (
            [sid for sid in ordered if sid in verified_ids]
            if require_verified_for_locked and verified_ids
            else list(ordered)
        )
        if not eligible:
            eligible = list(ordered)
        n_test = max(1, int(round(n_total * test_ratio))) if n_total >= 3 and len(eligible) >= 2 else 0
        n_val = max(1, int(round(n_total * validation_ratio))) if n_total >= 3 and len(eligible) >= 2 else 0
        test = set(eligible[-n_test:]) if n_test else set()
        validation_end = len(eligible) - n_test
        validation_start = max(0, validation_end - n_val)
        validation = set(eligible[validation_start:validation_end])
        train = set(ordered) - test - validation
        gold = set()
        manifest = {
            "version": SPLIT_VERSION,
            "created_at": now,
            "updated_at": now,
            "policy": {
                "unit": "session",
                "ordering": "chronological",
                "holdout_policy": "newest_sessions_locked",
                "new_sessions_default": "train",
                "validation_ratio_on_first_create": validation_ratio,
                "test_ratio_on_first_create": test_ratio,
                "gold_locked_assignment": "explicit_only",
                "require_verified_for_locked": require_verified_for_locked,
            },
        }

    field_regression = set(
        manifest.get("splits", {}).get("field_regression", [])
    ) & current_ids
    if field_regression_session_ids:
        field_regression |= set(field_regression_session_ids) & current_ids

    if gold_session_ids:
        # Gold is monotonically additive — never reassigned away.
        gold |= set(gold_session_ids) & current_ids

    manifest["splits"] = {
        "train": sorted(train),
        "validation_locked": sorted(validation),
        "test_locked": sorted(test),
        "field_regression": sorted(field_regression),
        "gold_locked": sorted(gold),
    }
    save_split_manifest(manifest, output_path)
    return manifest


def indices_for_split(
    features_dir: Path,
    split_manifest_path: Path | None,
    split_name: str,
) -> np.ndarray | None:
    """Return feature-row indices for a split, or None when unavailable."""
    if split_name == "all":
        return None
    if split_manifest_path is None:
        return None

    features_dir = Path(features_dir)
    session_ids_path = features_dir / "session_ids.npy"
    if not session_ids_path.exists() or not Path(split_manifest_path).exists():
        return None

    session_ids = np.load(session_ids_path, allow_pickle=True).astype(str)
    manifest = load_split_manifest(Path(split_manifest_path))
    wanted = set(manifest.get("splits", {}).get(split_name, []))
    if not wanted:
        return np.array([], dtype=int)

    return np.flatnonzero(np.isin(session_ids, list(wanted)))
