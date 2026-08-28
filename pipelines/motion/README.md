# Motion Data Pipeline

This package owns canonical label ingestion, temporal motion-window
construction, personalized model training, locked evaluation, model registry,
promotion, and CoreML deployment.

The v2 materializer reads exact finalized session lineage and emits three
seconds of six-axis IMU at 50 Hz plus a gap-validity mask. The causal TCN
outputs eating, an individual-chew event logit, phase sine/cosine, and phase
confidence. The event head counts chews; phase is auxiliary evidence.

The older twelve-feature MLP remains readable/trainable only for binary eating
compatibility. It cannot pass temporal chew promotion.

## Main Commands

```bash
chewsense-motion materialize-temporal --input ../../workspace/sessions/raw
chewsense-motion from-raw --arch causal_tcn
chewsense-motion evaluate \
  --registry-entry ../../workspace/models/registry/<model_id> \
  --features <printed-temporal-features-dir> --split test_locked
chewsense-motion registry --latest
chewsense-motion deploy
```

The retained `sample`, `from-features`, and `from-field-test` commands reproduce
historical binary eating models only and require an explicit `--arch mlp`.
They cannot promote individual-chew coaching.

Coarse-only and migrated legacy sessions may supervise eating, but their event
and phase masks are zero. Reviewed event supervision requires exact candidate,
review, materialized, pointer, and motion-label hashes.

Deployment is blocked unless the exact registry checkpoint passes locked
promotion metrics and minimum verified-session counts.

See:

- [`../../docs/RUNBOOK.md`](../../docs/RUNBOOK.md)
- [`../../docs/DATA_CONTRACT.md`](../../docs/DATA_CONTRACT.md)
- [`../../docs/STATUS.md`](../../docs/STATUS.md)
