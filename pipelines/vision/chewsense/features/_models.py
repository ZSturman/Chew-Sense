"""Tiny helper: download and cache MediaPipe Tasks model files."""
from __future__ import annotations

import hashlib
import os
import tempfile
import ssl
import urllib.request
from pathlib import Path

from ..paths import workspace_root

try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()

_MODEL_URLS = {
    "face_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
        "face_landmarker/float16/1/face_landmarker.task",
    "hand_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
        "hand_landmarker/float16/1/hand_landmarker.task",
}


_MODEL_SHA256 = {
    "face_landmarker.task": "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
    "hand_landmarker.task": "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1",
}


def model_provenance() -> dict[str, str]:
    return {name: f"mediapipe-float16-v1:sha256:{digest}" for name, digest in _MODEL_SHA256.items()}


def _cache_dir() -> Path:
    env = os.environ.get("CHEWSENSE_MODELS_DIR")
    if env:
        return Path(env)
    return workspace_root() / "models" / "mediapipe"


def get_model(name: str) -> Path:
    if name not in _MODEL_URLS:
        raise KeyError(f"unknown model: {name}")
    d = _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    expected = _MODEL_SHA256[name]
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"model cache hash mismatch: {path}; move the invalid file aside and retry")
        return path
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=d, suffix=".part", delete=False) as handle:
            temporary = Path(handle.name)
            with urllib.request.urlopen(_MODEL_URLS[name], context=_SSL_CONTEXT, timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
            raise ValueError(f"download hash mismatch for {name}")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
