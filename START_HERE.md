# Start Here

## The Three Apps

1. `apps/collect-ios`: paired video, AirPods motion, shared-clock metadata,
   optional local review audio, and coarse chewing intervals.
2. `apps/review-macos`: authoritative candidate decisions and the final
   full-video pass.
3. `apps/live-ios`: promoted motion-only inference and cadence coaching. It
   does not create ground truth.

Anything under `archive/` is recoverable history, not active source.

## Bootstrap And Verify

ChewSense uses the checked-in Python 3.12 lock:

```bash
uv sync --locked
./tools/verify fast
./tools/verify fixture
```

Apple builds and tests are grouped under:

```bash
./tools/verify apps
```

Run all checks with `./tools/verify all`. Physical AirPods behavior remains a
recorded promotion check and cannot be replaced by CI.

## One Session

For a new Collect bundle at `workspace/sessions/raw/<session_id>`:

```bash
python pipelines/vision/run_on_sessions.py --only <session_id>
chewsense-vision audit-session \
  workspace/sessions/raw/<session_id> \
  --derived workspace/derived/vision/<session_id>
```

Vision writes immutable `candidates-vNNNN.json`. Open ChewSenseReview, select
`workspace/`, decide every candidate, mark unobservable/uncertain events when
needed, add missed events, and complete the final full-video pass. Review
saves decisions only and refuses a stale candidate hash.

Then finalize and audit again:

```bash
chewsense-vision finalize-session \
  workspace/sessions/raw/<session_id> \
  --derived workspace/derived/vision/<session_id>

chewsense-vision audit-session \
  workspace/sessions/raw/<session_id> \
  --derived workspace/derived/vision/<session_id>
```

The mutable `canonical_events.json` is only a pointer to immutable candidate,
review, and materialized revisions; it records the selected candidate revision
and hash, not a path to a mutable candidate copy. Motion-row targets are
derived output.

To check training lineage without fitting a model:

```bash
chewsense-motion materialize-temporal \
  --input workspace/sessions/raw \
  --output workspace/derived/motion/manual-temporal-check
```

Training and locked registry evaluation begin only after the dataset gates in
[docs/RUNBOOK.md](docs/RUNBOOK.md) are satisfied.

## Legacy Recordings

Never train individual chews from old event labels. Stage recoverable exact
video/motion pairs non-destructively, then migrate explicitly:

```bash
python tools/stage_legacy_pairs.py /path/to/legacy/media
python tools/stage_legacy_pairs.py /path/to/legacy/media \
  --destination workspace/sessions/raw --apply

chewsense-vision migrate-session \
  workspace/sessions/raw/<session_id> \
  --source-app legacy_import
```

The wrapper remains unverified. Missing coarse labels require a full coarse
review; an old all-false row label is the only case treated as explicit
all-negative.

## Before Live Promotion

The first release is personalized to one wearer. Do not deploy a temporal
checkpoint until every initial chew gate in [docs/ROADMAP.md](docs/ROADMAP.md)
passes on locked chronological sessions and a physical-AirPods smoke check is
recorded.

Live requires a meal texture (`soft`, `mixed`, `hard`, or `unknown`). It may
show individual chews, cadence, coverage, and uncertain time only when the
temporal chew capability is promoted. Bite and chews-per-bite UI remains
hidden until the separately promoted food-entry capability passes.
