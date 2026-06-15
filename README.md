# ChewSense

ChewSense collects paired eating video and AirPods motion, uses computer
vision to propose mouth states and individual chew/bite events, routes those
events through human review, and uses the finalized labels to train both video
and motion models.

## Products

- **Collect:** `01-DataCollection/ChewSense-DataCollection/`
- **Review:** `04-RealWorldTesting/ChewSense-CycleReviewer/`
- **Live:** `04-RealWorldTesting/ChewSense-RealTime/`

The sole active video pipeline is `02b-VideoAssistedLabeling/`. Motion model
construction, evaluation, registry, and deployment live in
`02-DataPipeline/`.

```text
Collect -> 02b CV candidates -> CycleReviewer -> finalized annotation revision
                                              -> CV training manifest
                                              -> motion training manifest
```

The authoritative concepts are:

- mouth state: closed, opening, open, closing, or unknown;
- chew cycle: closed-to-open-to-closed start, peak, and end;
- bite event: food-entry timestamp;
- chewing bout: interval linking a bite and its associated chews.

A bite event is not a chewing bout.

## Start Here

- [Architecture](docs/ARCHITECTURE.md)
- [Session bundle specification](docs/SESSION_BUNDLE.md)
- [Pipeline runbook](docs/PIPELINE_RUNBOOK.md)
- [Current status](docs/CURRENT_STATUS.md)

## Safety Rules

- Automatic predictions and human corrections are stored separately.
- Review revisions are immutable.
- Unverified sessions are excluded from training by default.
- Training views reference recordings instead of copying them.
- Locked evaluation sessions and the session currently under review never
  enter recalibration.
- Live consumes versioned promoted models and does not own ground-truth
  labeling.

AirPods Pro or AirPods Max with headphone motion support are required for
motion collection and Live testing.
