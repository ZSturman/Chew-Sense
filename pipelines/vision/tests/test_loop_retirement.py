"""Recording-level retirement: out of queues and datasets, nothing deleted or changed."""
from __future__ import annotations

import hashlib
import json

import pytest
from click.testing import CliRunner

from chewsense.loop.config import load_loop_config
from chewsense.loop.controller import LoopController
from chewsense.loop.processing import refresh_review_tasks
from chewsense.loop.retirement import (
    CATEGORIES, load_retirements, retire_recording, retirement_path, verify_preserved,
)
from chewsense.loop.store import LoopPaths

SID = "Not-eating-20261026-174428-SYNTH"


def _loop(tmp_path):
    paths = LoopPaths(tmp_path / "loop").ensure()
    for workspace in (paths.assisted, paths.blind):
        session = workspace / "sessions/raw" / SID
        (session / "annotations").mkdir(parents=True)
        (session / f"{SID}.csv").write_text("timestamp,ax,ay,az,gx,gy,gz\n1,0,0,0,0,0,0\n")
        (session / f"{SID}.mov").write_bytes(b"video")
        (session / "annotations/coarse_labels.json").write_text(json.dumps({"intervals": []}))
    paths.source(SID).mkdir(parents=True)
    (paths.source(SID) / "session.json").write_text("{}")
    record = {"session_id": SID, "role": "evaluation", "activity": "not_eating",
              "processing": "completed", "vision": "completed"}
    paths.session_record(SID).write_text(json.dumps(record))
    other = dict(record, session_id="Other-20261101-120000-AAAA", role="training")
    paths.session_record(other["session_id"]).write_text(json.dumps(other))
    return paths


def _snapshot(paths):
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths.root.rglob("*")
            if p.is_file() and "state/retired" not in str(p) and p.name != "review_tasks.json"}


def test_retirement_removes_queues_and_datasets_without_touching_files(tmp_path):
    paths = _loop(tmp_path)
    split_root = tmp_path / "eating"
    split_root.mkdir()
    (split_root / "splits.json").write_text(json.dumps({"assignments": {SID: {"split": "test"}}}))
    splits_before = (split_root / "splits.json").read_bytes()
    assert any(t["session_id"] == SID for t in refresh_review_tasks(paths))
    before = _snapshot(paths)

    record = retire_recording(paths, SID, category="mimed_chewing_no_food",
                              reason="Mimed chewing without food", evidence=["12 closed-lip cycles"],
                              split_root=split_root)

    assert record["category_title"] == CATEGORIES["mimed_chewing_no_food"]
    assert record["role_at_retirement"] == "evaluation"
    assert record["locked_split_at_retirement"] == {"split": "test"}
    assert f"blind/sessions/raw/{SID}/{SID}.mov" in record["preserved_files_sha256"]
    assert not any(t["session_id"] == SID for t in refresh_review_tasks(paths))
    controller = LoopController(paths, {})
    assert [r["session_id"] for r in controller.records()] == ["Other-20261101-120000-AAAA"]
    assert {r["session_id"] for r in controller.records(include_retired=True)} == {SID, "Other-20261101-120000-AAAA"}
    # Nothing deleted or rewritten; the locked split keeps its entry.
    assert _snapshot(paths) == before
    assert (split_root / "splits.json").read_bytes() == splits_before
    assert verify_preserved(paths, SID) == []
    assert set(load_retirements(paths.root)) == {SID}


def test_retirement_is_write_once_and_validated(tmp_path):
    paths = _loop(tmp_path)
    with pytest.raises(ValueError, match="category"):
        retire_recording(paths, SID, category="other", reason="x")
    with pytest.raises(ValueError, match="reason"):
        retire_recording(paths, SID, category="protocol_mismatch", reason="  ")
    with pytest.raises(ValueError, match="not a loop recording"):
        retire_recording(paths, "Unknown-20261101-120000-BBBB", category="protocol_mismatch", reason="x")
    retire_recording(paths, SID, category="protocol_mismatch", reason="x")
    first = retirement_path(paths.root, SID).read_bytes()
    with pytest.raises(FileExistsError):
        retire_recording(paths, SID, category="capture_defect", reason="y")
    assert retirement_path(paths.root, SID).read_bytes() == first


def test_preserved_file_changes_are_detected(tmp_path):
    paths = _loop(tmp_path)
    retire_recording(paths, SID, category="mimed_chewing_no_food", reason="x")
    (paths.blind / "sessions/raw" / SID / "annotations/coarse_labels.json").write_text('{"changed": true}')
    assert verify_preserved(paths, SID) == [f"blind/sessions/raw/{SID}/annotations/coarse_labels.json"]


def test_cli_retires_and_lists(tmp_path, monkeypatch):
    from chewsense.loop import cli

    paths = _loop(tmp_path)
    monkeypatch.setattr(cli, "_paths", lambda: paths)
    monkeypatch.setattr(cli, "_config", lambda _paths: load_loop_config())
    monkeypatch.setenv("CHEWSENSE_WORKSPACE", str(tmp_path))
    result = CliRunner().invoke(cli.loop, ["retire-recording", SID, "--category", "mimed_chewing_no_food",
                                           "--reason", "Mimed chewing without food"])
    assert result.exit_code == 0, result.output
    listing = CliRunner().invoke(cli.loop, ["retired"])
    assert "intact" in listing.output and "Mimed chewing" in listing.output


def test_review_offers_only_categories_the_loop_knows():
    import re
    from pathlib import Path

    swift = (Path(__file__).resolve().parents[3] / "apps/review-macos/ChewSenseReview/Models/Session.swift").read_text()
    block = swift[swift.index("static let categories"):swift.index("enum Failure")]
    offered = re.findall(r'\("([a-z_]+)", "', block)
    assert offered and set(offered) <= set(CATEGORIES)
