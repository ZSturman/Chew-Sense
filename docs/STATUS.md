# Current Status

## November 3, 2026 — first real training and evaluation

See the [independent audit](AUDIT_2026-11-03.md). A motion-only chewing-bout
detector now trains on the existing human coarse labels:

- 32 recordings, one wearer;
- persistent session-level splits;
- grouped cross-validation and a locked test set;
- an HTML error-analysis report.

Gradient boosting on interpretable IMU features reached development F1
0.87 [0.74–0.92] and locked-test F1 0.90 [0.51–0.98], with 7/7 test chewing
bouts found and 2 false bouts. That is pilot evidence for one person, not a
validated product. The deployable TCN does not yet generalize to 2026
recordings (test F1 0.50). Nothing was promoted or deployed. Individual chew
counting, bites and cadence remain unvalidated.

---

Phase 0 update: October 26, 2026. See [the protocol and current evidence](PHASE0.md)
and [device handoff](NEXT_STEPS.md). The implementation now processes four paired
legacy videos and supports independent blind review; physical synchronization,
new human gold and actual annotation time savings remain pending.

The remaining status text records the July 13, 2026 baseline.

## Bottom Line

The trustworthy chew-cycle software foundation is implemented. ChewSense now
has one schema-v2 annotation lineage, an exact-hash review/finalization path, a
causal temporal IMU model contract, and cadence-only Live coaching with bite
features hard-disabled. It is not yet a validated chew coach: there is no freshly
reviewed real schema-v2 dataset and no temporal checkpoint has passed the
promotion gates.

## What Is Implemented

- Three role-based apps with imported Collect and Live histories retained.
- Shared Collect/Live schema-v2 capture serialization for clocks, media
  hashes, gaps, interruptions, and optional local review audio.
- Canonical Closed start / Maximum open / Closed end semantics, with
  `chew_event_ts == end_closed_ts` and adjacent shared closed boundaries.
- One immutable, hashed candidate revision per vision run; coarse all-negative
  and missing-review states are explicit.
- Candidate-bound Mac review with accept/edit/reject/add, uncertain or
  unobservable dispositions, synchronized AirPods evidence, stale-hash
  blocking, and a final full-video pass.
- Read-only session audit and explicit immutable finalization/materialization.
- A three-second, 50 Hz causal TCN contract with eating, chew-event, phase, and
  confidence outputs; event-head decoding uses eating gating, local-peak
  suppression, and a 250 ms refractory period.
- Chronological session-level splits, exact registry evaluation/promotion
  checks, CoreML export/parity
  support, and a privacy-safe end-to-end golden fixture.
- Live texture selection, verified personal cadence baselines, sustained
  too-fast cues, raw prediction telemetry, confidence coverage, and post-meal
  cadence summaries. The food-entry head is not implemented and all related
  coaching remains hidden.
- One locked Python 3.12 environment and `./tools/verify fast|fixture|apps|all`.

Obsolete aggregate chew-phase, mouth-shape, bout-as-bite, duplicate label,
standalone export/evaluation, and placeholder dataset/model paths have been
removed from the active checkout.

## Verification Evidence

- `./tools/verify fast`: 80 passed, 1 skipped.
- `./tools/verify fixture`: 25 passed, 1 skipped.
- The skip is CoreML prediction execution: conversion succeeds, but the local
  sandbox denies CoreML's temporary working directory. The `1e-3` parity gate
  remains enforced when that runtime is available.
- The current 12-test Review contract bundle compiles; the earlier seven-test
  bundle executed with all seven passing before the local test runner became
  sandbox-blocked.
- The Collect app and its 10-test bundle compile with code signing disabled.
- The Live app reached CoreML code generation, where Xcode's nested
  `sandbox-exec` was denied before Swift compilation. It still needs a macOS CI
  or unrestricted local build/test run.
- Project files parse successfully. Physical AirPods capture and promotion
  checks remain intentionally local and recorded, not synthetic CI claims.

## Local Data Recovery

The latest ignored checksum inventory covers 2,051 files and 5,045,338,633
bytes across `workspace/` and `archive/`. It records 250 duplicate-content
groups, 1,144,852,669 duplicate bytes, and 3,900,485,964 unique bytes; no file
was deleted.

Of 27 raw session directories, 26 now have explicit unverified schema-v2
manifests and pass read-only structural audit. The zero-row corrupt
`Not-eating-20251212-181452` bundle is quarantined from migration. Two exact
video/motion pairs (`video-20250919-000138` and `video-20251021-225301`) were
re-homed under matching canonical IDs; their source media was preserved and
they remain unverified.

Vision could not be rerun on those pairs in this environment because the
required MediaPipe model files are absent and dependency/model download
network access was denied. No candidate or truth artifact was fabricated.

## What Remains

1. Make the pinned MediaPipe assets available, rerun vision on the recovered
   pairs, and review every candidate from scratch.
2. Capture and verify at least 20 eating and 20 varied hard-negative sessions
   for the first wearer, including texture and confound metadata.
3. Blindly re-review at least 10%, measure anchor disagreement, and freeze
   chronological positive/negative train, validation, and test splits.
4. Train the temporal checkpoint and pass every chew promotion gate, including
   Python/CoreML/Swift parity and a recorded physical-AirPods smoke test.
5. Complete three verified calibration meals per supported texture before
   treating cadence goals as personalized.
6. Keep food-entry, bite count, chews-per-bite, and “not enough chews” hidden
   until the separate food-entry head passes its independent gates.
