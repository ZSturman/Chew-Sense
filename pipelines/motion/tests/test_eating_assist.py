"""Assisted coarse labeling: proposals, provenance tiers and evaluation.

Synthetic data only.  These tests prove that proposals stay proposals, that
human decisions keep their origin and never enter evaluation tiers, and that
the evaluation arithmetic is right.  They say nothing about real accuracy.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.eating.assist import (  # noqa: E402
    AUTO,
    REVIEW,
    AssistConfig,
    build_spans,
    evaluate_assist,
    fuse,
    propose_for_session_dir,
    wilson,
)
from pipeline.eating.dataset import (  # noqa: E402
    ASSISTED_TIERS,
    EVALUATION_TIERS,
    LABEL_TIERS,
    LabeledInterval,
    load_canonical_session,
    overlay_review,
)
from pipeline.eating.runtime import export_spec  # noqa: E402

from test_eating_detection import _span, make_canonical  # noqa: E402
from test_eating_runtime import _fake_run  # noqa: E402


def _vision(folder: Path, duration: float, chewing: list[tuple[float, float]], face=(0.0, 1e9)) -> Path:
    """Ungated Vision output: chew cycles every 0.7 s inside ``chewing``."""

    (folder / "signals").mkdir(parents=True)
    cycles = []
    for start, end in chewing:
        t = start + 0.7
        while t < end:
            cycles.append({"start_ts": t - 0.6, "end_ts": t, "peak_ts": t - 0.3})
            t += 0.7
    (folder / "mouth_cycles.json").write_text(json.dumps(cycles))
    ts = np.arange(0, duration, 1 / 30)
    np.save(folder / "signals" / "ts.npy", ts)
    np.save(folder / "signals" / "jaw_open_bs_z.npy", np.sin(2 * np.pi * 1.4 * ts))
    visible = (ts >= face[0]) & (ts <= face[1])
    np.save(folder / "signals" / "face_confidence.npy", np.where(visible, 0.9, 0.0))
    return folder


def test_assisted_tiers_train_but_never_evaluate():
    assert set(ASSISTED_TIERS) <= set(LABEL_TIERS)
    assert not set(ASSISTED_TIERS) & set(EVALUATION_TIERS)
    assert "human_coarse" in EVALUATION_TIERS


def test_fusion_needs_two_independent_sources():
    video = np.array(["chewing", "not_chewing", "chewing", "unavailable", "not_chewing"])
    motion = np.array(["chewing", "not_chewing", "not_chewing", "chewing", "unavailable"])
    state, proposal, reason = fuse(video, motion)
    assert list(state) == [AUTO, AUTO, REVIEW, REVIEW, REVIEW]
    assert list(proposal[:2]) == ["chewing", "not_chewing"] and not any(proposal[2:])
    assert list(reason[2:]) == ["video and motion disagree", "face not visible", "motion missing"]


def test_spans_tile_the_recording_and_flag_conflicts():
    ends = np.arange(3.0, 30.0 + 1e-9, 0.5)
    states = np.where((ends > 12) & (ends < 22), "chewing", "not_chewing")
    frame = pd.DataFrame({
        "t_end": ends, "video_state": states, "motion_state": states,
        "p_motion": np.where(states == "chewing", 0.9, 0.1), "video_cycles_per_s": 0.0,
    })
    frame["state"], frame["proposal"], frame["reason"] = fuse(frame["video_state"], frame["motion_state"])
    frame.loc[frame.index[5], ["state", "proposal", "reason"]] = [REVIEW, "", "video and motion disagree"]
    spans = build_spans(frame, 30.0, AssistConfig(), existing_runs=[(0.0, 30.0, "not_chewing")])
    assert spans[0]["start"] == 0.0 and spans[-1]["end"] == 30.0
    assert all(abs(a["end"] - b["start"]) < 1e-6 for a, b in zip(spans, spans[1:]))
    chewing = [s for s in spans if s["proposal"] == "chewing"]
    assert len(chewing) == 1 and chewing[0]["conflict_with_existing_label"]
    assert chewing[0]["priority"] > max(s["priority"] for s in spans if s is not chewing[0])
    # A one-window disagreement is review, never silently proposed.
    assert any(s["state"] == REVIEW for s in spans)


def test_overlay_keeps_independent_labels_and_masks_unknown():
    base = [LabeledInterval(0, 30, "not_chewing", "human_coarse")]
    review = [
        (0, 10, "not_chewing", "human_batch_accepted"),   # repeats the phone label
        (10, 20, "chewing", "human_assisted"),             # corrects it
        (20, 25, "unknown", "human_assisted"),             # wearer can't tell
    ]
    merged = overlay_review(base, review)
    assert [(i.start, i.end, i.label, i.tier) for i in merged] == [
        (0, 10, "not_chewing", "human_coarse"),
        (10, 20, "chewing", "human_assisted"),
        (25, 30, "not_chewing", "human_coarse"),
    ]


def test_review_only_session_loads_with_assisted_tiers(tmp_path):
    folder = make_canonical(tmp_path, "Guided-a-20261105-100000-AAAA", intervals=[], chewing=[(20, 40)])
    (folder / "annotations" / "coarse_labels.json").unlink()
    (folder / "annotations" / "coarse-review-v0001.json").write_text(json.dumps({
        "artifact_type": "coarse_label_review", "schema_version": 1, "revision": 1,
        "session_id": folder.name,
        "intervals": [
            {"start_ts": 0, "end_ts": 20, "label": "not_chewing", "decision": "batch_accepted", "boundary_adjusted": False},
            {"start_ts": 20, "end_ts": 40, "label": "chewing", "decision": "accepted"},
            {"start_ts": 40, "end_ts": 60, "label": "unknown", "decision": "labelled"},
        ],
    }))
    session = load_canonical_session(folder)
    assert [(i.start, i.end, i.label, i.tier) for i in session.intervals] == [
        (0.5, 19.5, "not_chewing", "human_batch_accepted"),  # automatic boundaries get a margin
        (20, 40, "chewing", "human_assisted"),
    ]
    assert any("coarse-review-v0001.json" in note for note in session.notes)


def test_proposals_are_immutable_and_never_labels(tmp_path):
    run, _, _ = _fake_run(tmp_path / "run")
    spec = export_spec(run)
    folder = make_canonical(tmp_path / "raw", "Guided-b-20261105-110000-BBBB", duration=60.0,
                            chewing=[(15, 40)], intervals=[_span(0, 60, "not_chewing")])
    vision = _vision(tmp_path / "vision", 60.0, [(15, 40)])
    out = tmp_path / "assist"
    path, payload = propose_for_session_dir(folder, vision, spec, out)
    assert path.name == "coarse-proposals-v0001.json"
    assert not os.access(path, os.W_OK)
    assert payload["status"] == "proposed_not_labels"
    assert payload["spans"][0]["start"] == 0.0 and payload["spans"][-1]["end"] == 60.0
    # The phone said not chewing throughout; where both sources say chewing it is a conflict.
    assert any(s["conflict_with_existing_label"] for s in payload["spans"] if s["proposal"] == "chewing")
    again, _ = propose_for_session_dir(folder, vision, spec, out)
    assert again == path  # identical content is not duplicated
    labels_before = (folder / "annotations" / "coarse_labels.json").read_text()
    assert json.loads(labels_before)["intervals"] == [_span(0, 60, "not_chewing")]


def test_evaluation_counts_and_unavailable_face(tmp_path):
    folder = make_canonical(tmp_path / "raw", "Guided-c-20261105-120000-CCCC", duration=60.0,
                            chewing=[(15, 40)], intervals=[
                                _span(0, 15, "not_chewing"), _span(15, 40, "chewing"), _span(40, 60, "not_chewing")])
    session = load_canonical_session(folder)
    vision = _vision(tmp_path / "vision", 60.0, [(15, 40)], face=(0.0, 50.0))
    ends = np.arange(3.0, 60.0 + 1e-9, 0.5)
    inside = (ends - 3.0 >= 15) & (ends <= 40)
    predictions = pd.DataFrame({"session_id": session.session_id, "t_end": ends,
                                "probability": np.where(inside, 0.95, 0.05)})
    result = evaluate_assist([session], {session.session_id: vision}, predictions, AssistConfig())
    totals = result["totals"]
    assert result["false_positive_windows"] == 0 and result["false_negative_windows"] == 0
    assert totals["auto_chewing"] > 0 and totals["auto_not"] > 0
    assert totals["video_unavailable"] > 0  # face hidden after 50 s -> review, not proposed
    assert result["effort_model"]["status"] == "modelled, not measured"


def test_wilson_interval():
    assert wilson(0, 0)["rate"] is None
    interval = wilson(82, 82)
    assert interval["rate"] == 1.0 and interval["high"] == 1.0 and 0.94 < interval["low"] < 0.97
