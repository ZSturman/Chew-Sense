"""Isolated Mac loop service (and optionally Home Services) for Collect's end-to-end tests.

Runs the real service code in a temporary folder, with the stand-in Vision of
the Python tests and synthetic recordings (test pattern video, flat motion).
Nothing touches ``workspace/``, the running service or the Pi. Usage::

    .venv/bin/python pipelines/vision/tests/loop_e2e_harness.py <scratch-dir>

With ``HOME_SERVICES_PYTHON`` (and optionally ``HOME_SERVICES_SRC``) set, it
also starts an isolated Home Services mailbox (``hs_fixture_server.py``) and
configures the Mac's collector for it.

It writes ``<scratch-dir>/fixture.json`` and then serves until
``<scratch-dir>/done`` exists. The test steers it with command files
``<scratch-dir>/cmd/<n>-<action>`` (mac-sleep, mac-wake, pi-stop, pi-start,
pairing); each is answered by ``<n>.done``. Finally it checks the service side
and writes ``result.json``. See apps/collect-ios/ChewSenseCollectTests/LoopEndToEndTests.swift.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loop_fixtures import fake_vision, loop_config, make_session, make_video  # noqa: E402

from chewsense.loop.mailbox import MailboxConfig  # noqa: E402
from chewsense.loop.routes import RouteReporter  # noqa: E402
from chewsense.loop.server import LoopService, _make_handler, _Server, serve  # noqa: E402
from chewsense.loop.store import LoopPaths  # noqa: E402

SESSIONS = tuple(f"E2E-route-20990101-00000{i}-{c * 8}" for i, c in enumerate("ABCDEF", start=1))


def fake_proposer(assisted, session_id, log_path):
    """Stand-in for chewing-span proposals, as in test_loop.py."""
    return {"summary": {"coverage": 0.5, "review_items": 2, "batch_items": 3, "label_conflicts": 0}}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class HomeServer:
    def __init__(self, scratch: Path):
        self.root, self.port, self.process = scratch / "hs", free_port(), None
        self.python = os.environ["HOME_SERVICES_PYTHON"]
        self.script = Path(__file__).with_name("hs_fixture_server.py")
        self.log = open(scratch / "hs.log", "a")

    def start(self) -> None:
        self.process = subprocess.Popen([self.python, str(self.script), str(self.root), str(self.port)],
                                        stdout=self.log, stderr=self.log)
        for _ in range(300):
            if (self.root / "hs.json").exists() and self._up():
                return
            time.sleep(0.1)
        raise RuntimeError("Home Services fixture did not start; see hs.log")

    def _up(self) -> bool:
        info = self.info
        context = ssl.create_default_context(cafile=info["cafile"])
        try:
            urllib.request.urlopen(f"{info['url']}/healthz", timeout=1, context=context).read()
            return True
        except OSError:
            return False

    def stop(self) -> None:
        if self.process:
            self.process.terminate()
            self.process.wait(10)
            self.process = None

    @property
    def info(self) -> dict:
        return json.loads((self.root / "hs.json").read_text())


def main(scratch: Path) -> int:
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "cmd").mkdir(exist_ok=True)
    paths = LoopPaths(scratch / "loop").ensure()
    home = HomeServer(scratch) if os.environ.get("HOME_SERVICES_PYTHON") else None
    if home:
        home.start()
        info = home.info
        MailboxConfig.write(paths.service, info["url"], info["mac_token"], info["phone_token"])
        config_path = paths.service / "mailbox.json"
        config_path.write_text(json.dumps({**json.loads(config_path.read_text()), "cafile": info["cafile"],
                                           "fingerprint": info["fingerprint"], "interval_s": 0.5}))
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
               "fingerprint": service.identity.fingerprint, "sessions": folders,
               "control": str(scratch / "cmd"), "mailbox": home is not None}
    (scratch / "fixture.json").write_text(json.dumps(fixture, indent=2))
    print(json.dumps(fixture), flush=True)

    state = {"device": device, "asleep": False}
    Base = _make_handler(service, admin=False)

    class Sleepy(Base):
        """A sleeping Mac answers nothing, not even on connections already open."""

        def handle_one_request(self):
            if state["asleep"]:
                self.close_connection = True
                return
            super().handle_one_request()

    def sleep_mac():
        state["asleep"] = True
        state["device"].shutdown()
        state["device"].server_close()
        if service.mailbox:
            service.mailbox.awake.clear()

    def wake_mac():
        server = _Server(("127.0.0.1", port), Sleepy)
        server.socket = service.identity.ssl_context().wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        state["device"], state["asleep"] = server, False
        if service.mailbox:
            service.mailbox.awake.set()

    # Serve through the sleep-aware handler from the start.
    device.shutdown()
    device.server_close()
    wake_mac()

    actions = {"mac-sleep": sleep_mac, "mac-wake": wake_mac,
               "pi-stop": home.stop if home else None, "pi-start": home.start if home else None,
               "pairing": lambda: service.devices.open_window()["code"]}
    deadline = time.monotonic() + 900
    try:
        while not (scratch / "done").exists() and time.monotonic() < deadline:
            for command in sorted((scratch / "cmd").glob("*-*")):
                number, action = command.name.split("-", 1)
                answer = actions[action]()
                (scratch / "cmd" / f"{number}.done").write_text(str(answer or "ok"))
                command.unlink()
            time.sleep(0.05)
        time.sleep(2)  # let a last collector cycle finish
        jobs = service.processor.jobs()
        result = {
            "jobs": len(jobs),
            "job_states": sorted(j["state"] for j in jobs),
            "import_states": sorted(j.get("import_state") or "none" for j in jobs),
            "via_home_server": len([j for j in jobs if "(home server)" in (j.get("device") or "")]),
            "imported": sorted(p.name for p in paths.sources.iterdir() if not p.name.startswith(".")),
            "quarantined": sorted(p.name for p in paths.quarantine.iterdir()),
            "devices": [d["name"] for d in service.devices.list()],
            "job_detail": [(j.get("session_id"), j.get("device"), j.get("import_state"), j["archive_sha256"][:8],
                            j.get("upload_id")) for j in jobs],
        }
        if home:
            blobs = Path(home.info["blobs"])
            result["home_server_copies_left"] = sorted(p.name for p in blobs.glob("*.sealed")) if blobs.exists() else []
            result["mailbox_last_error"] = json.loads((paths.root / "state/mailbox.json").read_text()).get("last_error")
        (scratch / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
        expected = sorted(SESSIONS if home else SESSIONS[:2])
        # A re-sent recording is re-zipped (new archive hash), so it may get its
        # own job; the importer must recognize it as a duplicate, not import it.
        extra = result["import_states"].count("duplicate")
        ok = (result["imported"] == expected and not result["quarantined"]
              and result["import_states"].count("imported") == len(expected)
              and result["jobs"] == len(expected) + extra
              and set(result["job_states"]) == {"completed"}
              and not result.get("home_server_copies_left"))
        return 0 if ok else 1
    finally:
        stop.set()
        state["device"].shutdown()
        admin.shutdown()
        if home:
            home.stop()


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
