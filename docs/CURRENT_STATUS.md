# Current Status

Updated June 15, 2026.

## Implemented

- Canonical versioned session bundle in Swift and Python.
- Explicit shared-clock synchronization for new Collect recordings.
- Legacy session and tree migration commands.
- 02b as the sole active video pipeline.
- Coarse-interval-constrained chew and bite candidate generation.
- Separate mouth states, chew cycles, bite events, and chewing bouts.
- CycleReviewer chew clip queue, bite review, missed-event insertion, boundary
  editing, immutable snapshots, and full-video completion gate.
- Finalization into canonical JSON, row-level motion labels, and two
  nonduplicating training manifests.
- Training ingestion of reviewed 02b labels with verification gating.
- Dataset hashes that include annotations, revisions, and model IDs.
- Two-head motion training targets for eating probability and chew phase.
- CV frame-label construction from verified human revisions.
- Field-test import that preserves review artifacts.
- Registry-backed Live promotion gate.
- Live navigation no longer exposes ground-truth editing.

## Verification

- 02b Python tests pass.
- 02-DataPipeline Python tests pass.
- CycleReviewer builds with code signing disabled.
- Collect and Live build for a generic iOS device with code signing disabled.
- Physical-device validation is still required for camera, AirPods timing,
  interrupted capture, and end-to-end export.

## Not Yet Promotion-Ready

No current model should be promoted as the first personalized replacement
until the locked evaluation includes chew-event metrics and the dataset has at
least 20 verified eating and 20 varied hard-negative sessions.

Motion-only food-entry detection remains experimental and is not a primary
model output. The deterministic MediaPipe/FSM path remains the CV fallback.

## Remaining Repository Work

Collect and Live are still dirty nested git repositories represented by broken
parent gitlinks. Their runtime behavior is integrated, but history-preserving
monorepo absorption must wait until those worktrees are checkpointed. See
`docs/ARCHITECTURE.md` for the safe sequence.

Old stroke, visual-labeler, bite-segmentation, and Live-review implementations
are retained only for migration and fixture parity. They should be moved under
`ProjectHistory/` after all unique behavior and annotations have canonical
equivalents.
