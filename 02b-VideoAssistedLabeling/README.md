# 02b Video-Assisted Labeling

This is the sole active computer-vision labeling pipeline for ChewSense.

It reads a canonical session bundle, predicts mouth state across usable video
frames, generates chew-cycle and bite-event candidates only inside padded
human-marked chewing intervals, and prepares the session for CycleReviewer.

## Commands

```bash
pip install -e .
chewsense migrate-session <session-directory>
python run_on_sessions.py --only <session-id>
chewsense finalize-session <session-directory> --derived <derived-directory>
```

`finalize-session` writes an immutable reviewed revision, canonical event JSON,
row-level motion labels, and separate CV and motion training manifests.

See:

- [`../docs/SESSION_BUNDLE.md`](../docs/SESSION_BUNDLE.md)
- [`../docs/PIPELINE_RUNBOOK.md`](../docs/PIPELINE_RUNBOOK.md)

The older `pipeline/stroke_seeds.py`, `pipeline/visual_labeler.py`, and future
bite-segmentation prototype are fixture-parity references, not alternate
production entry points.
