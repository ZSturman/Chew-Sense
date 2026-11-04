from __future__ import annotations

import math
import json
import platform
import shutil
from pathlib import Path

import numpy as np
import pytest
import torch

from pipeline.temporal import (
    CONTEXT_SAMPLES,
    ChewEventDecoder,
    TemporalChewNet,
    TemporalPrediction,
    build_temporal_targets,
    chew_event_metrics,
    resample_motion_window,
    temporal_multitask_loss,
)
from pipeline.export import (
    CoreMLRuntimeUnavailable,
    export_coreml,
    export_normalization_json,
    generate_swift_constants,
    verify_temporal_coreml_parity,
)
from pipeline.temporal_dataset import (
    TemporalDatasetError,
    create_locked_temporal_splits,
    materialize_temporal_dataset,
)
from pipeline.evaluate import _evaluate_temporal
from pipeline.config import load_config
from pipeline.train import train_model
from chewsense.session_bundle import finalize_session


def _logit(probability: float) -> float:
    return math.log(probability / (1.0 - probability))


def _prediction(timestamp: float, event_probability: float, **kwargs) -> TemporalPrediction:
    return TemporalPrediction(
        timestamp=timestamp,
        eating_logit=_logit(kwargs.get("eating_probability", 0.9)),
        chew_event_logit=_logit(event_probability),
        phase_sin=0.0,
        phase_cos=1.0,
        phase_confidence=1.0,
        gap_coverage=kwargs.get("gap_coverage", 1.0),
    )


def test_temporal_model_has_fixed_coreml_contract():
    model = TemporalChewNet(channels=(8, 8), dropout=0)
    output = model(torch.randn(4, 7, CONTEXT_SAMPLES))

    assert output.shape == (4, 5)
    assert torch.all(output[:, 2:4].abs() <= 1.0)
    assert torch.all((output[:, 4] >= 0) & (output[:, 4] <= 1))


def test_legacy_or_coarse_supervision_masks_event_and_phase_losses():
    timestamps = np.arange(0, 2.01, 0.1)
    targets = build_temporal_targets(
        timestamps,
        eating_intervals=[(0.5, 1.5)],
        cycles=[
            {
                "start_closed_ts": 0.5,
                "open_peak_ts": 0.75,
                "end_closed_ts": 1.0,
            }
        ],
        supervision="coarse",
    )

    assert np.all(targets[:, 6] == 0)
    assert np.all(targets[:, 7] == 0)
    output = torch.zeros((len(timestamps), 5), requires_grad=True)
    total, parts = temporal_multitask_loss(output, torch.from_numpy(targets))
    total.backward()
    assert parts["loss_event"] == 0
    assert parts["loss_phase"] == 0


def test_reviewed_targets_use_end_closed_event_and_open_then_close_phase():
    timestamps = np.arange(0, 1.51, 0.05)
    targets = build_temporal_targets(
        timestamps,
        eating_intervals=[(0, 1.5)],
        cycles=[
            {
                "start_closed_ts": 0.5,
                "open_peak_ts": 0.75,
                "end_closed_ts": 1.0,
                "disposition": "accepted",
            }
        ],
    )
    event_peak_timestamp = timestamps[np.argmax(targets[:, 1])]
    assert event_peak_timestamp == pytest.approx(1.0)

    start = np.argmin(abs(timestamps - 0.5))
    peak = np.argmin(abs(timestamps - 0.75))
    end = np.argmin(abs(timestamps - 1.0))
    assert targets[start, 2] == pytest.approx(0, abs=1e-5)
    assert targets[start, 3] == pytest.approx(1, abs=1e-5)
    assert targets[peak, 2] == pytest.approx(0, abs=1e-5)
    assert targets[peak, 3] == pytest.approx(-1, abs=1e-5)
    assert targets[end, 2] == pytest.approx(0, abs=1e-5)
    assert targets[end, 3] == pytest.approx(1, abs=1e-5)


def test_unobservable_cycle_is_excluded_from_event_and_phase_supervision():
    timestamps = np.arange(0, 2.01, 0.1)
    targets = build_temporal_targets(
        timestamps,
        eating_intervals=[(0, 2)],
        cycles=[
            {
                "start_closed_ts": 0.5,
                "open_peak_ts": 0.75,
                "end_closed_ts": 1.0,
                "observable": False,
            }
        ],
    )
    assert np.all(targets[:, 1] == 0)
    assert np.all(targets[:, 7] == 0)


def test_resampler_emits_exact_shape_and_masks_long_gaps():
    timestamps = np.concatenate((np.arange(0, 1.0, 0.02), np.arange(1.3, 3.02, 0.02)))
    imu = np.column_stack([timestamps + channel for channel in range(6)])
    window, grid = resample_motion_window(timestamps, imu, end_timestamp=3.0)

    assert window.shape == (7, CONTEXT_SAMPLES)
    assert grid.shape == (CONTEXT_SAMPLES,)
    gap = (grid > 1.0) & (grid < 1.3)
    assert np.all(window[6, gap] == 0)
    assert np.all(window[:6, gap] == 0)
    assert window[6].mean() < 1.0


def test_event_decoder_uses_peak_eating_gate_and_250ms_refractory():
    decoder = ChewEventDecoder(event_threshold=0.5, refractory_seconds=0.25)
    sequence = [
        _prediction(0.0, 0.1),
        _prediction(0.1, 0.8),
        _prediction(0.2, 0.1),  # emits 0.1
        _prediction(0.3, 0.9),
        _prediction(0.4, 0.1),  # 0.3 is rejected: only 200 ms later
        _prediction(0.5, 0.2),
        _prediction(0.6, 0.85),
        _prediction(0.7, 0.1),  # emits 0.6
    ]
    emitted = [event for row in sequence if (event := decoder.push(row)) is not None]

    assert [event.timestamp for event in emitted] == pytest.approx([0.1, 0.6])
    assert len(decoder.raw_outputs) == len(sequence)


def test_event_decoder_rejects_invalid_or_non_eating_peaks():
    decoder = ChewEventDecoder()
    rows = [
        _prediction(0.0, 0.1),
        _prediction(0.1, 0.9, eating_probability=0.2),
        _prediction(0.2, 0.1),
        _prediction(0.3, 0.9, gap_coverage=0.4),
        _prediction(0.4, 0.1),
    ]
    assert all(decoder.push(row) is None for row in rows)


def test_chew_metrics_include_count_and_inter_chew_interval_error():
    metrics = chew_event_metrics(
        [1.0, 1.6, 2.2, 2.8],
        [1.04, 1.68, 2.28, 2.9],
    )
    assert metrics["chew_f1_at_250ms"] == pytest.approx(1.0)
    assert metrics["chew_count_error"] == 0
    assert metrics["median_inter_chew_interval_mae_sec"] == pytest.approx(0.02)


def test_temporal_checkpoint_exports_runtime_contract(tmp_path):
    model = TemporalChewNet(channels=(8,), kernel_size=3, dropout=0)
    checkpoint = tmp_path / "temporal.pth"
    torch.save(
        {
            "model_id": "fixture-temporal",
            "mode": "temporal_tcn",
            "model_state_dict": model.state_dict(),
            "mean": np.zeros(6, dtype=np.float32),
            "std": np.ones(6, dtype=np.float32),
            "input_channels": 7,
            "context_samples": 150,
            "channels": [8],
            "kernel_size": 3,
            "dropout": 0.0,
            "num_outputs": 5,
            "output_names": [
                "eating_logit",
                "chew_event_logit",
                "phase_sin",
                "phase_cos",
                "phase_confidence",
            ],
            "dataset_hash": "fixture",
            "config": {
                "temporal_model": {"sample_rate_hz": 50},
                "runtime": {
                    "chew_event_threshold": 0.55,
                    "event_refractory_sec": 0.25,
                },
            },
        },
        checkpoint,
    )

    normalization = tmp_path / "normalization.json"
    swift = tmp_path / "NormalizationConstants.swift"
    export_normalization_json(checkpoint, normalization)
    generate_swift_constants(
        checkpoint,
        swift,
        chew_event_promoted=True,
        food_entry_promoted=False,
    )

    payload = json.loads(normalization.read_text())
    assert payload["mode"] == "temporal_tcn"
    assert payload["context_samples"] == 150
    generated = swift.read_text()
    assert "CHEW_EVENT_HEAD_PROMOTED: Bool = true" in generated
    assert "FOOD_ENTRY_HEAD_PROMOTED: Bool = false" in generated
    assert "chewEventThreshold: Double = 0.55" in generated


def test_swift_python_replay_golden_contract():
    repo_root = Path(__file__).resolve().parents[3]
    fixture = json.loads(
        (
            repo_root
            / "apps/live-ios/ChewSenseLiveTests/Fixtures/temporal_replay_v2.json"
        ).read_text()
    )
    tolerance = fixture["probability_tolerance"]
    decoder = ChewEventDecoder(
        event_threshold=fixture["decoder"]["event_threshold"],
        eating_threshold=fixture["decoder"]["eating_threshold"],
        min_gap_coverage=fixture["decoder"]["min_gap_coverage"],
        refractory_seconds=fixture["decoder"]["refractory_sec"],
    )
    for row in fixture["rows"]:
        prediction = TemporalPrediction(
            timestamp=row["timestamp"],
            eating_logit=row["eating_logit"],
            chew_event_logit=row["chew_event_logit"],
            phase_sin=row["phase_sin"],
            phase_cos=row["phase_cos"],
            phase_confidence=row["phase_confidence"],
            gap_coverage=row["gap_coverage"],
        )
        assert prediction.eating_probability == pytest.approx(
            row["eating_probability"], abs=tolerance
        )
        assert prediction.chew_event_probability == pytest.approx(
            row["chew_event_probability"], abs=tolerance
        )
        decoder.push(prediction)

    actual = [event.timestamp for event in decoder.decoded_events]
    assert actual == pytest.approx(
        fixture["decoded_event_timestamps"],
        abs=fixture["inference_step_sec"],
    )


def test_finalized_golden_session_materializes_exact_temporal_contract(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    work = tmp_path / "golden"
    shutil.copytree(repo_root / "fixtures/golden/session-v2", work)
    draft_path = work / "session" / "annotations" / "review-draft.json"
    draft_payload = json.loads(draft_path.read_text())
    draft_payload["review_context"] = {"mode": "assisted", "sync_verified": True}
    draft_path.write_text(json.dumps(draft_payload))
    finalize_session(work / "session", work / "derived")

    result = materialize_temporal_dataset([work / "session"], tmp_path / "dataset")
    X = np.load(result.output_dir / "X.npy")
    y = np.load(result.output_dir / "y.npy")
    timestamps = np.load(result.output_dir / "timestamps.npy")
    session_ids = np.load(result.output_dir / "session_ids.npy", allow_pickle=True)

    assert X.shape == (33, 7, CONTEXT_SAMPLES)
    assert y.shape == (33, 8)
    assert np.all(session_ids == "golden-session-v2")
    assert result.sessions[0].event_supervision
    assert result.sessions[0].event_count == 3
    assert timestamps[np.argmax(y[:, 1])] == pytest.approx(0.6)
    assert np.all(y[(timestamps >= 1.6) & (timestamps <= 2.5), 6] == 0)
    assert json.loads(result.manifest_path.read_text())["dataset_hash"] == result.dataset_hash


def test_coarse_only_and_all_negative_sessions_never_train_event_or_phase(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    coarse = tmp_path / "coarse"
    negative = tmp_path / "negative"
    shutil.copytree(repo_root / "fixtures/golden/session-v2/session", coarse)
    shutil.copytree(repo_root / "fixtures/golden/session-v2/session", negative)
    negative_manifest = json.loads((negative / "session.json").read_text())
    negative_manifest["session_id"] = "golden-negative-v2"
    negative_manifest["activity"] = "walking"
    (negative / "session.json").write_text(json.dumps(negative_manifest))
    negative_coarse = json.loads((negative / "annotations/coarse_labels.json").read_text())
    negative_coarse["session_id"] = "golden-negative-v2"
    negative_coarse["intervals"] = []
    (negative / "annotations/coarse_labels.json").write_text(json.dumps(negative_coarse))

    result = materialize_temporal_dataset([coarse, negative], tmp_path / "dataset")
    y = np.load(result.output_dir / "y.npy")
    session_ids = np.load(result.output_dir / "session_ids.npy", allow_pickle=True)
    negative_mask = np.load(result.output_dir / "hard_negative_mask.npy")

    assert np.all(y[:, 6:8] == 0)
    assert np.all(y[session_ids == "golden-negative-v2", 0] == 0)
    # The fixture begins at 0.2s and samples at 10 Hz. Pre-roll and sensor
    # gaps must not silently become negative supervision.
    X = np.load(result.output_dir / "X.npy")
    np.testing.assert_array_equal(y[session_ids == "golden-negative-v2", 5], X[session_ids == "golden-negative-v2", 6, -1])
    np.testing.assert_array_equal(negative_mask[session_ids == "golden-negative-v2"], y[session_ids == "golden-negative-v2", 5] >= .5)


def test_missing_coarse_review_is_not_silently_all_negative(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    session = tmp_path / "missing-coarse"
    shutil.copytree(repo_root / "fixtures/golden/session-v2/session", session)
    (session / "annotations/coarse_labels.json").unlink()

    with pytest.raises(TemporalDatasetError, match="coarse_labels"):
        materialize_temporal_dataset([session], tmp_path / "dataset")


def test_tampered_immutable_review_blocks_temporal_materialization(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    work = tmp_path / "tampered"
    shutil.copytree(repo_root / "fixtures/golden/session-v2", work)
    draft_path = work / "session" / "annotations" / "review-draft.json"
    draft_payload = json.loads(draft_path.read_text())
    draft_payload["review_context"] = {"mode": "assisted", "sync_verified": True}
    draft_path.write_text(json.dumps(draft_payload))
    finalize_session(work / "session", work / "derived")
    pointer = json.loads((work / "session/annotations/canonical_events.json").read_text())
    review_path = work / "session" / pointer["review_path"]
    review = json.loads(review_path.read_text())
    review["verified"] = False
    review_path.write_text(json.dumps(review))

    with pytest.raises(TemporalDatasetError, match="tampered review lineage"):
        materialize_temporal_dataset([work / "session"], tmp_path / "dataset")


def test_locked_temporal_splits_keep_both_classes_in_every_partition(tmp_path):
    sessions = []
    for index in range(3):
        sessions.append(
            {
                "session_id": f"eating-{index}",
                "created_at": f"2026-01-0{index + 1}T00:00:00+00:00",
                "label": "eating",
                "hard_negative": False,
                "verified": True,
                "event_supervision": True,
            }
        )
        sessions.append(
            {
                "session_id": f"negative-{index}",
                "created_at": f"2026-01-0{index + 1}T01:00:00+00:00",
                "label": "hard-negative",
                "hard_negative": True,
                "verified": True,
                "event_supervision": True,
            }
        )
    split = create_locked_temporal_splits(
        {"dataset_hash": "fixture", "sessions": sessions},
        tmp_path / "splits.json",
    )

    for name in ("train", "validation_locked", "test_locked"):
        ids = split["splits"][name]
        assert any(value.startswith("eating-") for value in ids)
        assert any(value.startswith("negative-") for value in ids)

    locked_before = {
        name: split["splits"][name]
        for name in ("validation_locked", "test_locked")
    }
    sessions.extend(
        [
            {
                "session_id": "eating-new",
                "created_at": "2026-02-01T00:00:00+00:00",
                "label": "eating",
                "hard_negative": False,
                "verified": True,
                "event_supervision": True,
            },
            {
                "session_id": "negative-new",
                "created_at": "2026-02-01T01:00:00+00:00",
                "label": "hard-negative",
                "hard_negative": True,
                "verified": True,
                "event_supervision": True,
            },
        ]
    )
    updated = create_locked_temporal_splits(
        {"dataset_hash": "fixture-updated", "sessions": sessions},
        tmp_path / "splits.json",
    )
    assert {
        name: updated["splits"][name]
        for name in ("validation_locked", "test_locked")
    } == locked_before
    assert {"eating-new", "negative-new"} <= set(updated["splits"]["train"])


def test_temporal_evaluation_masks_coarse_events_and_prevents_count_cancellation():
    timestamps = np.tile(np.arange(0.0, 0.7, 0.1), 2)
    session_ids = np.array(["positive"] * 7 + ["reviewed-negative"] * 7)
    target = np.zeros((14, 8), dtype=np.float32)
    target[:, 5] = 1.0
    target[:, 6] = 1.0
    target[:7, 0] = 1.0
    target[1, 1] = 1.0
    target[5, 1] = 1.0
    output = np.zeros((14, 5), dtype=np.float32)
    output[:, 0] = _logit(0.9)
    output[:, 1] = _logit(0.1)
    output[1, 1] = _logit(0.9)  # positive: one of two references is found
    output[10, 1] = _logit(0.9)  # negative: one false chew
    raw_windows = np.ones((14, 7, CONTEXT_SAMPLES), dtype=np.float32)
    hard_negative = np.array([False] * 7 + [True] * 7)

    metrics = _evaluate_temporal(
        target,
        output,
        raw_windows,
        load_config(),
        session_ids=session_ids,
        timestamps=timestamps,
        hard_negative_mask=hard_negative,
    )

    assert metrics["chew_true_positives"] == 1
    assert metrics["chew_false_positives"] == 1
    assert metrics["chew_false_negatives"] == 1
    assert metrics["chew_count_error"] == pytest.approx(1.0)


def test_temporal_evaluation_ignores_event_predictions_without_event_supervision():
    timestamps = np.arange(0.0, 0.7, 0.1)
    target = np.zeros((7, 8), dtype=np.float32)
    target[:, 5] = 1.0
    target[0, 0] = 1.0
    output = np.zeros((7, 5), dtype=np.float32)
    output[:, 0] = _logit(0.1)
    output[0, 0] = _logit(0.9)
    output[:, 1] = _logit(0.1)
    output[3, 1] = _logit(0.99)
    raw_windows = np.ones((7, 7, CONTEXT_SAMPLES), dtype=np.float32)

    metrics = _evaluate_temporal(
        target,
        output,
        raw_windows,
        load_config(),
        session_ids=np.array(["coarse"] * 7),
        timestamps=timestamps,
        hard_negative_mask=np.ones(7, dtype=bool),
    )

    assert metrics["chew_false_positives"] == 0
    assert metrics["chew_count_error"] == 0


def test_temporal_training_consumes_locked_session_arrays(tmp_path):
    features = tmp_path / "features"
    features.mkdir()
    session_ids = np.repeat(
        np.array(["train-eating", "train-negative", "val-eating", "val-negative"]),
        4,
    )
    rng = np.random.default_rng(11)
    X = rng.normal(size=(16, 7, CONTEXT_SAMPLES)).astype(np.float32)
    X[:, 6, :] = 1.0
    y = np.zeros((16, 8), dtype=np.float32)
    y[:, 5] = 1.0
    y[:, 6] = 1.0
    y[np.isin(session_ids, ["train-eating", "val-eating"]), 0] = 1.0
    y[1, 1] = 1.0
    y[9, 1] = 1.0
    np.save(features / "X.npy", X)
    np.save(features / "y.npy", y)
    np.save(features / "session_ids.npy", session_ids)
    split_path = tmp_path / "splits.json"
    split_path.write_text(
        json.dumps(
            {
                "splits": {
                    "train": ["train-eating", "train-negative"],
                    "validation_locked": ["val-eating", "val-negative"],
                }
            }
        )
    )
    config = load_config()
    config.wandb.enabled = False
    config.training.epochs = 1
    config.training.batch_size = 4
    config.training.patience = 1
    config.temporal_model.channels = [4]
    config.temporal_model.kernel_size = 3
    config.temporal_model.dropout = 0.0
    checkpoint_path = tmp_path / "temporal.pth"

    result = train_model(
        features,
        checkpoint_path,
        config,
        dataset_hash="fixture-dataset",
        data_source="test",
        split_manifest_path=split_path,
    )
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)

    assert result["model_id"].startswith("chewnet-temporal-")
    assert checkpoint["mode"] == "temporal_tcn"
    assert checkpoint["temporal_contract_version"] == "2.0.0"
    assert checkpoint["split_name"] == "locked_session_split"


@pytest.mark.skipif(platform.system() != "Darwin", reason="CoreML execution requires macOS")
def test_pytorch_coreml_temporal_probability_parity(tmp_path):
    model = TemporalChewNet(channels=(4,), kernel_size=3, dropout=0)
    checkpoint = tmp_path / "temporal-parity.pth"
    torch.save(
        {
            "model_id": "temporal-parity",
            "mode": "temporal_tcn",
            "temporal_contract_version": "2.0.0",
            "model_state_dict": model.state_dict(),
            "mean": np.zeros(6, dtype=np.float32),
            "std": np.ones(6, dtype=np.float32),
            "input_channels": 7,
            "context_samples": CONTEXT_SAMPLES,
            "channels": [4],
            "kernel_size": 3,
            "dropout": 0.0,
            "num_outputs": 5,
            "output_names": [
                "eating_logit",
                "chew_event_logit",
                "phase_sin",
                "phase_cos",
                "phase_confidence",
            ],
        },
        checkpoint,
    )
    package = tmp_path / "TemporalParity.mlpackage"
    export_coreml(checkpoint, package)
    rng = np.random.default_rng(7)
    window = rng.normal(size=(7, CONTEXT_SAMPLES)).astype(np.float32)
    window[6] = 1.0

    try:
        report = verify_temporal_coreml_parity(checkpoint, package, window)
    except CoreMLRuntimeUnavailable as exc:
        pytest.skip(str(exc))

    assert report["max_probability_error"] <= 1e-3


@pytest.mark.parametrize("origin", [0, 3600, 100000, 1_000_000_000])
def test_resampling_gap_mask_is_translation_invariant(origin):
    values = np.ones((2, 6), dtype=np.float32)
    window, grid = resample_motion_window(
        np.array([0.0, 1.0]) + origin, values,
        end_timestamp=origin + 1, context_seconds=2,
    )
    expected = np.zeros(100, dtype=np.float32)
    expected[[49, 99]] = 1
    np.testing.assert_array_equal(window[6], expected)
    assert np.all(window[:6, window[6] == 0] == 0)


def test_resampler_accepts_an_exact_left_sample_across_float_rounding():
    source = np.array([0., .1, .2, .3, .4])
    out, grid = resample_motion_window(source, np.ones((5, 6)), end_timestamp=.30000000000000004, context_seconds=.02)
    assert out[6, -1] == 1


def test_derived_mask_lookup_does_not_bridge_outages():
    from pipeline.temporal_dataset import _nearest_values
    source = np.array([100000., 100000.02, 100001., 100001.02])
    target = np.array([99999., 100000., 100000.03, 100000.5, 100001.02, 100002.])
    np.testing.assert_array_equal(_nearest_values(source, np.ones(4), target), [0, 1, 0, 0, 1, 0])
