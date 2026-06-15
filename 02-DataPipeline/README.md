# Motion Data Pipeline

This package owns canonical label ingestion, motion feature construction,
personalized model training, locked evaluation, model registry, promotion, and
CoreML deployment.

Reviewed 02b labels are consumed directly from session bundles. Verified chew
phases enable a two-head model with:

- chewing probability;
- chew-cycle phase (`sin`, `cos`).

Live derives individual motion chews from phase cycles. The older band-power
peak counter is retained only for one-head checkpoint compatibility.

## Main Commands

```bash
python main_new.py from-raw \
  --input data/raw_sessions \
  --video-labels-root data/derived/video_labels

python main_new.py from-field-test \
  --input data/user/field_tests \
  --video-labels-root data/derived/video_labels

python main_new.py evaluate --model models/chewnet.pth
python main_new.py registry --latest
python main_new.py deploy
```

Unverified video labels are rejected by default. Dataset hashes include
reviewed annotations, annotation revisions, and model IDs.

Deployment is blocked unless the exact registry checkpoint passes locked
promotion metrics and minimum verified-session counts.

See:

- [`../docs/PIPELINE_RUNBOOK.md`](../docs/PIPELINE_RUNBOOK.md)
- [`../docs/SESSION_BUNDLE.md`](../docs/SESSION_BUNDLE.md)
- [`../docs/CURRENT_STATUS.md`](../docs/CURRENT_STATUS.md)
