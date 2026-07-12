# Motion Data Pipeline

This package owns canonical label ingestion, motion feature construction,
personalized model training, locked evaluation, model registry, promotion, and
CoreML deployment.

Reviewed vision labels are consumed directly from session bundles. Verified chew
phases enable a two-head model with:

- chewing probability;
- chew-cycle phase (`sin`, `cos`).

Live derives individual motion chews from phase cycles. The older band-power
peak counter is retained only for one-head checkpoint compatibility.

## Main Commands

```bash
chewsense-motion from-raw
chewsense-motion from-field-test
chewsense-motion evaluate
chewsense-motion registry --latest
chewsense-motion deploy
```

Unverified video labels are rejected by default. Dataset hashes include
reviewed annotations, annotation revisions, and model IDs.

Deployment is blocked unless the exact registry checkpoint passes locked
promotion metrics and minimum verified-session counts.

See:

- [`../../docs/RUNBOOK.md`](../../docs/RUNBOOK.md)
- [`../../docs/DATA_CONTRACT.md`](../../docs/DATA_CONTRACT.md)
- [`../../docs/STATUS.md`](../../docs/STATUS.md)
