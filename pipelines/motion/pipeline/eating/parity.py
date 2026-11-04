"""Python-versus-Swift parity for the exported eating detector.

Every recording is written once as the exact float64 timestamps and float32
values the experiment used.  The Swift runtime (compiled from Live's sources
by ``tools/eating-parity/build``) streams those samples one by one, exactly as
Live receives them, and this module compares its windows with
:func:`runtime.run_stream`:

* window end times and validity (must be identical),
* the 80 float32 features (bit-identical fraction, largest difference),
* model probability (largest absolute difference),
* decoder state per window and decoded bouts (must be identical).

It also measures the noise floor of the decision: how much the probability
moves when inputs change by about one float32 rounding step.  A
parity difference below that floor cannot be distinguished from the sensor's
own precision.
"""

from __future__ import annotations

import json
import struct
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .dataset import EatingSession
from .runtime import TreeEnsemble, decoder_config, run_stream, stream_end_times
from .evaluate import decode_bouts

INPUT_MAGIC = b"CSEP0001"
OUTPUT_MAGIC = b"CSEO0001"


def write_input(path: Path, timestamps: np.ndarray, imu: np.ndarray, first_end: float) -> None:
    t = np.ascontiguousarray(timestamps, dtype="<f8")
    v = np.ascontiguousarray(imu, dtype="<f4")
    if v.shape != (len(t), 6):
        raise ValueError("imu must be (n, 6)")
    with Path(path).open("wb") as handle:
        handle.write(INPUT_MAGIC)
        handle.write(struct.pack("<Qd", len(t), float(first_end)))
        handle.write(t.tobytes())
        handle.write(v.tobytes())


@dataclass
class SwiftOutput:
    t_end: np.ndarray
    valid_fraction: np.ndarray
    probability: np.ndarray
    decoder_input: np.ndarray
    smoothed: np.ndarray
    chewing: np.ndarray
    features: np.ndarray


def read_output(path: Path) -> SwiftOutput:
    data = Path(path).read_bytes()
    if data[:8] != OUTPUT_MAGIC:
        raise ValueError(f"{path} is not an eating-parity output")
    count, feature_count = struct.unpack_from("<QQ", data, 8)
    record = np.dtype([
        ("end", "<f8"), ("valid", "<f8"), ("probability", "<f8"), ("input", "<f8"),
        ("smoothed", "<f8"), ("chewing", "u1"), ("features", "<f4", (feature_count,)),
    ])
    rows = np.frombuffer(data, dtype=record, count=count, offset=24)
    return SwiftOutput(
        t_end=rows["end"].copy(), valid_fraction=rows["valid"].copy(), probability=rows["probability"].copy(),
        decoder_input=rows["input"].copy(), smoothed=rows["smoothed"].copy(),
        chewing=rows["chewing"].astype(bool), features=rows["features"].copy(),
    )


def _bouts(times: np.ndarray, states: np.ndarray) -> list[tuple[float, float]]:
    bouts, start = [], None
    for t, state in zip(times, states):
        if state and start is None:
            start = float(t)
        elif not state and start is not None:
            bouts.append((start, float(t)))
            start = None
    if start is not None:
        bouts.append((start, float(times[-1])))
    return bouts


def noise_floor(spec: Mapping[str, Any], session: EatingSession, *, seed: int = 0, max_windows: int = 400) -> dict[str, Any]:
    """Probability change when every input value moves by about one float32 step."""

    ends = stream_end_times(session.motion_t, float(spec["decision"]["stride_s"]), first_end=float(spec["input"]["window_s"]))
    ends = ends[: max_windows]
    if not len(ends):
        return {"windows": 0}
    base = run_stream(spec, session.motion_t, session.imu, ends)
    rng = np.random.default_rng(seed)
    imu = session.imu.astype(np.float32)
    nudged = np.where(rng.random(imu.shape) < 0.5, np.nextafter(imu, np.float32(np.inf)), np.nextafter(imu, np.float32(-np.inf)))
    moved = run_stream(spec, session.motion_t, nudged.astype(np.float32), ends)
    ok = base["probability"].notna() & moved["probability"].notna()
    diff = (base["probability"][ok] - moved["probability"][ok]).abs()
    return {
        "windows": int(ok.sum()),
        "max_probability_change": float(diff.max()) if len(diff) else None,
        "windows_changed": int((diff > 0).sum()),
        "state_changes": int((base["chewing"] != moved["chewing"]).sum()),
    }


def compare_session(spec: Mapping[str, Any], session: EatingSession, swift: SwiftOutput,
                    ensemble: TreeEnsemble) -> dict[str, Any]:
    ends = swift.t_end
    expected_ends = stream_end_times(
        session.motion_t, float(spec["decision"]["stride_s"]), first_end=float(spec["input"]["window_s"])
    )
    reference = run_stream(spec, session.motion_t, session.imu, expected_ends, ensemble=ensemble)
    result: dict[str, Any] = {
        "session_id": session.session_id,
        "windows_python": int(len(expected_ends)),
        "windows_swift": int(len(ends)),
        "end_times_identical": bool(len(ends) == len(expected_ends) and np.array_equal(ends, expected_ends)),
    }
    if not result["end_times_identical"]:
        result["status"] = "fail"
        return result
    valid_ok = np.array_equal(swift.valid_fraction, reference["valid_fraction"].to_numpy())
    py_prob = reference["probability"].to_numpy()
    invalid_same = np.array_equal(np.isnan(py_prob), np.isnan(swift.probability))
    usable = ~np.isnan(py_prob) & ~np.isnan(swift.probability)
    py_features = reference.attrs["features"][usable]
    sw_features = swift.features[usable]
    identical = (py_features.view(np.uint32) == sw_features.view(np.uint32))
    feature_diff = np.abs(py_features.astype(np.float64) - sw_features.astype(np.float64))
    scale = np.maximum(np.abs(py_features.astype(np.float64)), 1e-6)
    prob_diff = np.abs(py_prob[usable] - swift.probability[usable])
    py_states = reference["chewing"].to_numpy()
    result.update({
        "valid_fraction_identical": bool(valid_ok),
        "invalid_windows_identical": bool(invalid_same),
        "scored_windows": int(usable.sum()),
        "feature_values_bit_identical": float(identical.mean()) if identical.size else None,
        "windows_all_features_identical": int(identical.all(axis=1).sum()) if identical.size else 0,
        "max_feature_abs_diff": float(feature_diff.max()) if feature_diff.size else 0.0,
        "max_feature_rel_diff": float((feature_diff / scale).max()) if feature_diff.size else 0.0,
        "max_probability_abs_diff": float(prob_diff.max()) if prob_diff.size else 0.0,
        "probability_differs_windows": int((prob_diff > 0).sum()),
        "threshold_decision_disagreements": int(
            ((py_prob[usable] >= spec["decision"]["threshold"]) != (swift.probability[usable] >= spec["decision"]["threshold"])).sum()
        ),
        "max_smoothed_abs_diff": float(np.abs(reference["smoothed"].to_numpy() - swift.smoothed).max()) if len(ends) else 0.0,
        "state_disagreements": int((py_states != swift.chewing).sum()),
        "bouts_python": [list(b) for b in reference.attrs["bouts"]],
        "bouts_swift": [list(b) for b in _bouts(ends, swift.chewing)],
    })
    result["bouts_identical"] = result["bouts_python"] == result["bouts_swift"]
    passed = (
        result["valid_fraction_identical"] and result["invalid_windows_identical"]
        and result["state_disagreements"] == 0 and result["bouts_identical"]
        and result["threshold_decision_disagreements"] == 0
    )
    result["status"] = "pass" if passed else "fail"
    return result


def experiment_policy_difference(
    spec: Mapping[str, Any], session: EatingSession, run_frame, ensemble: TreeEnsemble
) -> dict[str, Any]:
    """How the deployed gap rule differs from the experiment's bout replay.

    The experiment scored windows below the validity bar inside unlabelled
    time with the model; the runtime gives them probability 0.  Both decoders
    run on the experiment's own window grid with the final model.
    """

    rows = run_frame[run_frame["session_id"] == session.session_id].sort_values("t_end")
    if rows.empty:
        return {"session_id": session.session_id, "windows": 0}
    ends = rows["t_end"].to_numpy()
    deployed = run_stream(spec, session.motion_t, session.imu, ends, ensemble=ensemble)
    probability = deployed["probability"].to_numpy().copy()
    gap_rows = (rows["exclusion"] == "sensor_gap").to_numpy()
    experiment_input = probability.copy()
    low_valid = np.isnan(probability)
    if low_valid.any():
        from ..temporal import resample_motion_window
        from .features import compute_features

        windows = np.stack([
            resample_motion_window(session.motion_t, session.imu, end_timestamp=float(e))[0] for e in ends[low_valid]
        ])
        experiment_input[low_valid] = ensemble.probability(compute_features(windows))
    experiment_input[gap_rows] = np.nan
    config = decoder_config(spec)
    experiment_states, experiment_bouts = decode_bouts(ends, experiment_input, config)
    return {
        "session_id": session.session_id,
        "windows": int(len(ends)),
        "windows_policy_differs": int((low_valid & ~gap_rows).sum()),
        "state_differences": int((experiment_states != deployed["chewing"].to_numpy()).sum()),
        "bouts_experiment": len(experiment_bouts),
        "bouts_deployed": len(deployed.attrs["bouts"]),
    }


def synthetic_stream(seed: int = 11) -> tuple[np.ndarray, np.ndarray]:
    """45 s synthetic AirPods stream on a host-uptime clock (no wearer data).

    Quiet sitting, 20 s of jittered jaw-like rotation pulses that the detector
    scores as chewing, quiet again with a 0.6 s sensor gap, and one repeated
    and one backward timestamp that the runtime must drop.
    """

    rng = np.random.default_rng(seed)
    t = np.arange(0.0, 45.0, 0.02) + rng.uniform(-0.002, 0.002, size=2250)
    imu = rng.normal(0.0, 0.004, size=(len(t), 6))
    chewing = (t >= 10.0) & (t < 30.0)
    imu[chewing] += rng.normal(0.0, 0.016, size=(int(chewing.sum()), 6))
    beat = 10.3
    while beat < 30.0:
        a = 0.4 * rng.uniform(0.6, 1.4)
        pulse = np.exp(-0.5 * ((t - beat) / 0.1) ** 2)
        slope = -(t - beat) / 0.1 * pulse
        imu[:, 3] += a * slope
        imu[:, 4] += 0.5 * a * pulse
        imu[:, 5] += 0.3 * a * slope
        imu[:, 0] += 0.05 * a * pulse
        imu[:, 1] += 0.03 * a * slope
        imu[:, 2] += 0.02 * a * pulse
        beat += rng.uniform(0.85, 1.15) / 1.5
    keep = (t < 38.0) | (t > 38.6)
    t, imu = t[keep] + 291953.157447375, imu[keep].astype(np.float32)
    # A repeated and a backward row (dropped by every implementation).
    t = np.insert(t, [400, 1200], [t[399], t[1199] - 0.01])
    imu = np.insert(imu, [400, 1200], imu[[399, 1199]], axis=0)
    return t, imu


def live_fixture(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Expected Python outputs for :func:`synthetic_stream`, for Live's XCTest."""

    t, imu = synthetic_stream()
    running = np.maximum.accumulate(t)
    advancing = np.ones(len(t), dtype=bool)
    advancing[1:] = t[1:] > running[:-1]
    clean_t, clean_imu = t[advancing], imu[advancing]
    ends = stream_end_times(clean_t, float(spec["decision"]["stride_s"]))
    reference = run_stream(spec, clean_t, clean_imu, ends)
    probability = reference["probability"].to_numpy()
    return {
        "artifact_type": "eating_detector_live_fixture",
        "model_id": spec["model_id"],
        "note": "Synthetic stream; expected values from pipeline.eating.runtime.run_stream. "
                "Regenerate with `chewsense-motion eating-parity --write-live-fixture`.",
        "dropped_samples": int((~advancing).sum()),
        "samples": [[float(ti), *[float(v) for v in row]] for ti, row in zip(t, imu)],
        "windows": [
            {
                "end": float(end),
                "valid_fraction": float(valid),
                "probability": None if np.isnan(p) else float(p),
                "smoothed": float(s),
                "chewing": bool(c),
            }
            for end, valid, p, s, c in zip(
                ends, reference["valid_fraction"], probability, reference["smoothed"], reference["chewing"]
            )
        ],
        "bouts": [list(b) for b in reference.attrs["bouts"]],
    }


def run_parity(
    *,
    spec: Mapping[str, Any],
    sessions: Sequence[EatingSession],
    swift_tool: Path,
    work_dir: Path,
    run_frame=None,
    noise_sessions: int = 3,
) -> dict[str, Any]:
    work_dir = Path(work_dir)
    inputs, outputs = work_dir / "inputs", work_dir / "swift"
    inputs.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    first_end = float(spec["input"]["window_s"])
    spec_path = work_dir / "spec.json"
    spec_path.write_text(json.dumps(spec))
    for session in sessions:
        write_input(inputs / f"{session.session_id}.input.bin", session.motion_t, session.imu, first_end)
    completed = subprocess.run(
        [str(swift_tool), str(spec_path), str(inputs), str(outputs)], capture_output=True, text=True, timeout=3600
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Swift parity tool failed: {completed.stderr.strip() or completed.stdout.strip()}")
    swift_summary = json.loads(completed.stdout.strip().splitlines()[-1])
    ensemble = TreeEnsemble.from_spec(spec)
    per_session = [
        compare_session(spec, session, read_output(outputs / f"{session.session_id}.swift.bin"), ensemble)
        for session in sessions
    ]
    scored = [r for r in per_session if r.get("scored_windows")]
    summary: dict[str, Any] = {
        "artifact_type": "eating_detector_parity",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_id": spec.get("model_id"),
        "recordings": len(per_session),
        "windows": int(sum(r["windows_swift"] for r in per_session)),
        "scored_windows": int(sum(r.get("scored_windows", 0) for r in per_session)),
        "recordings_passed": int(sum(r["status"] == "pass" for r in per_session)),
        "feature_values_bit_identical": float(np.average(
            [r["feature_values_bit_identical"] for r in scored], weights=[r["scored_windows"] for r in scored]
        )) if scored else None,
        "max_feature_rel_diff": max((r["max_feature_rel_diff"] for r in scored), default=0.0),
        "max_probability_abs_diff": max((r["max_probability_abs_diff"] for r in scored), default=0.0),
        "threshold_decision_disagreements": int(sum(r.get("threshold_decision_disagreements", 0) for r in per_session)),
        "state_disagreements": int(sum(r.get("state_disagreements", 0) for r in per_session)),
        "bouts_identical_recordings": int(sum(bool(r.get("bouts_identical")) for r in per_session)),
        "swift_runtime": swift_summary,
        "inputs": "recorded float64 timestamps and float32 values streamed sample by sample; "
                  "simulated stream on a Mac, not a physical device",
        "per_session": per_session,
    }
    summary["status"] = "pass" if summary["recordings_passed"] == len(per_session) else "fail"
    longest = sorted(sessions, key=lambda s: -len(s.motion_t))[:noise_sessions]
    summary["noise_floor"] = [{"session_id": s.session_id, **noise_floor(spec, s)} for s in longest]
    if run_frame is not None:
        summary["experiment_policy_difference"] = [
            experiment_policy_difference(spec, s, run_frame, ensemble) for s in sessions
        ]
    return summary
