# ChewSense Roadmap

The implementation path is no longer organized as competing numbered app
iterations. The active products and commands are documented in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/PIPELINE_RUNBOOK.md`](docs/PIPELINE_RUNBOOK.md).

## Milestone 1: Repository Checkpoint

- Commit or export the dirty Collect and Live nested worktrees.
- Import their history into the parent repository.
- Replace the broken gitlinks with ordinary monorepo directories.
- Move superseded implementations under `ProjectHistory/` only after parity
  fixtures and unique annotations are preserved.

## Milestone 2: Device Validation And Data

- Validate shared-clock timing, interruption recovery, dropped motion rows,
  and missing video frames on physical hardware.
- Finalize at least 20 verified eating sessions.
- Finalize at least 20 varied hard-negative sessions.
- Keep the newest chronological sessions permanently locked.

## Milestone 3: Personalized CV Replacement

- Train the sequence model from verified annotation revisions.
- Retain deterministic MediaPipe/FSM inference as fallback.
- Require mouth-state macro F1 of at least `0.85`.
- Require chew F1 at 250 ms of at least `0.80`.

## Milestone 4: Personalized Motion Promotion

- Train chewing probability and chew phase from finalized motion labels.
- Derive individual chews from phase cycles.
- Require chew-count error at most `10%`, eating recall at least `0.90`, and
  hard-negative false-positive rate at most `0.15`.
- Deploy only a versioned checkpoint with a passing locked promotion report.

Motion-only food-entry detection remains a separate experiment and is not a
promotion requirement.
