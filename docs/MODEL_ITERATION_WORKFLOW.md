# ChewSense Model Iteration Workflow

This is the repeatable loop that turns app field-tests into a better deployed
model and back into the app. Once everything is set up, **one full iteration
is three commands** in `02-DataPipeline/`:

```bash
python main_new.py from-field-test     # ingest + retrain
python main_new.py compare             # report vs previous model
python main_new.py deploy --force      # ship to Xcode
```

Then in Xcode: **Product → Clean Build Folder (⇧⌘K)** and run the app.

---

## 0. One-time environment setup

The `coremltools` export step on macOS arm64 currently segfaults with
`torch>=2.8`. Use the pinned export venv from
[02-DataPipeline/requirements-export.txt](../02-DataPipeline/requirements-export.txt):

```bash
cd 02-DataPipeline
python3 -m venv .venv-export
source .venv-export/bin/activate
pip install -r requirements-export.txt
```

The same venv handles training **and** export, so you don't need a second one.
Activate it before every command in this doc.

---

## 1. Record + correct in the app (stage 04)

1. Open `04-RealWorldTesting/ChewSense-RealTime/ChewSenseDebuggingModel.xcodeproj`.
2. Build and run on iPhone or Mac with AirPods connected.
3. **Pick an activity preset** (Eating soft / Eating hard / Drinking water /
   Baseline / Walking / Head shake / Nodding / Talking / Singing / Custom)
   before recording. The preset is required — the record button stays
   disabled until a Custom description is filled in. The chosen preset
   stamps the bundle with `activity`, `activity_label`, and
   `expected_label_class`. See
   [REAL_WORLD_TEST_PROTOCOL.md](REAL_WORLD_TEST_PROTOCOL.md#activity-presets)
   for the full table.
4. Optionally toggle **"Mark as benchmark candidate"**. Two presets default
   it ON (`head_shake`, `baseline`); the toggle only writes a flag —
   promotion to `gold_locked` remains an explicit operator step (see
   step 2 below).
5. Hit **Start** to begin a recording. The app saves a session bundle that
   contains `motion.csv`, `predictions.csv`, `<session_id>.mov`, and
   `session_metadata.json`. **Detection params (alpha, thresholds, window
   counts) come from the deployed model — they are not adjustable in the
   UI during a recording.**
6. Open the recording in the review pane. Drag review segments to mark
   missed chews, false positives, or "the prediction was correct here." On
   finalize, the app writes **`labeled_motion.csv`** — every motion sample
   in the session with a per-row `label` resolved from the model's
   predictions plus your corrections.
7. Open the **Recording Configuration** inspector and confirm the
   **Bundled Model** section shows the `MODEL_ID` and short SHA you expect.
   That's how you'll know when a new model has actually landed in step 6.
8. Export the bundle (Files / Finder share sheet) and drop the folder into:

   ```
   02-DataPipeline/data/user/field_tests/
   ```

> **All sessions count as training data.** `labeled_motion.csv` is written
> for every reviewed session, whether you changed labels or not. The
> pipeline always prefers `labeled_motion.csv` over the legacy
> `motion.csv + feedback.csv` pair, so a "correct" review still contributes
> a fully-labeled session to the next training run.

---

## 1.5 Visual auto-labeler — lab tool only

Stage 04 ships a single review surface: chew/not-chew windows **and**
chew-stroke ticks live on the same timeline. To make stroke marking
tractable on real meals, the pipeline auto-seeds strokes from the
session video using a small CNN+GRU mouth-shape model
(`02-DataPipeline/pipeline/visual_labeler.py`).

**This model is a dataset tool, not a shipped feature.** It only runs in
the lab, only reads the on-device camera capture during review, and is
never exported to CoreML. The shipped iOS/macOS app continues to use the
motion-only ChewNet checkpoint.

Three guarantees enforce the boundary:

- The model is saved as `models/visual_labeler.pth`. Both
  [pipeline/deploy.py](../02-DataPipeline/pipeline/deploy.py) and
  [pipeline/registry.py](../02-DataPipeline/pipeline/registry.py) raise
  `RuntimeError` if anything with that filename is fed in.
- A regression gate in
  [03-ModelEvaluation/labeler_gate.py](../03-ModelEvaluation/labeler_gate.py)
  refuses to accept a new labeler that doesn't beat the mouth-openness
  heuristic by ≥10 pp F1 on a held-out set.
- MediaPipe drop-outs are written to `face_presence.json`. The reviewer
  can't mark strokes inside face-missing windows, and downstream
  metrics + training drop those windows.

Typical lab workflow:

```bash
# Train the labeler on bundles that already have human strokes.
python -m pipeline.visual_labeler train \
    --bundles data/curated/accepted_sessions/* \
    --out models/visual_labeler.pth

# Then on every new bundle, the pipeline auto-seeds strokes:
python -m pipeline.stroke_seeds <bundle_dir>          # uses the model if present
```

The closed loop for stroke quality is therefore
**04-RealWorldTesting → 02-DataPipeline (visual_labeler train) → 03-ModelEvaluation (labeler_gate) → 04-RealWorldTesting**,
which is intentionally orthogonal to the ChewNet train/deploy loop
described below.

---

## 2. Ingest + retrain (stage 02)

```bash
cd 02-DataPipeline
source .venv-export/bin/activate
python main_new.py from-field-test --input data/user/field_tests
```

What this does:

1. Reads every bundle under `data/user/field_tests/` (skipping anything
   already inside `_archive/`).
2. Writes labeled session CSVs into
   `data/curated/accepted_sessions/Eating-field-reviewed-…/`. **Curated
   sessions accumulate forever** — every iteration adds new ones, and
   training in step 3 uses the entire curated directory.
3. **Archives the source bundle** to
   `data/user/field_tests/_archive/<timestamp>/<bundle>/`. This guarantees
   the next iteration only ingests *new* bundles, not duplicates of past
   ones, while still keeping a copy of the raw data on disk.
4. Validates curated sessions, transforms them, extracts features, trains a
   new `models/chewnet.pth`, and writes a registry entry under
   `model_registry/runs/<model_id>/`.

Useful flags:

- `--no-archive` — keep bundles in `field_tests/` after ingest (will be
  re-imported next iteration; usually not what you want).
- `--strip-archive-mov` — delete the bulky `.mov`/`.mp4` files inside the
  archived bundle. Labels live in CSV/JSON, so this is lossless for
  training but reclaims most of the disk.
- `--gold-locked <session_id>` (repeatable) — promote a session into the
  permanent locked validation set.

### Two-head training (chew-phase mode)

When sessions have been auto-labeled by Stage 02b and reviewed in
`ChewSense-CycleReviewer`, the pipeline can also learn a chew-phase
regression head alongside the eating head. Two config knobs in
`02-DataPipeline/config/defaults.yaml` (or your project config) enable it:

```yaml
features:
  extended_chew_phase: true     # emit 3-col targets [eating, sin, cos]

chew_phase_head:
  enabled: true                 # dispatch TwoHeadChewNet in train.py
  eating_weight: 1.0            # α — BCE term on eating logit
  phase_weight: 0.5             # β — MSE term on (sin, cos)
  mask_when_not_eating: true    # skip phase loss on idle rows
```

With both flags on, `train.py` builds `TwoHeadChewNet` (shared trunk →
1-D eating head + 2-D tanh phase head). Checkpoint records
`mode="two_head"` and `output_names=["eating_logit","chew_phase_sin",
"chew_phase_cos"]`; `export.py` emits a CoreML model with a single
`output` tensor of shape `(1, 3)`. The real-time app's
`ChewPhaseCounter` consumes the sin column to count chew cycles by
zero-crossing.

### Promoting benchmark candidates

If any imported session has `gold_lock_candidate: true` (set via the
"Mark as benchmark candidate" toggle in the recording app), the import
prints a block like:

```text
🔒 Gold-lock candidates flagged in app:
     - Eating-field-reviewed-FieldTest-20260502-064508
     - Not-eating-field-reviewed-FieldTest-20260502-065535

   To promote them into the locked validation set, rerun with:
     python main_new.py from-field-test --gold-locked Eating-field-reviewed-FieldTest-20260502-064508 \
       --gold-locked Not-eating-field-reviewed-FieldTest-20260502-065535
```

The flag is **never auto-applied**. `pipeline/splits.py` documents this as
the `gold_locked_assignment: explicit_only` invariant — you have to
choose to lock these sessions.

---

## 3. Compare against the previous model (stage 03)

```bash
python main_new.py compare
```

This calls [03-ModelEvaluation/compare.py](../03-ModelEvaluation/compare.py),
which loads the **two newest registry entries**, diffs their
`metrics.json` per split (`validation_locked`, `field_regression`,
`all`), and writes a markdown report:

```
03-ModelEvaluation/reports/<timestamp>_compare.md
```

The report ends with a verdict — `improved`, `neutral`, or `regressed`
(neutral band ±0.005 on accuracy/AUC/precision/recall/F1). The deploy
command in step 4 reads the same verdict and refuses to ship a regressed
model unless you pass `--force`.

To pin specific models for the comparison:

```bash
python main_new.py compare --baseline chewnet-2026… --candidate chewnet-2026…
```

---

## 4. Deploy to the app (stage 02 → 04)

```bash
python main_new.py deploy --force
```

What this does:

1. Runs the comparator gate (skip with `--skip-compare`).
2. Prints the resolved versions of `python`/`torch`/`coremltools`/`numpy`
   so you can spot a bad env immediately.
3. Runs the CoreML conversion in an **isolated subprocess** so a native
   crash inside `coremltools` no longer takes down the whole CLI; instead
   you get a clear error and remediation hint.
4. Writes:
   - `02-DataPipeline/exports/ChewNet.mlpackage`
   - `04-RealWorldTesting/ChewSense-RealTime/ChewNet.mlpackage`
   - `04-RealWorldTesting/ChewSense-RealTime/ChewSenseDebuggingModel/Generated/NormalizationConstants.swift`
   - `02-DataPipeline/exports/chewnet_norm.json`
5. Prints a **before → after diff table** for the seven identity fields
   (`MODEL_ID`, `DATASET_HASH`, `DATASET_STATUS`, `MODEL_PACKAGE_SHA256`,
   `WANDB_RUN_ID`, `GENERATED_AT`).
6. **Exits with code 3** if `MODEL_PACKAGE_SHA256` is unchanged — that's
   the loud signal that the deploy was a no-op (typically because the
   checkpoint wasn't retrained).

Useful flags:

- `--force` — bypass the regression gate and overwrite existing artifacts.
- `--skip-coreml` — reuse the existing `exports/ChewNet.mlpackage` (debug
  the Swift constants without paying for the conversion).
- `--skip-compare` — skip the regression gate.

---

## 5. Rebuild the app (stage 04)

In Xcode:

1. **Product → Clean Build Folder (⇧⌘K)** — required because the
   `.mlpackage` compilation is cached aggressively.
2. Build and run.
3. Open **Recording Configuration → Bundled Model** and verify:
   - `MODEL_ID` matches the value the deploy step printed.
   - The short SHA matches the new `MODEL_PACKAGE_SHA256`.
   - `GENERATED_AT` is the most recent run.

If those values still show the old model, you forgot the clean build.

---

## 6. Loop again

Record more sessions in the app, drop the new bundles into
`data/user/field_tests/`, and re-run steps 2–5. Each iteration:

- Adds new labeled sessions to `data/curated/accepted_sessions/` (training
  data **strictly grows**; nothing the user has ever recorded is dropped).
- Archives the imported bundles so they aren't re-ingested.
- Produces a new comparison report and a new registry entry.
- Replaces the deployed `.mlpackage` and `NormalizationConstants.swift`.

---

## Disk hygiene: `clean` subcommand

The loop produces four kinds of accumulating artifact. Run this whenever
you notice disk pressure (everything except curated training data is
disposable):

```bash
python main_new.py clean --dry-run        # preview
python main_new.py clean                  # actually prune
python main_new.py clean --strip-archive-mov   # also drop .mov/.mp4 from archived bundles
```

Defaults keep the most recent:

| Path | Default keep | What's pruned |
|---|---:|---|
| `data/derived/runs/` | 3 | per-iteration features + transformed CSVs |
| `model_registry/runs/` | 5 | per-train registry entries (full checkpoint copies) |
| `wandb/` | 3 | local W&B run dirs |
| `data/user/field_tests/_archive/` | 5 | timestamped archives of already-imported bundles |

Override with `--keep-runs N`, `--keep-registry N`, `--keep-wandb N`,
`--keep-archive N`. **Never pruned**: `data/curated/`, split manifests,
`models/chewnet.pth`, the active export under
`02-DataPipeline/exports/`, the deployed Xcode artifacts, and any
non-archived field-test bundles.

---

## Invariants for the loop

- **Raw data is preserved.** Bundles are archived, not deleted. The
  curated CSVs that feed training are kept indefinitely.
- **Validation/test splits are stable.** Locked validation/test sessions
  in `data/manifests/splits/locked_splits.json` carry across retraining.
- **Every deployed model is traceable.** `MODEL_ID`, `DATASET_HASH`, the
  W&B run id, and the package SHA-256 are all visible in the app.
- **A no-op deploy is loud.** Unchanged SHA returns exit code 3 and prints
  a warning, so a forgotten retrain can't masquerade as a successful
  deploy.
