"""Isolated loop service for the Collect end-to-end test (Swift client ↔ Mac service).

Runs the real service code in a temporary folder, with the stand-in Vision of
the Python tests and synthetic recordings (test pattern video, flat motion).
Nothing touches ``workspace/``. Usage::

    .venv/bin/python pipelines/vision/tests/loop_e2e_harness.py <scratch-dir>

It writes ``<scratch-dir>/fixture.json`` (ports, pairing code, synthetic
session folders) and serves until ``<scratch-dir>/done`` exists, then checks
the service side and writes ``<scratch-dir>/result.json``. See
apps/collect-ios/ChewSenseCollectTests/LoopEndToEndTests.swift.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loop_fixtures import fake_vision, loop_config, make_session, make_video  # noqa: E402

from chewsense.loop.routes import RouteReporter  # noqa: E402
from chewsense.loop.server import LoopService, serve  # noqa: E402
from chewsense.loop.store import LoopPaths  # noqa: E402


def fake_proposer(assisted, session_id, log_path):
    """Stand-in for chewing-span proposals, as in test_loop.py."""
    return {"summary": {"coverage": 0.5, "review_items": 2, "batch_items": 3, "label_conflicts": 0}}


SESSIONS = ("E2E-route-20990101-000001-AAAAAAAA", "E2E-route-20990101-000002-BBBBBBBB")


def main(scratch: Path) -> int:
    scratch.mkdir(parents=True, exist_ok=True)
    paths = LoopPaths(scratch / "loop")
    service = LoopService(paths, loop_config(), vision_runner=fake_vision(),
                          trainer=lambda _p: None, proposer=fake_proposer)
    device, admin, stop = serve(service, host="127.0.0.1", device_port=0, admin_port=0, advertise=False)
    port = device.server_address[1]
    # The only route the Mac reports is loopback, so a phone holding a stale
    # address must find the Mac through its reported routes.
    service.routes = RouteReporter(port, probes={"lan": lambda: "127.0.0.1"})
    video = make_video(scratch / "pattern.mov", seconds=6)
    folders = [str(make_session(scratch / "phone", sid, video=video, video_seconds=6,
                                coarse=[dict(start_ts=0, end_ts=6, label="chewing", source="human_coarse")]))
               for sid in SESSIONS]
    fixture = {"port": port, "code": service.devices.open_window()["code"],
               "fingerprint": service.identity.fingerprint, "sessions": folders}
    (scratch / "fixture.json").write_text(json.dumps(fixture, indent=2))
    print(json.dumps(fixture), flush=True)
    deadline = time.monotonic() + 900
    try:
        while not (scratch / "done").exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        jobs = service.processor.jobs()
        result = {
            "jobs": len(jobs),
            "job_states": sorted(j["state"] for j in jobs),
            "import_states": sorted(j.get("import_state") or "none" for j in jobs),
            "imported": sorted(p.name for p in paths.sources.iterdir() if not p.name.startswith(".")),
            "quarantined": sorted(p.name for p in paths.quarantine.iterdir()),
            "devices": [d["name"] for d in service.devices.list()],
        }
        (scratch / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
        # A re-sent recording is re-zipped (new archive hash), so it may get its
        # own job; the importer must recognize it as a duplicate, not import it.
        extra = result["import_states"].count("duplicate")
        ok = (result["imported"] == sorted(SESSIONS) and not result["quarantined"]
              and result["import_states"].count("imported") == len(SESSIONS)
              and result["jobs"] == len(SESSIONS) + extra
              and set(result["job_states"]) == {"completed"})
        return 0 if ok else 1
    finally:
        stop.set()
        device.shutdown()
        admin.shutdown()


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
