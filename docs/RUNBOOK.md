# Runbook

Run commands from the repository root.

## Install CLIs

```bash
python3 -m pip install -e pipelines/vision -e pipelines/motion
```

Available commands:

```text
chewsense-vision
chewsense-motion
```

## Workspace

Default: `<repo>/workspace`.

```bash
export CHEWSENSE_WORKSPACE="/absolute/path/to/workspace"
```

The path must be absolute. Confirm it with:

```bash
python3 tools/migrate_workspace.py --print-workspace
```

## Collect

Open `apps/collect-ios/ChewSenseCollect.xcodeproj` and run the
`ChewSenseCollect` scheme. Export each canonical session into
`workspace/sessions/raw/`.

Migrate an old session:

```bash
chewsense-vision migrate-session \
  workspace/sessions/raw/<session_id> \
  --source-app legacy_import
```

## Vision

```bash
python3 pipelines/vision/run_on_sessions.py --only <session_id>
python3 pipelines/vision/run_on_sessions.py
```

Use `--force` to regenerate automatic outputs. Results land in
`workspace/derived/vision/<session_id>/`.

## Review

Open `apps/review-macos/ChewSenseReview.xcodeproj`, run `ChewSenseReview`, and
select the workspace directory.

For each candidate:

1. Accept, reject, or edit the chew.
2. Review bite events with wider context.
3. Add missed events.
4. Watch the full video.
5. Acknowledge the full-video pass and save.

## Finalize

```bash
chewsense-vision finalize-session \
  workspace/sessions/raw/<session_id> \
  --derived workspace/derived/vision/<session_id>
```

Incomplete reviews are rejected unless `--allow-incomplete` is explicitly
used. Incomplete sessions are not admitted to training by default.

## Train Vision

```bash
chewsense-vision pseudolabel \
  workspace/derived/vision/<session_id> \
  --annotations workspace/sessions/raw/<session_id>/annotations/canonical_events.json \
  --out workspace/derived/vision/<session_id>/reviewed_labels.parquet
```

Train from reviewed frame labels with `chewsense-vision train-chew-head`.

## Train Motion

```bash
chewsense-motion from-raw --no-wandb
```

Import field-test bundles:

```bash
chewsense-motion from-field-test
```

Both commands reject unverified reviewed labels by default.

## Evaluate And Promote

```bash
chewsense-motion evaluate
chewsense-motion registry --latest
chewsense-motion status
chewsense-motion compare
chewsense-motion deploy
```

Production deployment requires an exact registry checkpoint and a passing
locked evaluation. `--development-override` must not be used as a production
promotion.

## Tests

```bash
PYTHONPATH=pipelines/vision python3 -m pytest -q pipelines/vision/tests
PYTHONPATH=pipelines/motion python3 -m pytest -q pipelines/motion/tests

xcodebuild -project apps/collect-ios/ChewSenseCollect.xcodeproj \
  -scheme ChewSenseCollect -sdk iphoneos CODE_SIGNING_ALLOWED=NO build

xcodebuild -project apps/review-macos/ChewSenseReview.xcodeproj \
  -scheme ChewSenseReview CODE_SIGNING_ALLOWED=NO build

xcodebuild -project apps/live-ios/ChewSenseLive.xcodeproj \
  -scheme ChewSenseLive -sdk iphoneos CODE_SIGNING_ALLOWED=NO build
```

## Troubleshooting

- No sessions in Review: select `workspace/`, then confirm both
  `sessions/raw/<id>` and `derived/vision/<id>` exist.
- Training rejects a session: complete every candidate and the full-video
  pass, save, then finalize again.
- Wrong local paths: unset `CHEWSENSE_WORKSPACE` or set it to an absolute
  directory.
- Live refuses deployment: inspect the latest registry metrics and promotion
  report; do not bypass locked evaluation for production.
