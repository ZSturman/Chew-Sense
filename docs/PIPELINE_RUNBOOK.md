# Pipeline Runbook

Run commands from the repository root unless a step says otherwise.

## 1. Collect

Use the Collect iOS app to record video and AirPods motion. It writes
`session.json` with shared-host-clock synchronization and refreshes
`annotations/coarse_labels.json` after coarse labeling.

For old sessions:

```bash
cd 02b-VideoAssistedLabeling
pip install -e .
chewsense migrate-tree ../02-DataPipeline/data/raw_sessions
```

Migrated legacy timing is explicitly marked low confidence.

## 2. Generate CV Candidates

```bash
cd 02b-VideoAssistedLabeling
python run_on_sessions.py --limit 1
python run_on_sessions.py
```

Use `--only <session_id>` for one session and `--force` to regenerate
automatic predictions. 02b predicts mouth state on usable frames and limits
chew/bite candidates to human-marked chewing intervals.

## 3. Review

Open:

```text
04-RealWorldTesting/ChewSense-CycleReviewer/ChewSenseCycleReviewer.xcodeproj
```

For each chew, review the looping closed-to-open-to-closed clip with at least
five context frames. Accept, reject, or edit its boundaries. Review bite
events with wider context, add missed events, then watch the full video and
acknowledge the final pass.

Save writes a mutable working file and an immutable numbered review snapshot.

## 4. Finalize

```bash
cd 02b-VideoAssistedLabeling
chewsense finalize-session \
  ../02-DataPipeline/data/raw_sessions/<session_id> \
  --derived ../02-DataPipeline/data/derived/video_labels/<session_id>
```

This writes canonical reviewed JSON, row-level motion labels, and separate CV
and motion training manifests without copying the recording.

## 5. Retrain CV

Build frame labels from the verified human revision, not the original
automatic event file:

```bash
cd 02b-VideoAssistedLabeling
chewsense pseudolabel \
  ../02-DataPipeline/data/derived/video_labels/<session_id> \
  --annotations ../02-DataPipeline/data/raw_sessions/<session_id>/annotations/canonical_events.json \
  --out ../02-DataPipeline/data/derived/video_labels/<session_id>/reviewed_labels.parquet

chewsense train-chew-head \
  --pair ../02-DataPipeline/data/derived/video_labels/<session_id>/frame_predictions.parquet:../02-DataPipeline/data/derived/video_labels/<session_id>/reviewed_labels.parquet \
  --out models/chew_head.pt
```

The deterministic MediaPipe/FSM implementation remains the fallback.

## 6. Train Motion

```bash
cd 02-DataPipeline
python main_new.py from-raw \
  --input data/raw_sessions \
  --video-labels-root data/derived/video_labels \
  --no-wandb
```

Verified reviewed phases automatically enable the two-head motion model:
chewing probability plus chew phase. Sessions with unverified video labels are
rejected unless the explicit development-only override is supplied.

Imported field-test bundles preserve canonical review artifacts:

```bash
python main_new.py from-field-test \
  --input data/user/field_tests \
  --video-labels-root data/derived/video_labels
```

## 7. Evaluate And Promote

Initial personalized splits are chronological: earlier sessions train, later
sessions validate, and the newest holdout sessions become permanent locks.
Never train on the current review session, `validation_locked`, `test_locked`,
or `gold_locked`.

Promotion requires:

- mouth-state macro F1 at least `0.85` for the CV model;
- chew F1 at 250 ms at least `0.80`;
- chew-count error at most `0.10`;
- eating recall at least `0.90`;
- hard-negative false-positive rate at most `0.15`;
- at least 20 verified eating and 20 verified hard-negative sessions.

Deploying the motion model:

```bash
python main_new.py deploy
```

Deployment requires an exact versioned registry checkpoint and a passing
`test_locked` or `gold_locked` promotion report. A
`--development-override` build is recorded as unpromoted and must not be used
as a production Live model.

## 8. Live Field Test

Live displays motion-only predictions, records the active model ID, and
exports field-test bundles. It does not own human labels. Bring a recording
back through 02b and CycleReviewer when ground truth is needed.

## Verification Commands

```bash
cd 02b-VideoAssistedLabeling
PYTHONPATH=. python3 -m pytest -q tests

cd ../02-DataPipeline
PYTHONPATH=. python3 -m pytest -q tests
```
