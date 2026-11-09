"""How paired phones can reach this Mac, whatever network it is on.

An address is only a hint. The phone uses a route only after the Mac there
presents the pinned certificate and accepts the device token, so reporting
routes never weakens pairing. They let a phone find the Mac again after it
changes network (another Wi-Fi, the iPhone's Personal Hotspot, or away from
home) without anyone typing an address.

* ``lan``     - address of the interface that carries the default route
* ``local``   - the Bonjour host name (``<name>.local``)
* ``tailnet`` - the Mac's Tailscale MagicDNS name (``<mac>.<tailnet>.ts.net``),
  reachable from any network while both devices are on the same tailnet

iOS App Transport Security exempts private LAN addresses and ``.local`` names,
but not Tailscale's ``100.x`` addresses: a self-signed certificate there fails
with -9802 (seen on the iPhone, Nov 9 2026). The apps carry an ATS exception
for ``ts.net`` only, so the tailnet route is the MagicDNS name. The apps still
require the pinned certificate. Routes are sent only to paired devices (pairing
reply and ``/v1/status``), never from the unauthenticated ``/v1/hello``.
"""
from __future__ import annotations

import ipaddress
import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

# Tailscale assigns addresses from the carrier-grade NAT block.
TAILNET = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE = ("tailscale", "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale",
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale")


def primary_lan_ipv4() -> str | None:
    """Local address of the default route; a UDP connect sends no packets."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed anywhere
            address = probe.getsockname()[0]
        except OSError:
            return None
    ip = ipaddress.ip_address(address)
    if ip.is_loopback or ip.is_unspecified or ip in TAILNET:
        return None
    return address


def local_host_name() -> str | None:
    name = socket.gethostname()
    if name.endswith(".local"):
        return name
    try:
        result = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True,
                                text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    name = result.stdout.strip()
    return f"{name}.local" if result.returncode == 0 and name else None


def _tailscale() -> str | None:
    for candidate in _TAILSCALE:
        found = shutil.which(candidate) if "/" not in candidate else (
            candidate if Path(candidate).is_file() else None)
        if found:
            return found
    return None


def tailnet_name() -> str | None:
    """The Mac's MagicDNS name, or None when Tailscale is absent, stopped or without MagicDNS."""
    tool = _tailscale()
    if tool is None:
        return None
    try:
        result = subprocess.run([tool, "status", "--json"], capture_output=True, text=True, timeout=3)
        status = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if status.get("BackendState") != "Running":
        return None
    name = str((status.get("Self") or {}).get("DNSName") or "").rstrip(".")
    return name if name.endswith(".ts.net") else None


Probe = Callable[[], "str | None"]
DEFAULT_PROBES: dict[str, Probe] = {"lan": primary_lan_ipv4, "local": local_host_name,
                                    "tailnet": tailnet_name}


class RouteReporter:
    """Current routes for one listening port, re-checked at most every ``ttl_s``."""

    def __init__(self, port: int, *, ttl_s: float = 60.0, probes: dict[str, Probe] | None = None):
        self.port = port
        self.ttl_s = ttl_s
        self.probes = DEFAULT_PROBES if probes is None else probes
        self._lock = threading.Lock()
        self._cached: list[dict[str, object]] = []
        self._checked = float("-inf")

    def routes(self) -> list[dict[str, object]]:
        with self._lock:
            if time.monotonic() - self._checked < self.ttl_s:
                return list(self._cached)
            routes = []
            for kind, probe in self.probes.items():
                try:
                    host = probe()
                except Exception:  # noqa: BLE001 - a broken probe must not break status
                    host = None
                if host:
                    routes.append({"kind": kind, "host": host, "port": self.port})
            self._cached, self._checked = routes, time.monotonic()
            return list(routes)
