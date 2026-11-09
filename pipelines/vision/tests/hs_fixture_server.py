"""Isolated Home Services (Chew Sense mailbox) for the end-to-end test.

Runs under Home Services' own environment, never the deployed Pi:

    <home-services>/.venv/bin/python hs_fixture_server.py <dir> <port>

``HOME_SERVICES_SRC`` may point at a checkout or worktree to test. The first
start builds Home Services' development fixture in ``<dir>`` (a fake HDD guarded
like the real one), a throwaway TLS certificate for 127.0.0.1 and the two
mailbox tokens (``<dir>/hs.json``). Restarts reuse all of it, so a stop and
start behaves like a Pi outage. Started by ``loop_e2e_harness.py``.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import sys
from pathlib import Path

if os.environ.get("HOME_SERVICES_SRC"):
    sys.path.insert(0, os.environ["HOME_SERVICES_SRC"])

import uvicorn  # noqa: E402

from home_services.api.app import create_app  # noqa: E402
from home_services.config import load_config  # noqa: E402
from home_services.contract import _fixture_config  # noqa: E402
from home_services.devfixtures import create_dev_fixtures  # noqa: E402
from home_services.services import build_services  # noqa: E402


def main(root: Path, port: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    info_path = root / "hs.json"
    first = not info_path.exists()
    if first:
        create_dev_fixtures(root)
        _fixture_config(root)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-days", "2", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                        "-keyout", str(root / "tls-key.pem"), "-out", str(root / "tls-cert.pem")],
                       check=True, capture_output=True)
    services = build_services(load_config(root / "config.toml"))
    if first:
        mac, _ = services.tokens.create("E2E Mac", ["chewsense:collect"])
        phone, _ = services.tokens.create("E2E iPhone", ["chewsense:deposit"])
        der = ssl.PEM_cert_to_DER_cert((root / "tls-cert.pem").read_text())
        info_path.write_text(json.dumps({
            "url": f"https://127.0.0.1:{port}", "mac_token": mac, "phone_token": phone,
            "fingerprint": hashlib.sha256(der).hexdigest(), "cafile": str(root / "tls-cert.pem"),
            "blobs": str(root / "volumes/backup-hdd/home-services/staging/chewsense")}))
    uvicorn.Server(uvicorn.Config(create_app(services), host="127.0.0.1", port=port, log_level="warning",
                                  ssl_certfile=str(root / "tls-cert.pem"),
                                  ssl_keyfile=str(root / "tls-key.pem"))).run()


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]))
