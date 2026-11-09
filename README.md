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
  It runs alongside ChewNet by default and records its own results. Select the
  experimental model in **New model check** to display its predictions live.
- **Training and Evaluation window** in Review (⇧⌘T): recordings, checks,
  labels, splits, model, metrics, prediction timelines, errors and what to
  record next, from `workspace/experiments/eating/pipeline_status.json`.

```sh
.venv/bin/chewsense-motion pipeline-status        # refresh what Review shows
.venv/bin/chewsense-motion eating-experiment --dev-only   # retrain/check; locked test untouched
.venv/bin/chewsense-motion assist-propose --loop   # chewing-span proposals for loop recordings
.venv/bin/chewsense-motion assist-eval             # proposal accuracy against human labels
.venv/bin/chewsense-motion behavior-eval           # bout, pause, cadence and alignment checks
.venv/bin/chewsense-motion chew-count-eval         # AirPods-only cadence/chew count vs frozen hand-marked chews
.venv/bin/chewsense-motion eating-export --install-live && .venv/bin/chewsense-motion eating-parity
```

**Apps and how to move between them:** [docs/APPS.md](docs/APPS.md) lists every Chew Sense app, where it is installed, and the links between Collect, Live and Review (Review is installed in `/Applications/Chew Sense Review.app`).

**Where we are (same message in every app, November 8).** The loop service
computes one status: the current step (Collect → Label → Train → Test), the
numbered next steps with the app each happens in, and plain answers to "Can I
test a model?", "Is visual chew detection working?" and "Do we need more
data?". Collect shows it under **What happens next**. Review shows it in the
sidebar, on the empty main window and at the top of the Pipeline and Training
windows. Live shows it under **Where we are** after a one-time, status-only
pairing (Review → Pipeline → Pair iPhone, then Live → **Connect to your Mac**).
The service rewrites `workspace/loop/project_status.json` whenever the evidence
changes. Without the service, refresh it with:

```sh
.venv/bin/chewsense-vision loop project-status    # reads loop files only; no analysis or catalog publish
```

November 6 [physical-device and pipeline validation](docs/DEVICE_PIPELINE_2026-11-06.md):
Collect and Live were installed on iPhone. Eighteen Collect and 22 Live fixture
tests passed on the phone. Two real recordings, including a short Natural Meal,
completed the running Mac loop. System logs confirmed that MediaPipe telemetry
attempts were blocked. The intentionally shortened meal remains diagnostic.

Live now opens on **Connect AirPods**, **New model check**, and **Recent checks**.
The connection panel is shared with Collect and includes the iOS audio picker,
a test sound, and a fresh-motion indicator. In a physical camera-free check on
November 7, Live saved 1,590 motion samples and 59 experimental model windows.
All 59 decisions matched Mac replay and the AirDropped export matched the phone
copy. Recognition accuracy and battery use still need separate measurements.

Training now rejects incomplete guided takes and declared motion-hash mismatches.
The Phase 2 experiment accidentally included 46 windows from incomplete Quiet
Sitting; historical artifacts remain intact. Corrected development-only training
selected gradient boosting again (F1 .8683, precision .8664, recall .8703).
No new locked-test evaluation or deployment was performed. The dashboard shows
this development run separately from Live's installed experimental detector.

Collect's Mac connection reports denied Local Network permission and offers
**Open Settings**. See [current next steps](docs/NEXT_STEPS.md) for the independent
baseline and the next recordings to collect.

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
