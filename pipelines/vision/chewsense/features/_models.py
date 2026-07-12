"""Tiny helper: download and cache MediaPipe Tasks model files."""
from __future__ import annotations

import os
import ssl
import urllib.request
from pathlib import Path

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


def _cache_dir() -> Path:
    env = os.environ.get("CHEWSENSE_MODELS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "chewsense" / "models"


def get_model(name: str) -> Path:
    if name not in _MODEL_URLS:
        raise KeyError(f"unknown model: {name}")
    d = _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    if path.exists() and path.stat().st_size > 1024:
        return path
    url = _MODEL_URLS[name]
    tmp = path.with_suffix(path.suffix + ".part")
    with urllib.request.urlopen(url, context=_SSL_CONTEXT) as resp:  # nosec - fixed Google CDN URLs
        tmp.write_bytes(resp.read())
    tmp.replace(path)
    return path
