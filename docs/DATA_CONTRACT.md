# Data Contract

The authoritative session bundle schema remains version `1.0.0`. The path
reorganization is not a schema break.

```text
<session_id>/
  session.json
  <video>.mov
  <motion>.csv
  annotations/
    coarse_labels.json
    auto_predictions.json
    review-v0001.json
    canonical_events.json
    motion_labels.csv
  manifests/
    cv_training.json
    motion_training.json
```

## Session Manifest

`session.json` records session and wearer IDs, source app and version,
activity, lifecycle status, recording quality, video and sensor rates,
interruption state, active model IDs, and annotation revision.

Synchronization records a shared host-clock origin, explicit
motion-to-video offset, drift estimate, method, and confidence. New sessions
must not infer alignment from the first motion row.

## Annotation Semantics

- `mouth_state`: closed, opening, open, closing, or unknown.
- `chew_cycle`: start, peak, end, confidence, review status, and optional bout
  link.
- `bite_event`: food-entry timestamp.
- `chewing_bout`: interval following a bite and containing linked chews.

`coarse_labels.json` contains human chewing, not-chewing, or unknown
intervals. Vision runs mouth-state prediction on usable frames and constrains
chew/bite candidates to chewing intervals plus boundary padding.

`auto_predictions.json` is immutable model output.

`review-vNNNN.json` is an immutable human revision. It contains decisions,
edits, added events, rejected candidate IDs, completion state, reviewer, and
model versions.

`canonical_events.json` identifies the latest finalized revision without
deleting earlier revisions.

## Motion Materialization

`motion_labels.csv` retains sensor rows and adds:

| Column | Meaning |
| --- | --- |
| `video_ts` | Motion row mapped into video-relative seconds |
| `eating` | Coarse eating target |
| `mouth_state` | Reviewed or predicted mouth state |
| `chew_phase` | Phase of the linked chew cycle |
| `chew_id` | Stable chew ID |
| `bite_event` | Food-entry indicator on the nearest row |
| `bite_id` | Stable bite ID |
| `bout_id` | Linked chewing-bout ID |
| `label_valid` | Supervised-training validity mask |
| `label_source` | Automatic, human, human-edited, or legacy |
| `model_id` | Automatic proposal model version |
| `annotation_revision` | Immutable review revision |
| `video_confidence` | Visual confidence |

JSON is authoritative for events. CSV is a materialized analysis and
motion-training view.

## Finalization Gate

A session is verified only after every chew and bite candidate is assessed,
missed events are handled, and the final full-video pass is acknowledged.
Only verified sessions enter training by default.
