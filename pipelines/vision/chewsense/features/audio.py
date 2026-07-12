"""Audio features. Computes per-frame (video-frame-aligned) RMS and a
voice-activity flag using webrtcvad. Audio is optional; if no audio is
extracted the pipeline degrades gracefully.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import webrtcvad
    _HAS_VAD = True
except Exception:
    _HAS_VAD = False


@dataclass
class AudioFrameFeatures:
    is_speaking: bool = False
    rms: float = 0.0


def compute_audio_frame_features(
    audio: np.ndarray | None,
    sample_rate: int,
    timestamps: np.ndarray,
    cfg: dict,
) -> list[AudioFrameFeatures]:
    """Align audio to video timestamps. For each ts we look at a small audio window
    around that time and compute RMS + VAD.
    """
    n = len(timestamps)
    out = [AudioFrameFeatures() for _ in range(n)]
    if audio is None or len(audio) == 0:
        return out

    vad = webrtcvad.Vad(cfg["audio"]["vad_aggressiveness"]) if _HAS_VAD else None
    frame_ms = cfg["audio"]["vad_frame_ms"]
    win = int(sample_rate * frame_ms / 1000.0)

    # webrtcvad needs 16-bit PCM at 8/16/32/48 kHz
    audio_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)

    for i, ts in enumerate(timestamps):
        center = int(ts * sample_rate)
        a = max(0, center - win // 2)
        b = min(len(audio_int16), a + win)
        a = max(0, b - win)
        if b - a < win:
            continue
        chunk = audio_int16[a:b]
        rms = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32768.0) ** 2)))
        speaking = False
        if vad is not None and sample_rate in (8000, 16000, 32000, 48000):
            try:
                speaking = vad.is_speech(chunk.tobytes(), sample_rate)
            except Exception:
                speaking = False
        out[i] = AudioFrameFeatures(is_speaking=speaking, rms=rms)
    return out
