# 02b Video-Assisted Labeling

This is the sole active computer-vision labeling pipeline for ChewSense.

It reads a canonical session bundle, predicts mouth state across usable video
frames, generates chew-cycle and bite-event candidates only inside padded
human-marked chewing intervals, and prepares the session for CycleReviewer.

## Commands

```bash
pip install -e .
chewsense-vision migrate-session <session-directory>
python run_on_sessions.py --only <session-id>
chewsense-vision finalize-session <session-directory> --derived <derived-directory>
```

`finalize-session` writes an immutable reviewed revision, canonical event JSON,
row-level motion labels, and separate CV and motion training manifests.

See:

- [`../../docs/DATA_CONTRACT.md`](../../docs/DATA_CONTRACT.md)
- [`../../docs/RUNBOOK.md`](../../docs/RUNBOOK.md)

The retired stroke-seed, visual-labeler, and bite-segmentation implementations
are archived references, not alternate production entry points.


## Phase 0 validation

See [PHASE0.md](../../docs/PHASE0.md) for `prepare-experiment`, `review-coarse`,
`init-blind-review`, `audit-timing` and `evaluate-experiment`. ffprobe is required
for display timestamps; no nominal-FPS fallback is used. Existing frame-based
rules run on a documented 30 Hz analysis grid with source-frame mappings and
missing-evidence masks. Phase 0 uses original video playback; annotated
re-encodes are not generated. The MediaPipe v1 assets are SHA-256 pinned.

## MediaPipe runs offline

MediaPipe's PyPI wheels (0.10.22 and later; this lock pins 0.10.35) send usage
reports to Google (`play.googleapis.com`). Google provides no switch to turn
them off. The reports contain task, mode, latency, frame-count and version
metadata, not frames (see `chewsense/features/offline.py`). Landmarkers are
therefore created only inside `sandbox-exec` with all sockets and DNS denied:

- the loop's Vision step launches `run_on_sessions.py` sandboxed;
- `chewsense-vision run`, `extract-features` and `run_on_sessions.py` re-run
  themselves sandboxed (same PID) after fetching any missing model;
- `FaceExtractor` and `HandsExtractor` refuse to start in any other process
  with network access.

There is no override. In the sandbox, the log shows `Failed to send to clearcut:
INTERNAL: ResponseInfo[...]`; this is the expected, blocked attempt.

## Guided loop (`chewsense/loop/`)

`chewsense-vision loop serve|pair|status|import|analyze|experiments|install-agent`
runs the Mac service: experiment registry/catalog, TLS device API and loopback
admin API, verified import, automatic processing with this package's existing
Vision, review tasks, evaluation on frozen blind reviews, weakness analysis,
collection planning and gated experimental training. Policy:
`configs/loop.yaml`; seed experiments: `configs/experiments/`. Tests:
`tests/test_loop.py`. See [GUIDED_LOOP.md](../../docs/GUIDED_LOOP.md).
