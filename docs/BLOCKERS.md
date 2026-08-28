# Blocking Work

The source refactor can be verified locally and in CI. Model promotion is
blocked by evidence, not by missing scaffolding.

| Priority | Remaining requirement | Evidence needed |
| --- | --- | --- |
| P0 | Recover and freshly review usable legacy video/motion pairs | Schema-v2 immutable revisions; legacy event labels remain masked |
| P0 | Build the personalized dataset | ≥20 verified eating and ≥20 varied hard-negative sessions |
| P0 | Validate capture on physical AirPods | Recorded delayed-motion, gap, drift, interruption, and optional-audio checks |
| P0 | Train/evaluate a temporal checkpoint | All initial chew promotion gates pass on locked chronological test sessions |
| P1 | Establish personal coaching baselines | Three verified calibration meals per supported texture |
| P1 | Validate food entry separately | Every food-entry promotion gate passes before bite UI is enabled |
| P1 | Measure reviewer agreement | Blind second review of ≥10% of sessions and anchor-disagreement report |

Local candidate recovery also needs the pinned MediaPipe assets. They were not
available in this checkout, and the restricted environment denied the network
download, so the recovered real pairs remain deliberately unreviewed.

Do not enable bite count, chews-per-bite, “not enough chews,” or promote a
replacement Live model while the corresponding P0/P1 evidence is absent.
