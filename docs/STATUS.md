# Current Status

Updated June 15, 2026.

## Main Apps

- `ChewSenseCollect`: canonical collection app.
- `ChewSenseReview`: canonical review app.
- `ChewSenseLive`: promoted-model field-test app.

DemoApp, Showcase, FutureStages, the visual labeler, stroke seeder, and stale
Live mini-apps are archived.

## Implemented

- Role-based source layout and separate local workspace.
- `CHEWSENSE_WORKSPACE` path resolution in both Python pipelines.
- Canonical session bundle schema `1.0.0` in Swift and Python.
- Shared-clock synchronization metadata for new Collect recordings.
- Vision candidate generation constrained by coarse chewing intervals.
- Separate mouth states, chew cycles, bite events, and chewing bouts.
- Review queue, boundary editing, missed-event insertion, bite review, and
  full-video completion gate.
- Immutable reviewed revisions and two nonduplicating training manifests.
- Reviewed-label ingestion with verification gating.
- Two-head motion targets: chewing probability and chew phase.
- Registry-backed promotion checks and versioned Live deployment.
- Local data migration with a SHA-256 manifest at
  `workspace/manifests/reorg-2026-06-15.json`.

## Current Verification

- Vision tests: 43 passed.
- Motion tests: 35 passed.
- Both CLIs load and show their renamed command surfaces.
- Default and external `CHEWSENSE_WORKSPACE` resolution works in both
  pipelines.
- ChewSenseReview builds successfully with code signing disabled.
- Xcode lists `ChewSenseCollect`, `ChewSenseReview`, and `ChewSenseLive`.

Collect compilation is currently blocked by unavailable Icon
Composer/CoreSimulator services in the sandbox. Live compilation is blocked
by the sandbox denying Xcode's nested `sandbox-exec` for CoreML code
generation. These builds previously passed before reorganization and must be
rerun outside this restricted environment.

## Source-Control State

Collect and Live working copies were checkpointed on
`codex/pre-reorg-2026-06-15`, and their histories were fetched into the parent
as `import/collect` and `import/live`. The filesystem no longer contains
nested `.git` directories.

The parent repository still needs the final history-preserving merge commits
and staging of the role-based layout. The protected `.git` approval quota
blocked that bookkeeping during this run; no source or history was deleted.

## Model Readiness

No replacement personalized model is promotion-ready yet. Motion-only
food-entry detection remains experimental. The deterministic MediaPipe/FSM
vision path remains the fallback.
