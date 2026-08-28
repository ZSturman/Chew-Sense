# Roadmap

## Implemented Foundation

- Role-based monorepo with imported Collect and Live histories retained.
- Schema-v2 closed–open–closed anchors and immutable hash lineage.
- Exact candidate-bound native review with uncertainty dispositions.
- Read-only audit and explicit finalization workflow.
- Locked Python 3.12 environment and one verification entrypoint.
- Causal temporal chew model contract, event decoder, replay metrics, and
  capability-gated Live path.
- Local checksum/deduplication inventory without participant media in Git.

## Dataset Recovery And Collection

- Re-homed two exact legacy video/motion pairs as unverified migration inputs;
  26 of 27 raw bundles now pass structural audit, with one zero-row bundle
  quarantined.
- Re-run vision and review usable video from scratch.
- Capture at least 20 verified eating and 20 varied hard-negative sessions for
  the first wearer, with texture and confound metadata.
- Lock chronological session-level train/validation/test splits containing
  positives and negatives in every partition.
- Blindly re-review at least 10% and track anchor disagreement; revise the
  instructions if median disagreement exceeds two video frames.

## Initial Chew Promotion Gate

- eating recall ≥ `0.90`;
- hard-negative window FPR ≤ `0.15`;
- chew F1 at ±250 ms ≥ `0.80`;
- chew-count error ≤ `10%`;
- median inter-chew-interval MAE ≤ `100 ms`;
- Python/CoreML/Swift probability error ≤ `1e-3`;
- decoded timestamps within one 100 ms inference step;
- recorded physical-AirPods promotion smoke check.

No model currently has sufficient verified v2 data to pass this gate.

## Food-Entry Promotion Gate

Food-entry coaching remains hidden until an independently locked report shows:

- a longer-context `food_entry_logit` head is implemented in Python, CoreML,
  and Swift with exact replay parity;

- event F1 at ±500 ms ≥ `0.80`;
- count error ≤ `15%`;
- false food entries ≤ `0.2/min` on negative sessions;
- per-bite chew-count MAE ≤ `2`.

Only then may Live close a prior bite on a new verified food entry and expose
optional chew-count-goal feedback.

## Later

- Multi-wearer validation and subject-aware generalization.
- Privacy-preserving dataset exchange.
- Broader wellness studies; no medical claims without appropriate validation.
