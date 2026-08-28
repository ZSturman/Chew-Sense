from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from chewsense_motion import (
    _file_sha256,
    record_registry_evaluation,
    verify_registry_evaluation_inputs,
)


def _lineage_fixture(root: Path) -> tuple[Path, Path, Path]:
    entry = root / "registry" / "model-v2"
    features = root / "features"
    entry.mkdir(parents=True)
    features.mkdir()

    np.save(features / "X.npy", np.zeros((2, 7, 150), dtype=np.float32))
    np.save(features / "y.npy", np.zeros((2, 8), dtype=np.float32))
    dataset = {
        "schema_version": "2.0.0",
        "artifact_type": "temporal_motion_dataset",
        "dataset_hash": "dataset-exact",
        "arrays": {
            name: {"sha256": _file_sha256(features / name)}
            for name in ("X.npy", "y.npy")
        },
    }
    (features / "dataset_manifest.json").write_text(json.dumps(dataset))
    (entry / "dataset_manifest.json").write_text(json.dumps(dataset))
    (entry / "metadata.json").write_text(
        json.dumps({"dataset_hash": "dataset-exact"})
    )
    torch.save(
        {
            "mode": "temporal_tcn",
            "temporal_contract_version": "2.0.0",
            "dataset_hash": "dataset-exact",
        },
        entry / "chewnet.pth",
    )
    split = {
        "version": 2,
        "dataset_hash": "dataset-exact",
        "splits": {
            "train": ["train"],
            "validation_locked": ["validation"],
            "test_locked": ["test"],
        },
    }
    split_path = entry / "split_manifest.json"
    split_path.write_text(json.dumps(split))
    return entry, features, split_path


def test_registry_evaluation_requires_exact_arrays_and_splits(tmp_path: Path):
    entry, features, split = _lineage_fixture(tmp_path)

    verify_registry_evaluation_inputs(entry, features, split)

    np.save(features / "X.npy", np.ones((2, 7, 150), dtype=np.float32))
    with pytest.raises(ValueError, match="array hash mismatch"):
        verify_registry_evaluation_inputs(entry, features, split)


def test_registry_metrics_are_merged_by_split(tmp_path: Path):
    entry, _, _ = _lineage_fixture(tmp_path)
    (entry / "metrics.json").write_text(
        json.dumps({"validation_locked": {"recall": 0.91}})
    )

    path = record_registry_evaluation(
        entry,
        "test_locked",
        {"split": "test_locked", "chew_f1_at_250ms": 0.84},
    )

    payload = json.loads(path.read_text())
    assert payload["validation_locked"]["recall"] == 0.91
    assert payload["test_locked"]["chew_f1_at_250ms"] == 0.84
    assert not (entry / ".metrics.json.tmp").exists()
