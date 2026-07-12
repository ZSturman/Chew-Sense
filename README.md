# ChewSense

ChewSense is a role-based monorepo for collecting paired eating video and
AirPods motion, reviewing computer-vision chew and bite candidates, training
video and motion models, and field-testing promoted motion models.

Start with [START_HERE.md](START_HERE.md).

## Main Products

| Product | Path | Purpose |
| --- | --- | --- |
| Collect | `apps/collect-ios/` | Record video, AirPods motion, metadata, synchronization, and coarse chewing labels |
| Review | `apps/review-macos/` | Verify chew cycles, bite events, boundaries, missed events, and the final full-video pass |
| Live | `apps/live-ios/` | Run promoted motion-only models during field testing |

## Active Pipelines

- `pipelines/vision/`: mouth state and visual chew/bite candidate generation,
  finalization, and CV training.
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

The canonical annotation schema remains version `1.0.0`.
