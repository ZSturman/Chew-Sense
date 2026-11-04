# Blocking Work

The source refactor can be verified locally and in CI. Model promotion is
blocked by evidence, not by missing scaffolding.

| Priority | Remaining requirement | Evidence needed |
| --- | --- | --- |
| P0 | Coarse chewing detection beyond one wearer and today's 3 test recordings with chewing | More verified-clock guided takes (≥300 chewing windows), confounds, natural meals, then a second wearer; see [audit](AUDIT_2026-11-03.md) |
| P0 | An on-device detector that generalizes | Swift/CoreML port of the feature model with parity, or a TCN that passes the locked test after current-setup training data |
| P0 | Record and independently review a new physical paired session | Measured markers, blind gold and assisted review; legacy pairs stay diagnostic |
| P0 | Build the personalized dataset | ≥20 verified eating and ≥20 varied hard-negative sessions |
| P0 | Validate capture on physical AirPods | Recorded delayed-motion, gap, drift, interruption, and optional-audio checks |
| P0 | Train/evaluate a temporal checkpoint | All initial chew promotion gates pass on locked chronological test sessions |
| P1 | Establish personal coaching baselines | Three verified calibration meals per supported texture |
| P1 | Validate food entry separately | Every food-entry promotion gate passes before bite UI is enabled |
| P1 | Measure reviewer agreement | Blind second review of ≥10% of sessions and anchor-disagreement report |

Phase 0 downloaded and checksum-verified the pinned MediaPipe assets and
processed all four paired legacy videos on isolated copies. Those predictions
establish software execution only. Follow [PHASE0.md](PHASE0.md) for the new
recording and independent evaluation; no training or promotion is part of this
phase. Current device handoff: [NEXT_STEPS.md](NEXT_STEPS.md).

Do not enable bite count, chews-per-bite, “not enough chews,” or promote a
replacement Live model while the corresponding P0/P1 evidence is absent.
