# Changes to the locked evaluation split

`workspace/experiments/eating/splits.json` is immutable: assignments are only
ever added. When a recording stops being usable, it is **retired** instead.
Its split entry stays as it was; every later experiment lists it as rejected,
with the reason. Past runs, reports, `index.json` and the count of locked-test
consultations are never edited.

How retirement works:
- **Record.** It is one write-once file per recording,
  `workspace/loop/state/retired/<session_id>.json`. It holds the reason, a
  research category, the evidence, the role and split at retirement, and the
  SHA-256 of every preserved file.
- **Commands.**
  `chewsense-vision loop retire-recording <id> --category … --reason … [--evidence …]`
  retires a recording; `chewsense-vision loop retired` lists retired recordings
  and re-checks that their files are intact.
- **Effect.**
  - Removed from: the blind and assisted review queues, loop evaluation and
    training, experiment progress counts, the AirPods eating experiment
    (development and locked test) and the shared status in Collect, Review and Live.
  - Review shows it under "Kept for research", watch-only, and never saves
    anything into it.
- **Nothing is deleted or modified.** Sources, archives, working copies,
  drafts, Vision outputs, reports and the loop's session record keep their bytes.
- **From Review:** ⋯ → *Don't use this recording…* runs the same command
  with a required reason (category `reviewer_excluded` unless another fits).
  `chewsense-vision loop retired` is the complete list. Entries below explain
  retirements that changed the locked test set.
- **Reversal (manual, deliberate).** Move the retirement file out of
  `state/retired/` and restart the loop service. Record that here too.

## 2026-11-09 — `Not-eating-20261026-174428-C27C9380` retired

| | |
| --- | --- |
| Category | Mimed chewing / chewing-like movement without food (`mimed_chewing_no_food`) |
| Requested by | The wearer, after an agent examination of the video and motion |
| Role and split at retirement | Loop `evaluation`; eating split `test`, stratum `negative_only`, reason `loop_evaluation_role` (assigned 2026-11-07) |
| Review state at retirement | Blind draft empty and unfinished (no chews, not verified); no assisted review; no frozen canonical events |
| Files preserved | 63 inventoried in the retirement record; `loop retired` reports them intact |

**Why it was retired.** It was recorded with Collect 1.1 as "not eating", and
the phone label is "not chewing" for all 25.3 s. But 14.1–19.7 s shows
deliberate chewing motion with the lips closed and no food. Neither answer in
the blind review is valid ground truth:
- **Marking the cycles as chews** puts chews in a recording filed as "no chewing".
- **Leaving them unmarked** scores the video detector as wrong on motion that
  the camera and the AirPods cannot tell apart from real chewing.

**Evidence** (stored verbatim in the retirement record):
- **Video, 14.1–19.7 s:** 12 closed-lip jaw cycles, one every ~0.45 s
  (≈2.2 Hz), lips pressed together (Vision `mouth_cycles`, kind `closed_lip`,
  confidence 0.98).
- **AirPods accelerometer, same span:** dominant frequency 2.14 Hz, matching
  the jaw rhythm.
- **No food:** no hand near the mouth at any time (`n_hands` 0), and no food visible.
- **Rest of the recording:**

  | Time | Content |
  | --- | --- |
  | 0–5.8 s and 10.8–14.1 s | Head movement |
  | 5.8–10.8 s | One wide mouth opening, then a held grin |
  | 19.7–25.3 s | Still |

- **Frames:** mouth crops are kept outside Git, in
  `workspace/experiments/retired/Not-eating-20261026-174428-C27C9380/`.

**What did not change:**
- `splits.json`, `index.json` and every earlier run and report.
- The locked-test consultation count (still 3).
- The evaluation criteria and acceptance targets.
- Published Vision metrics: the recording had no frozen blind review, so it
  was never part of the gold chews (45).

Later eating-experiment runs report 31 usable recordings, with this one under
"rejected". No replacement was assigned to the test split. New recordings get
roles by the existing rule, and the loop's evaluation quota now counts only
active (unretired) recordings.

**Research value.** The AirPods register a chewing-like 2 Hz rhythm during
mimed chewing. If false alarms from chewing-like movement (mimed chewing, gum)
matter, collect them as their own explicitly labelled experiment rather than
under "not eating".
