# Roadmap

## Milestone 1: Repository Integration

- Complete imported-history merge commits.
- Pass renamed app builds and both Python suites.
- Add one anonymized end-to-end fixture.
- Eliminate active numbered-path references.

## Milestone 2: Reliable Personalized Dataset

- Verify physical-device synchronization and interrupted capture handling.
- Collect at least 20 verified eating sessions.
- Collect at least 20 varied verified hard-negative sessions.
- Lock chronological session-level validation and test sets.

## Milestone 3: Model Promotion

Initial targets:

- mouth-state macro F1 at least `0.85`;
- chew F1 at 250 ms at least `0.80`;
- chew-count error at most `10%`;
- eating recall at least `0.90`;
- hard-negative false-positive rate at most `0.15`.

Retrain CV in reviewed batches. Train motion with a shared encoder and primary
outputs for eating probability and chew phase. Derive motion chew events from
phase cycles.

## Milestone 4: Live Validation

- Promote only versioned models that pass locked evaluation.
- Record active model IDs in every Live session.
- Compare Live field performance against finalized Review labels.
- Keep motion-only food-entry detection experimental until it passes a
  separate validation gate.

## Later

- Subject-aware generalization beyond the first wearer.
- Multi-wearer calibration and privacy-preserving dataset tooling.
- Validated meal-level feedback built on reviewed chew and bite events.
