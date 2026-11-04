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

Start with [START_HERE.md](START_HERE.md) for the broader repository.

## Main Products

| Product | Path | Purpose |
| --- | --- | --- |
| Collect | `apps/collect-ios/` | Record video, AirPods motion, metadata, synchronization, and coarse chewing labels |
| Review | `apps/review-macos/` | Verify closed/open/closed chew anchors, food-entry events, missed events, and the final full-video pass |
| Live | `apps/live-ios/` | Run promoted motion-only models during field testing |

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
