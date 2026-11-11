# Runbook

Run commands from the repository root.

## Environment And Verification

Requirements: Python 3.12, uv, and FFmpeg's `ffmpeg` and `ffprobe` commands.
On macOS with Homebrew, install the video tools with `brew install ffmpeg`.
The Python CI job installs them explicitly; they are not supplied by `uv sync`.
Apple app checks also require Xcode and an available iPhone simulator.

```bash
uv sync --locked
./tools/verify fast       # repository boundary + all Python tests
./tools/verify fixture    # canonical session + temporal replay golden tests
./tools/verify apps       # Review tests and all app/test builds
./tools/verify all
```

The verifier rejects a non-3.12 interpreter, nested gitlinks, and tracked cold
archive content. CI runs `fast`, `fixture`, and `apps` from `uv.lock`.

## Workspace

The default is `<repo>/workspace`. To use another location:

```bash
export CHEWSENSE_WORKSPACE="/absolute/path/to/workspace"
```

## Collect

Open `apps/collect-ios/ChewSenseCollect.xcodeproj`. Choose Eating or Not
Eating, optionally enable Local Review Audio before entering capture, and
record with AirPods motion available. The resulting schema-v2 session stores
the video-clock origin, first-motion latency, sample gaps, interruptions, and
optional deletable `.m4a` review audio.

Do not manually remove a referenced review-audio file: run audit after any
manifest-safe purge so media hashes and presence flags remain truthful.

Coarse label markers use video-relative time. If the origin is absent, the app
refuses to relabel rather than substituting the first motion row.

## Chewing-Bout Detector (coarse labels)

```bash
chewsense-motion eating-experiment                 # CV + locked test + HTML report
chewsense-motion eating-experiment --dev-only      # exploration; locked test untouched
chewsense-motion eating-report                     # rebuild the latest report
chewsense-motion eating-validity                   # what the scores can be trusted to mean; development only
chewsense-motion eating-prospective                # frozen Live detector on later recordings, scored once each
chewsense-motion video-teacher-eval --vision-root <folder of chewsense-vision run outputs>
```

The detector reads sessions, loop working copies and field tests without
modifying them. It writes to `workspace/experiments/eating/`. `splits.json`
there is permanent; delete it only to start a new, separately reported
evaluation. Labels are human coarse chewing spans with provenance tiers
(see `pipelines/motion/pipeline/eating/dataset.py`). Runs are experimental
and are never registered or deployed.

Development folds keep recordings of one sitting together (recordings that
start within 45 minutes of the previous one's end), and intervals resample
sittings. `--cv-grouping session` gives the per-recording folds of runs before
2026-11-10. `--window-grid sample` ends each window on a measured sample; it
is a comparison only until Live implements the same grid, and such runs
cannot be exported. Background: [VALIDITY_2026-11-10](VALIDITY_2026-11-10.md).

## Vision And Review

```bash
python pipelines/vision/run_on_sessions.py --only <session_id>
chewsense-vision audit-session \
  workspace/sessions/raw/<session_id> \
  --derived workspace/derived/vision/<session_id>
```

The batch runner writes automatic telemetry plus one immutable candidate
revision. It does not generate an automatic motion truth file.

In ChewSenseReview, the anchors are Closed start, Maximum open, and Closed
end. Accept, edit, reject, add, or mark an event unobservable/uncertain. Finish
with a full-video pass. Any candidate revision/hash mismatch blocks saving.

```bash
chewsense-vision finalize-session \
  workspace/sessions/raw/<session_id> \
  --derived workspace/derived/vision/<session_id>
```

Finalization refuses incomplete review, freezes an immutable revision,
materializes event/phase targets, and updates the pointer and training
manifests. Re-run `audit-session` after finalization.

## Legacy Recovery

```bash
python tools/inventory_local_data.py
python tools/stage_legacy_pairs.py /path/to/source
python tools/stage_legacy_pairs.py /path/to/source \
  --destination workspace/sessions/raw --apply
chewsense-vision migrate-session workspace/sessions/raw/<session_id>
```

Migration preserves media hashes and coarse row labels where present, but
sets legacy event/phase/food-entry eligibility to zero and requires fresh
review. Do not use `--force` without retaining the existing manifest.

## Temporal Motion Model

V2 training input is `X.npy` shaped `(N, 7, 150)` and `y.npy` shaped `(N, 8)`
with `session_ids.npy` and a locked chronological split. Six raw axes are
normalized from valid training samples; the seventh channel is the untouched
gap mask. Legacy/coarse sessions may train eating only.

```bash
chewsense-motion materialize-temporal \
  --input workspace/sessions/raw \
  --output workspace/derived/motion/manual-temporal-check
chewsense-motion from-raw --input workspace/sessions/raw \
  --arch causal_tcn --no-wandb
```

Use `materialize-temporal` for a deterministic dataset/lineage check without
training. `from-raw --arch causal_tcn` performs materialization, locked split
creation, training, validation evaluation, and registration prompts. Locked
splits require at least three fully reviewed eating and three fully reviewed
hard-negative sessions; the workspace does not have those sessions yet.

After training, evaluate the exact registered checkpoint against its exact
locked test assignment and attach the result to that entry:

```bash
chewsense-motion evaluate \
  --registry-entry workspace/models/registry/<model_id> \
  --features <temporal-features-directory-printed-by-from-raw> \
  --split test_locked \
  --arch causal_tcn \
  --output workspace/reports/motion/<model_id>-test.json
chewsense-motion deploy \
  --model workspace/models/registry/<model_id>/chewnet.pth
```

Registry evaluation verifies dataset, array, split, and checkpoint lineage,
updates `metrics.json`, and writes a promotion report. Deployment still
refuses the checkpoint unless every gate passes. `chewsense-motion status`
and `registry --latest` are inspection commands only.

Deployment binds capability flags to the exact promoted checkpoint. The
longer-context food-entry head is not implemented yet; its future promotion
report remains independent from chew/cadence promotion.

## Live

Select a texture before starting. A temporal promoted checkpoint logs every
raw output, validity coverage, and decoded chew timestamp. The event head
drives chew counting; phase is telemetry. The cadence summary reports detected
chews, median interval/distribution, confidence coverage, and excluded
uncertain time.

If the bundled model is legacy eating-only, individual-chew coaching is
disabled. If food entry is not independently promoted, bite count,
chews-per-bite, and “not enough chews” are absent.

## Troubleshooting

- Review shows no session: confirm both `sessions/raw/<id>` and
  `derived/vision/<id>/candidates-vNNNN.json` exist.
- Review cannot save: reload the exact candidate revision; do not edit hashes.
- Audit reports clock errors: preserve the bundle and recapture or explicitly
  migrate; never hand-shift CSV timestamps.
- Training rejects data: verify every event, the full-video pass, and locked
  session-level splits.
- iOS Icon Composer fails locally: `tools/verify apps` performs a code/test
  build with the `.icon` source excluded; archive assets still need a normal
  Xcode release build before distribution.
