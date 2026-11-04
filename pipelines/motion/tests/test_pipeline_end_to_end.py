"""Upload-to-model path on a synthetic workspace (software proof, not accuracy).

recordings with phone labels -> experiment -> export -> proposals for a new,
unlabelled recording (video + motion) -> the wearer's assisted review (as
Review writes it) -> dataset with provenance tiers -> development-only
retraining -> status.  Checks that assisted labels train but never score and
that the locked test is not consulted by the development run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.eating.assist import propose_for_session_dir  # noqa: E402
from pipeline.eating.dataset import load_canonical_session  # noqa: E402
from pipeline.eating.experiment import ExperimentConfig, run_experiment  # noqa: E402
from pipeline.eating.runtime import export_spec, run_stream, stream_end_times  # noqa: E402
from pipeline.eating.status import build_status  # noqa: E402

from test_eating_assist import _vision  # noqa: E402
from test_eating_detection import _span, make_canonical  # noqa: E402


def _review_from_proposals(proposals: dict, truth: list[tuple[float, float]]) -> dict:
    """What Review saves: batch-accept agreements, decide review spans from what the video shows."""

    intervals = []
    for span in proposals["spans"]:
        mid = (span["start"] + span["end"]) / 2
        actual = "chewing" if any(a <= mid <= b for a, b in truth) else "not_chewing"
        if span["state"] == "auto" and not span["conflict_with_existing_label"]:
            label, decision = span["proposal"], "batch_accepted"
        else:
            label, decision = actual, "labelled"
        intervals.append({
            "start_ts": span["start"], "end_ts": span["end"], "label": label, "decision": decision,
            "proposal_id": span["id"], "proposal": span["proposal"], "proposal_state": span["state"],
            "boundary_adjusted": False,
        })
    return {
        "artifact_type": "coarse_label_review", "schema_version": 1, "session_id": proposals["session_id"],
        "revision": 1, "proposals": {"content_sha256": proposals["content_sha256"]},
        "intervals": intervals, "review_context": {"mode": "assisted_coarse", "active_seconds": 42.0},
    }


def test_upload_to_retraining_with_assisted_labels(tmp_path):
    workspace = tmp_path / "workspace"
    raw = workspace / "sessions" / "raw"
    for index in range(8):
        chewing = index % 2 == 0
        make_canonical(
            raw, f"Guided-s{index}-2026110{index}-100000-AAAA", seed=index, duration=60.0,
            chewing=[(15, 40)] if chewing else [],
            intervals=[_span(0, 15, "not_chewing"), _span(15, 40, "chewing"), _span(40, 60, "not_chewing")]
            if chewing else [_span(0, 60, "not_chewing")],
        )
    first = run_experiment(workspace=workspace, log=lambda *a: None, config=ExperimentConfig(
        models=("prior", "logreg", "hgb"), folds=2, bootstrap_resamples=20))
    spec = export_spec(first, model="hgb")  # the deployable kind, whichever model the toy run selected

    # A new upload: video + motion, no phone labels.
    new_id = "Guided-new-20261120-100000-BBBB"
    folder = make_canonical(raw, new_id, seed=21, duration=60.0, chewing=[(20, 45)], intervals=[])
    (folder / "annotations" / "coarse_labels.json").unlink()
    vision = _vision(workspace / "derived" / "vision" / new_id, 60.0, [(20, 45)])
    _, proposals = propose_for_session_dir(folder, vision, spec, workspace / "derived" / "assist" / new_id)
    assert proposals["status"] == "proposed_not_labels"
    before = build_status(workspace=workspace, repo=tmp_path / "no-repo")
    assert new_id in before["data"]["awaiting_assisted_review"]
    assert not any(r["session_id"] == new_id and r["usable"] for r in before["recordings"])

    review = _review_from_proposals(proposals, truth=[(20, 45)])
    (folder / "annotations" / "coarse-review-v0001.json").write_text(json.dumps(review))
    session = load_canonical_session(folder)
    assert {i.tier for i in session.intervals} <= {"human_assisted", "human_batch_accepted"}

    index_before = json.loads((workspace / "experiments" / "eating" / "index.json").read_text())
    second = run_experiment(workspace=workspace, log=lambda *a: None, config=ExperimentConfig(
        models=("prior", "logreg", "hgb"), folds=2, bootstrap_resamples=20, evaluate_test=False))
    index_after = json.loads((workspace / "experiments" / "eating" / "index.json").read_text())
    assert index_after["test_consultations"] == index_before["test_consultations"]
    manifest = json.loads((second / "dataset_manifest.json").read_text())
    by_tier = manifest["windows"]["labelled_by_tier"]
    assert by_tier.get("human_batch_accepted", 0) + by_tier.get("human_assisted", 0) > 0
    metrics = json.loads((second / "metrics.json").read_text())
    scored = metrics["results"]["hgb"]["dev_oof"]["by_tier"]
    assert not {"human_assisted", "human_batch_accepted"} & set(scored)  # train, never score

    after = build_status(workspace=workspace, repo=tmp_path / "no-repo")
    row = next(r for r in after["recordings"] if r["session_id"] == new_id)
    assert row["usable"] and row["labels"]["assisted_s"] > 0
    assert new_id not in after["data"]["awaiting_assisted_review"]

    # The exported detector streams the new recording exactly like the reference.
    stream = run_stream(spec, session.motion_t, session.imu, stream_end_times(session.motion_t, 0.5))
    assert stream["probability"].notna().all()
