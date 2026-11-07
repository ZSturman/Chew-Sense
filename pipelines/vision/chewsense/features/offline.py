"""Run MediaPipe only in a process without network access.

MediaPipe's PyPI builds since 0.10.22 contain Google's Clearcut logger. It
posts usage reports to play.googleapis.com: task name and mode, graph name,
latencies, elapsed time, frame and dropped-frame counts, error codes and
MediaPipe/Python/OS versions in Clearcut's client envelope. Google states that
input images are never sent and offers no switch to turn the reports off
(google-ai-edge/mediapipe#6291).

Landmarkers are therefore created only where the macOS sandbox denies every
socket and name lookup. ``require_offline`` enforces that before a MediaPipe task
exists; ``offline_command`` and ``reexec_offline`` start such a process. Model
files are fetched, if missing, before the sandbox starts.
"""
from __future__ import annotations

import errno
import os
import socket
import sys
from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
# ``network*`` covers sockets and libc's resolver; Network.framework (used by the
# uploader) resolves names through the dnssd Mach service instead.
SANDBOX_PROFILE = ('(version 1)(allow default)(deny network*)'
                   '(deny mach-lookup (global-name "com.apple.dnssd.service"))')
_SANDBOXED_ENV = "CHEWSENSE_MEDIAPIPE_SANDBOXED"
# RFC 5737/3849 documentation addresses. A UDP connect only selects a route and
# sends nothing, so the probe is silent with or without the sandbox.
_PROBES = ((socket.AF_INET, ("192.0.2.1", 9)), (socket.AF_INET6, ("2001:db8::1", 9)))


class MediaPipeNetworkError(RuntimeError):
    pass


def network_blocked() -> bool:
    """True only if the sandbox refuses outbound sockets in this process."""
    denied = False
    for family, address in _PROBES:
        try:
            probe = socket.socket(family, socket.SOCK_DGRAM)
        except OSError as error:
            if error.errno == errno.EPERM:
                denied = True
                continue
            if error.errno in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT}:
                continue  # unsupported address family, not evidence of isolation
            return False  # resource exhaustion or other failures prove nothing
        with probe:
            try:
                probe.connect(address)
            except OSError as error:
                if error.errno != errno.EPERM:
                    return False
                denied = True
            else:
                return False
    return denied


def require_offline(task: str) -> None:
    """Refuse to create a MediaPipe task in a process that can reach the network.

    There is deliberately no override.
    """
    if network_blocked():
        return
    raise MediaPipeNetworkError(
        f"{task} not started: MediaPipe sends usage reports to Google and this process "
        f"has network access. Use `chewsense-vision run`, `extract-features` or "
        f"run_on_sessions.py, which sandbox themselves.")


def offline_command(argv: list[str]) -> list[str]:
    """``argv`` prefixed so that it runs without network access (same PID)."""
    if not Path(SANDBOX_EXEC).is_file():
        raise MediaPipeNetworkError(f"{SANDBOX_EXEC} not found; MediaPipe cannot run offline")
    return [SANDBOX_EXEC, "-p", SANDBOX_PROFILE, *argv]


def prefetch_models() -> Path:
    """Download missing model files now (the sandboxed process cannot); returns their directory."""
    from ._models import _MODEL_URLS, get_model

    return [get_model(name) for name in _MODEL_URLS][-1].parent


def reexec_offline() -> None:
    """Replace this process with the same command inside the sandbox.

    No-op when the network is already blocked.
    """
    if network_blocked():
        return
    if os.environ.get(_SANDBOXED_ENV) == "1":
        raise MediaPipeNetworkError("the sandbox did not block network access")
    prefetch_models()
    command = offline_command([sys.executable, *sys.orig_argv[1:]])
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(command[0], command, dict(os.environ, **{_SANDBOXED_ENV: "1"}))
