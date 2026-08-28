# ChewSense

ChewSense is a role-based monorepo for collecting paired eating video and
AirPods motion, reviewing computer-vision chew-cycle and food-entry candidates,
materializing trustworthy supervision, training a personalized temporal motion
model, and field-testing promoted checkpoints.

Start with [START_HERE.md](START_HERE.md).

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
- [Current status](docs/STATUS.md)
- [Blocking work](docs/BLOCKERS.md)
- [Roadmap](docs/ROADMAP.md)

The canonical session and annotation schemas are version `2.0.0`. Version-1
sessions require explicit migration and fresh chew review.
