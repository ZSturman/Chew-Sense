# ChewSense

ChewSense is a role-based monorepo for collecting paired eating video and
AirPods motion, reviewing computer-vision chew-cycle and food-entry candidates,
materializing trustworthy supervision, training a personalized temporal motion
model, and field-testing promoted checkpoints.

For the first physical experiment, follow [Phase 0: recording and review](docs/PHASE0.md).
The software path has automated, synthetic-training and legacy-video smoke
evidence. A new physical baseline has been captured; synchronization and
detection accuracy still require independent annotation and timing checks.
No wearer model has been trained or promoted.

After Phase 0, the [guided collection loop](docs/GUIDED_LOOP.md) adds
**Collect Required Data** on the iPhone, a Mac service (`tools/loop-service
install`) that receives and processes recordings automatically, and a
**Pipeline** window in Review. Capture, pairing and transfer have first-device
evidence; detailed review, physical timing and recognition accuracy remain pending.

On November 2, physical iPad/AirPods baseline, reconnect and timing checks were
received and processed. Collect now shows the next action, verified-data goals
and six pipeline stages, and requires coarse review before guided upload. The
short timing check passes the automatic three-axis comparison; human clock
confirmation and independent event review remain separate gates. Two complete
learning cycles passed on isolated fixtures, including fixed holdouts and new
collection requests. No wearer model has been trained or promoted. See the
[physical test record and next actions](docs/DEVICE_LOOP_2026-11-02.md).

On November 3, an [independent audit](docs/AUDIT_2026-11-03.md) trained and
evaluated the first motion-only chewing-bout detectors on real recordings:
32 recordings, one wearer, locked session splits. Run
`.venv/bin/chewsense-motion eating-experiment` to reproduce the HTML report
in `workspace/experiments/eating/`. These are pilot results; nothing was
promoted or deployed.

Phase 2 (November 3, [report](docs/PHASE2_2026-11-03.md)) added assisted
labeling, an on-device version of the selected detector and a training
dashboard:

- **Assisted labeling.** After Vision runs, the Mac proposes chewing spans
  where video and AirPods motion agree. Review → **Chewing spans** lets you
  accept agreements together and decide only the rest. Proposals are never
  labels; saved decisions keep their origin and are used for training only.
- **On-device detector.** The selected gradient-boosting model is exported as
  `apps/live-ios/ChewSenseLive/EatingDetector/eating-detector.json`. Live's
  Swift runtime matches the Python reference exactly on all recorded sessions.
  It runs in shadow by default; Settings can make it drive Live
  (experimental, not yet tested on a physical device).
- **Training and Evaluation window** in Review (⇧⌘T): recordings, checks,
  labels, splits, model, metrics, prediction timelines, errors and what to
  record next, from `workspace/experiments/eating/pipeline_status.json`.

```sh
.venv/bin/chewsense-motion pipeline-status        # refresh what Review shows
.venv/bin/chewsense-motion eating-experiment --dev-only   # retrain/check; locked test untouched
.venv/bin/chewsense-motion assist-propose --loop   # chewing-span proposals for loop recordings
.venv/bin/chewsense-motion assist-eval             # proposal accuracy against human labels
.venv/bin/chewsense-motion behavior-eval           # bout, pause, cadence and alignment checks
.venv/bin/chewsense-motion eating-export --install-live && .venv/bin/chewsense-motion eating-parity
```

Data-integrity update (November 6): training rejects incomplete guided takes,
checks declared motion hashes, and reports corrupt recordings individually.
The Phase 2 reference accidentally included 46 development windows from the
shortened Quiet Sitting diagnostic; its historical scores and bundled detector
are retained, pending a corrected development-only comparison. Assisted review
cannot make that incomplete take eligible. The locked test is unchanged.

Start with [START_HERE.md](START_HERE.md) for the broader repository.

## Main Products

| Product | Path | Purpose |
| --- | --- | --- |
| Collect | `apps/collect-ios/` | Record video, AirPods motion, metadata, synchronization, and coarse chewing labels |
| Review | `apps/review-macos/` | Verify chew anchors, food entries and the final full-video pass; review proposed chewing spans; see training and evaluation status |
| Live | `apps/live-ios/` | Run motion-only detectors during field testing (bundled ChewNet; exported detector in shadow or experimental mode) |

## Active Pipelines

- `pipelines/vision/`: mouth-state telemetry, fused visual chew/food-entry
  candidate generation, audit, and finalization.
- `pipelines/motion/`: reviewed-label import, motion training, evaluation,
  registry, promotion, and Live deployment.

Local recordings, derived outputs, models, manifests, and reports belong in
`workspace/`, or in the absolute path set by `CHEWSENSE_WORKSPACE`. They are
not source code and are ignored by Git.

## Documentation

- [Start here](START_HERE.md)
- [Documentation index](docs/README.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Runbook](docs/RUNBOOK.md)
- [Data contract](docs/DATA_CONTRACT.md)
- [Guided collection loop](docs/GUIDED_LOOP.md)
- [Current status](docs/STATUS.md)
- [Blocking work](docs/BLOCKERS.md)
- [Roadmap](docs/ROADMAP.md)

The canonical session and annotation schemas are version `2.0.0`. Version-1
sessions require explicit migration and fresh chew review.
