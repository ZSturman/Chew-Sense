# Data Contract

The authoritative session and annotation schema is `2.0.0`. Version-1 labels
are historical input only: migration never marks them verified and they must
be reviewed again before training an individual-chew or food-entry head.

## Canonical Chew Semantics

| Field | Meaning |
| --- | --- |
| `start_closed_ts` | Closed/rest immediately before jaw opening |
| `open_peak_ts` | Maximum observable jaw opening |
| `end_closed_ts` | Return to closed/rest |
| `chew_event_ts` | Exactly `end_closed_ts`; counting and cadence timestamp |
| opening phase | `0 → π` from closed start to maximum open |
| closing phase | `π → 2π` from maximum open to closed end |
| `food_entry_ts` | First observable food or utensil entry through the lip boundary |
| `chewing_bout` | Chews linked to the most recent verified food entry; never a bite |

Every observable cycle satisfies
`start_closed_ts < open_peak_ts < end_closed_ts`. Adjacent cycles may share a
closed boundary. They may not overlap or be silently trimmed.

## Session Layout

```text
<session_id>/
  session.json
  <video>.mov
  <motion>.csv
  <optional-review-audio>.m4a
  annotations/
    coarse_labels.json
    review-draft.json
    review-vNNNN.json
    materialized-vNNNN.json
    canonical_events.json
    motion-labels-vNNNN.csv
  manifests/
    cv_training.json
    motion_training.json

workspace/derived/vision/<session_id>/
  candidates-vNNNN.json
  detector-telemetry.*
```

`session.json` records media paths and hashes, source app, wearer, activity,
capture interruption/gap information, model IDs, and shared host-clock
synchronization. `video_clock_origin_host_time_s` is video-relative zero.
Motion rows are mapped with that origin; the first motion sample is never
used as an inferred origin.

Optional audio is a local sidecar for VAD and human review only. It is never a
shipping motion-model input. Do not delete the file without updating the
manifest: audit intentionally treats a still-referenced audio sidecar as
required media.

## Immutable Candidates

Every vision run creates one `candidates-vNNNN.json` containing normalized,
ordered chew candidates and separate food-entry candidates. IDs are assigned
once. The envelope contains model versions and `content_sha256`, computed from
compact key-sorted JSON with the hash field omitted.

The coarse-label state is explicit:

- `reviewed_intervals`: proposals are restricted to reviewed `chewing` spans.
- `all_negative`: automatic chew and food-entry lists must both be empty.
- `missing`: the session is marked `requires_full_coarse_review`; it cannot be
  finalized as verified until that review is complete.

Discarded raw hypotheses remain debug telemetry with reasons. No review,
alignment, or finalization stage may repair or drop a canonical candidate.
Automatic mouth-state output is detector telemetry and never receives human
provenance.

## Review And Finalization

`review-draft.json` is mutable and contains decisions only. It names the exact
candidate revision and SHA-256. Valid dispositions are `accepted`, `edited`,
`rejected`, `added`, `unobservable`, and `uncertain`. Edited/added observable
cycles carry the three canonical anchors. A stale revision or hash blocks
saving and finalization.

Finalization requires a decision for every candidate, a completed full-video
pass, and resolved coarse review. It freezes an immutable `review-vNNNN.json`,
materializes targets, and atomically updates the small
`canonical_events.json` pointer. Human revisions and candidate artifacts are
never overwritten.

After review, every observable chew links to the most recent verified food
entry. A repeated verified food entry starts a new group even if chewing did
not stop. Chews without a preceding entry remain in an explicitly unlinked
chewing bout.

## Derived Motion Targets

`motion-labels-vNNNN.csv` is reproducible output, not editable truth. It includes
video-relative time, coarse eating, chew-event impulses, phase sine/cosine,
validity masks, stable IDs, annotation revision, and confidence. Artifact
hash lineage lives in the pointer and training manifests. Rejected candidates
remain reviewed negative evidence for the event head; unobservable and
uncertain spans are masked out, and only observable cycles supervise phase.
Legacy/coarse-only sessions have event and phase loss masks set to zero. The
current eight-column temporal target has no food-entry target.

## Shipping Capability Boundary

The promoted personalized model may expose eating, individual chew events,
phase telemetry, cadence, and confidence coverage. A longer-context
food-entry head is not implemented in the shipping model yet; its independent
gate/report scaffold cannot enable UI by itself. Food-entry, bite count,
chews-per-bite, and “not enough chews” remain hidden until the head exists and
passes that gate. A chewing bout is never presented as a detected bite.

## Phase 0 additive review context

Schema-v2 review drafts/revisions may include `review_context` with `mode`
(`assisted` or `blind`), `active_seconds`, `sync_verified` and `excluded_spans`
(`start_ts`, `end_ts`, `disposition` uncertain/unobservable). Historical review
hashes remain valid: absent context is not inserted into their serialized hash.
New finalization without explicit synchronization confirmation produces masked
motion supervision. Visual verification remains independently meaningful.

Blind review requires an empty candidate artifact and manually added events.
The evaluation command requires frozen independent gold and exact video hash
provenance; legacy diagnostic copies cannot establish independent evaluation.
Candidate debug provenance snapshots model/code/config metadata, source video
and coarse-label hashes, and display-timing/missing-frame metadata. Changing
coarse labels after inference requires new candidates and a new review.

Projection requires six finite sensor axes and is limited to 40 ms inside valid sensor coverage; interior outages
over 80 ms cannot receive event impulses. Full-cycle sensor coverage is required
for phase. Free exclusions and unknown/coarse gaps mask every task; downstream
resampling cannot borrow mask validity from a distant surviving row. Reports
and predictions never automatically set review verification or synchronization.

## Guided-session sidecar (`guided_session.json`)

Guided Collect recordings add `guided_session.json` beside `session.json`
(schema `guided-session/1`, validated by `chewsense.loop.guided`). It records
the verbatim experiment definition and SHA-256, actual host-clock times of
every instruction, start, pause, restart and completion, invalid-quality
intervals, quality fractions and on-device validation. Its
`video_clock_origin_host_time_s` must equal `session.json`.

It is **intended activity only** (`labels_status: intended_only_not_verified`).
It never becomes coarse labels, review decisions, training targets or
evaluation truth, and it is copied only into assisted working copies, never
blind ones. The only label Collect may add for a guided recording is the
wearer's explicit whole-recording no-food confirmation, stored in
`coarse_labels.json` as `human_coarse` with `basis: wearer_no_food_attestation`.
See [GUIDED_LOOP.md](GUIDED_LOOP.md).
