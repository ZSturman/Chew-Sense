"""MediaPipe runs only without network access (its builds send usage reports to Google).

The sandbox checks start a real ``sandbox-exec`` child; no MediaPipe task or
model is loaded here, and the socket probe sends no packets.
"""
from __future__ import annotations

import errno
import subprocess
import sys
from pathlib import Path

import pytest

from chewsense.features import face, hands, offline
from chewsense.loop import processing

macos = pytest.mark.skipif(not Path(offline.SANDBOX_EXEC).is_file(), reason="needs macOS sandbox-exec")
PROBE = "from chewsense.features.offline import network_blocked; print(network_blocked())"


@macos
def test_sandbox_blocks_network_and_probe_detects_it():
    assert offline.network_blocked() is False
    plain = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, check=True)
    sandboxed = subprocess.run(offline.offline_command([sys.executable, "-c", PROBE]),
                               capture_output=True, text=True, check=True)
    assert plain.stdout.strip() == "False"
    assert sandboxed.stdout.strip() == "True"


@pytest.mark.parametrize("extractor", [face.FaceExtractor, hands.HandsExtractor])
def test_landmarkers_refuse_network_before_loading_a_model(monkeypatch, extractor):
    def no_model(name):
        raise AssertionError("model loaded before the network check")

    monkeypatch.setattr(face, "get_model", no_model)
    monkeypatch.setattr(hands, "get_model", no_model)
    monkeypatch.setattr(offline, "network_blocked", lambda: False)
    with pytest.raises(offline.MediaPipeNetworkError, match="usage reports"):
        extractor({})


def test_no_environment_override_bypasses_the_check(monkeypatch):
    monkeypatch.setattr(offline, "network_blocked", lambda: False)
    monkeypatch.setenv("CHEWSENSE_ALLOW_MEDIAPIPE_NETWORK", "1")
    with pytest.raises(offline.MediaPipeNetworkError):
        offline.require_offline("FaceLandmarker")


@pytest.mark.parametrize("error_number", [errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EACCES,
                                         errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT])
def test_socket_creation_failure_is_not_evidence_of_network_isolation(monkeypatch, error_number):
    def fail(*args):
        raise OSError(error_number, "socket unavailable")

    monkeypatch.setattr(offline.socket, "socket", fail)
    assert not offline.network_blocked()
    with pytest.raises(offline.MediaPipeNetworkError):
        offline.require_offline("FaceLandmarker")


def test_explicit_sandbox_denial_at_socket_creation_is_recognized(monkeypatch):
    def denied(*args):
        raise OSError(errno.EPERM, "sandbox denied socket")

    monkeypatch.setattr(offline.socket, "socket", denied)
    assert offline.network_blocked()


@macos
def test_reexec_runs_the_same_command_sandboxed_after_fetching_models(monkeypatch):
    calls = []
    monkeypatch.setattr(offline, "network_blocked", lambda: False)
    monkeypatch.setattr(offline, "prefetch_models", lambda: calls.append("prefetch"))
    monkeypatch.setattr(sys, "orig_argv", ["python", "run_on_sessions.py", "--only", "S1"])
    monkeypatch.setattr(offline.os, "execve",
                        lambda path, argv, env: calls.append((path, argv, env[offline._SANDBOXED_ENV])))
    monkeypatch.delenv(offline._SANDBOXED_ENV, raising=False)
    offline.reexec_offline()
    assert calls == ["prefetch", (offline.SANDBOX_EXEC,
                                  [offline.SANDBOX_EXEC, "-p", offline.SANDBOX_PROFILE,
                                   sys.executable, "run_on_sessions.py", "--only", "S1"], "1")]


def test_reexec_is_a_noop_when_already_offline_and_never_loops(monkeypatch):
    monkeypatch.setattr(offline.os, "execve", lambda *a: pytest.fail("re-executed"))
    monkeypatch.setattr(offline, "network_blocked", lambda: True)
    offline.reexec_offline()
    monkeypatch.setattr(offline, "network_blocked", lambda: False)
    monkeypatch.setenv(offline._SANDBOXED_ENV, "1")
    with pytest.raises(offline.MediaPipeNetworkError, match="did not block"):
        offline.reexec_offline()


@macos
def test_loop_vision_subprocess_is_sandboxed_with_the_prefetched_models(tmp_path, monkeypatch):
    seen = {}

    class Finished:
        pid = 0

        def __init__(self, command, env, **kwargs):
            seen.update(command=command, env=env)

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(processing, "prefetch_models", lambda: tmp_path / "models")
    monkeypatch.setattr(processing.subprocess, "Popen", Finished)
    processing.VisionRunner()(tmp_path, "S1", tmp_path / "vision.log")
    assert seen["command"][:3] == [offline.SANDBOX_EXEC, "-p", offline.SANDBOX_PROFILE]
    assert seen["command"][3:] == [sys.executable, str(processing.RUN_ON_SESSIONS), "--only", "S1"]
    assert seen["env"]["CHEWSENSE_MODELS_DIR"] == str(tmp_path / "models")
