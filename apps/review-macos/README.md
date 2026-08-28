# ChewSense Review

ChewSenseReview is the sole authoritative human-review surface.

For every proposed chew it loops a closed-to-open-to-closed clip with at least
five frames of context. Reviewers can accept, reject, edit boundaries, add
missed chews, review food-entry events with wider context, and perform a
final full-video pass.

A session can be verified only after every proposed chew and food entry has been
assessed and the full-video pass is acknowledged.

Review opens exactly one immutable `candidates-vNNNN.json`. Its Python-compatible
`content_sha256` must match both the file contents and `review-draft.json` before
the app will save or mark the review complete. A missing/stale artifact is an
error; the app never substitutes another revision or trims overlapping cycles.

The cycle chart overlays the visual jaw-open signal with synchronized AirPods
acceleration and rotation evidence when `session.json` provides the video-clock
origin. Motion is reviewer context only and never becomes independently edited
truth.

## Build

Open `ChewSenseReview.xcodeproj` in Xcode and run the `ChewSenseReview`
scheme.

The shared scheme includes `ChewSenseReviewTests`:

```sh
xcodebuild -project ChewSenseReview.xcodeproj \
  -scheme ChewSenseReview -destination 'platform=macOS' \
  CODE_SIGNING_ALLOWED=NO test
```

## Main Controls

| Key | Action |
| --- | --- |
| Space | Play or pause |
| A | Add chew at playhead |
| B | Add food-entry event at playhead |
| Delete | Reject selected chew or food entry |
| N / P | Next or previous chew |
| 1 / 2 / 3 | Set Closed start, Maximum open, or Closed end |
| Arrow keys | Frame navigation and marker nudging |
| Command-S | Save the working decision draft |

Saving writes only `session/annotations/review-draft.json`. Final canonical
materialization and immutable review revisions are performed by
`chewsense-vision finalize-session`.

See [`../../docs/RUNBOOK.md`](../../docs/RUNBOOK.md).
