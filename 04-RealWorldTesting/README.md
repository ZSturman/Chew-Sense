# Live Motion Testing

`ChewSense-RealTime/ChewSenseDebuggingModel` is the motion-only Live product.

It:

- captures AirPods motion field tests;
- runs the versioned promoted CoreML model;
- displays chewing probability and phase-derived chew cycles;
- records the active model ID and exports reproducible session bundles.

It does not create ground truth. New human chew, bite, and full-video review
belongs to `ChewSense-CycleReviewer`.

Legacy review source files remain in the Xcode target only so old field-test
annotations can be migrated. The active navigation no longer exposes them.

## Deploy

```bash
cd ../02-DataPipeline
python main_new.py deploy
```

Deployment requires an exact registry checkpoint and a passing locked
promotion report. See [`../docs/PIPELINE_RUNBOOK.md`](../docs/PIPELINE_RUNBOOK.md).

## Field Tests

Every recording stores the bundled model ID in session metadata. Export the
bundle and import it with:

```bash
cd ../02-DataPipeline
python main_new.py from-field-test --input data/user/field_tests
```

Canonical review artifacts found in imported legacy bundles are preserved.
