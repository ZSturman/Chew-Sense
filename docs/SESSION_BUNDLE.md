# Canonical Session Bundle

Schema version `1.0.0` is implemented in
`02b-VideoAssistedLabeling/chewsense/session_bundle.py` and emitted directly by
Collect.

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

Generated diagnostic files may live under
`02-DataPipeline/data/derived/video_labels/<session_id>/`. Media remains in the
session bundle and is referenced by path.

## Session Manifest

`session.json` records:

- schema version, session ID, wearer ID, source app and app version;
- activity, lifecycle status, creation and update times;
- video and motion paths plus optional SHA-256 hashes;
- shared host-clock timing, explicit motion-to-video offset, drift estimate,
  synchronization method, and confidence;
- frame rate, sensor rate, duration, dimensions, and interruption status;
- recording-quality notes;
- active model IDs and current annotation revision.

New recordings use `shared_host_clock`. `legacy_first_motion_fallback` is
low-confidence migration metadata and must not be mistaken for measured
alignment.

## Annotation Files

`coarse_labels.json` contains human chewing, not-chewing, or unknown intervals.
02b uses chewing intervals, with small boundary padding, to constrain chew and
bite candidate generation. Mouth-state prediction still runs over usable
video frames.

`auto_predictions.json` contains the untouched CV output and model IDs.

`review-vNNNN.json` is an immutable human-reviewed revision. Each revision
contains mouth-state intervals, accepted/edited/added chew cycles, bite events,
chewing bouts, rejected candidate IDs, reviewer identity, model IDs, and
completion state.

`canonical_events.json` points downstream tools at the latest finalized
revision. It does not erase older revisions.

## Motion Materialization

`motion_labels.csv` retains the sensor rows and adds:

| Column | Meaning |
| --- | --- |
| `video_ts` | Motion row mapped into video-relative seconds |
| `eating` | Coarse chewing/eating target |
| `mouth_state` | Reviewed or predicted mouth state |
| `chew_phase` | Cycle phase for the linked chew |
| `chew_id` | Stable chew-cycle ID |
| `bite_event` | `1` on the nearest row to a food-entry event |
| `bite_id` | Stable bite-event ID |
| `bout_id` | Linked chewing-bout ID |
| `label_valid` | Whether the row is usable for supervised training |
| `label_source` | Automatic, human, human-edited, or legacy |
| `model_id` | Models that produced the automatic proposal |
| `annotation_revision` | Immutable human annotation revision |
| `video_confidence` | Visual confidence for the assigned event |

JSON is authoritative for point and interval events. CSV is a materialized
analysis and motion-training view.

## Finalization Gate

A session becomes verified only when:

- every proposed chew is accepted, rejected, or edited;
- every proposed bite is accepted, rejected, or edited;
- missed events have been added;
- the reviewer acknowledges the final full-video pass.

`chewsense finalize-session` rejects incomplete reviews unless
`--allow-incomplete` is supplied. Incomplete output is not admitted to training
by default.
