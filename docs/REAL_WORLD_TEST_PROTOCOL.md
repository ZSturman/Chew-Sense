# Real-World Test Protocol

Use this protocol when checking whether a deployed model works outside the training pipeline.

> **Detection parameters belong to the model, not the UI.** Smoothing alpha,
> thresholds, and start/end window counts are baked into the deployed CoreML
> bundle (`Generated/NormalizationConstants.swift::RuntimeConfig`). The app
> reads them at session start and never lets the user override them while
> recording — if the detector behaves badly, fix it by retraining and
> redeploying, not by twiddling sliders during a session.

## Setup

1. Pair supported AirPods with motion sensors.
2. Open the canonical testing app: `ChewSenseDebuggingModel`.
3. Confirm the app displays the deployed model metadata (model id, dataset
   hash, package SHA256). The runtime detector params are also shown
   read-only — they're for diagnostics, not for tuning.
4. Pick an **activity preset** (see table below). The preset stamps the
   session bundle with `activity`, `activity_label`, and
   `expected_label_class` so the pipeline can keep dataset coverage
   balanced and reject regressions per activity.
5. Optionally toggle **"Mark as benchmark candidate"** if the recording
   should be considered for the locked validation/gold split (see
   *Benchmark / gold-locked sessions* below).
6. Enter an optional short scenario note (e.g. `kitchen, evening, gum`).
7. Tap `Start`.

## Activity Presets

Pick the preset that best matches what you'll actually do during the
recording. Each preset declares an `expected_label_class` so the pipeline
can flag unexpected predictions and keep negatives well-represented.

| Preset | Slug | Expected class | Use for |
| --- | --- | --- | --- |
| Eating soft food | `eating_soft` | positive | Recall on subtle chewing (banana, yogurt, soft bread). |
| Eating hard food | `eating_hard` | positive | Recall on strong chewing (apple, carrot, granola). |
| Drinking water | `drinking_water` | negative | Sensitivity around mouth movement without chewing. |
| Not eating / baseline | `baseline` | negative | False-positive baseline at rest. |
| Walking | `walking` | negative | Motion robustness while ambulating. |
| Shaking head | `head_shake` | negative | Hard negative — periodic motion that resembles chewing. |
| Nodding | `nodding` | negative | Hard negative — vertical periodic motion. |
| Talking | `talking` | negative | Common false-positive source. |
| Singing | `singing` | negative | Stronger jaw motion than talking. |
| Custom | `custom` | unknown | Anything not covered above. Adds free-form description. |

> Per-block recordings should be **short and focused** (1-3 min each).
> One bundle = one preset. Don't mix activities inside a single recording.

## Benchmark / gold-locked sessions

Some sessions become permanent regression benchmarks — the pipeline's
"this must never get worse" set. The contract is:

- The app exposes a **"Mark as benchmark candidate"** toggle. Two presets
  default this toggle ON (`head_shake`, `baseline`) because they're
  high-value hard-negative anchors; you can toggle it for any session.
- The toggle is **only a flag** — it sets `gold_lock_candidate: true` in
  `session_metadata.json`. It does NOT auto-promote the session into the
  locked validation set.
- After import, `python main_new.py from-field-test ...` prints the exact
  `--gold-locked <session_id>` invocation needed to actually lock the
  flagged sessions. Promotion remains an explicit operator decision per
  `pipeline/splits.py::gold_locked_assignment: explicit_only`.

## Marking Feedback

Use the app buttons as the model runs:

| Button | Use when | Corrected label |
| --- | --- | --- |
| `False Positive` | App says chewing but you are not chewing. | Not chewing |
| `Missed Chew` | You are chewing but app stays idle or too low. | Chewing |
| `Correct` | The app is right in the current moment. | Current predicted state |

Add a note when the context matters, for example `talking`, `walking`, `gum`, `left AirPod loose`, or `small bite`.

Each feedback entry is tied to the current prediction window and the nearby motion samples, so it can become training data.

## Export

At the end of a block:

1. Tap `Stop`.
2. Tap `Share Session Bundle`.
3. Save or AirDrop the exported folder.
4. Place the folder under:

```text
02-DataPipeline/data/user/field_tests/
```

Then import:

```bash
cd 02-DataPipeline
python main_new.py from-field-test --input data/user/field_tests
```

## What Good Logs Contain

A useful field-test bundle has:
- enough `motion.csv` rows around each feedback event,
- `predictions.csv` rows with probability and smoothed state,
- `feedback.csv` entries with event type and corrected state,
- `session_metadata.json` with model id, dataset hash, **runtime config
  (read-only, sourced from the bundled model)**, platform, scenario,
  **`activity`/`activity_label`/`expected_label_class`**, and
  **`gold_lock_candidate`**,
- a non-empty `<session_id>.mov` video for review playback. The app's
  status text shows "Saved video: X.X MB" on a successful stop; if it
  shows "Recording failed: …" instead, the bundle is incomplete and
  should be re-recorded.

