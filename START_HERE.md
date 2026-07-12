# Start Here

## Which App Do I Use?

1. **ChewSenseCollect** in `apps/collect-ios/`
   Use this on iPhone when creating a new paired video and AirPods-motion
   session. It owns recording, synchronization metadata, and coarse
   chewing/not-chewing labels.
2. **ChewSenseReview** in `apps/review-macos/`
   Use this on Mac after the vision pipeline has generated candidates. It is
   the only authoritative human-review app.
3. **ChewSenseLive** in `apps/live-ios/`
   Use this only for motion-only field testing with a promoted model. It does
   not create ground truth.

Do not use anything under `archive/` as part of the active pipeline.

## Pipeline

```text
Collect
  -> Vision prediction
  -> Review chew clips and bite events
  -> Final full-video pass
  -> Finalize immutable annotation revision
  -> CV training manifest + motion training manifest
  -> Train and evaluate
  -> Promote
  -> Live
```

The two training manifests reference the same recording. Media is not
duplicated.

## Workspace

The default local workspace is `<repo>/workspace`.

```bash
export CHEWSENSE_WORKSPACE="/absolute/path/to/another/workspace"
```

The override must be absolute. ChewSenseReview asks you to select the
workspace directory, not the repository root.

## Open The Apps

```text
apps/collect-ios/ChewSenseCollect.xcodeproj
apps/review-macos/ChewSenseReview.xcodeproj
apps/live-ios/ChewSenseLive.xcodeproj
```

Build schemes:

```text
ChewSenseCollect
ChewSenseReview
ChewSenseLive
```

## One-Session Smoke Test

Install the local CLIs:

```bash
python3 -m pip install -e pipelines/vision -e pipelines/motion
```

For an existing session in `workspace/sessions/raw/<session_id>`:

```bash
chewsense-vision migrate-session \
  workspace/sessions/raw/<session_id> \
  --source-app collect

python3 pipelines/vision/run_on_sessions.py --only <session_id>
```

Expected output:

```text
workspace/derived/vision/<session_id>/
  events.json
  frame_predictions.csv
  chew_cycles.json
  motion_aligned_labels.csv
```

Open ChewSenseReview, select `workspace/`, assess every chew and bite, add
missed events, complete the full-video pass, and save.

Finalize:

```bash
chewsense-vision finalize-session \
  workspace/sessions/raw/<session_id> \
  --derived workspace/derived/vision/<session_id>
```

Expected session outputs:

```text
annotations/review-vNNNN.json
annotations/canonical_events.json
annotations/motion_labels.csv
manifests/cv_training.json
manifests/motion_training.json
```

Train the motion pipeline only from verified sessions:

```bash
chewsense-motion from-raw --no-wandb
```

Inspect registry and promotion state:

```bash
chewsense-motion registry --latest
chewsense-motion status
```

## Before Using Live

Do not deploy a replacement model until it passes locked evaluation and the
promotion requirements in [docs/ROADMAP.md](docs/ROADMAP.md). A development
override is for non-production device testing only.

## What Needs Attention Next?

The ordered blockers are maintained in
[docs/BLOCKERS.md](docs/BLOCKERS.md). Current operational truth is in
[docs/STATUS.md](docs/STATUS.md); commands and troubleshooting are in
[docs/RUNBOOK.md](docs/RUNBOOK.md).
