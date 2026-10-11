# ChewSense Review

<!-- WORKSPACE-VERIFICATION:START -->
## Current state and getting started

Updated 2026-10-05. Component of **Chew Sense**. ChewSenseReview is the sole authoritative human-review surface.

### Startup state

| App | Platform | Result |
|---|---|---|
| ChewSenseReview | mac | opened |

These are startup observations; simulated flows and production acceptance remain unverified.

### Install and start

#### ChewSenseReview — mac

Purpose: ChewSenseReview is the sole authoritative human-review surface.

Requirements: Xcode and the deployment version declared in the Xcode target. Physical Apple devices need Developer Mode, trust/pairing, a signing team and the required capabilities.

Open the project:

```sh
open ChewSenseReview.xcodeproj
```

Select scheme **ChewSenseReview**, choose My Mac, and choose Product → Run. Xcode builds, installs and opens the app; a successful device installation remains available for later testing.

Retained local audit build: [open the saved application bundle](</Users/zacharysturman/Library/Logs/BetterWorkspace/AppLaunchAudit/2026-10-05/derived/15e02c3a5bc5/Build/Products/Debug/ChewSenseReview.app>). Rebuild from Xcode for distributable signing.

### ChewSenseReview / mac screenshot

![ChewSenseReview startup](docs/images/startup-15e02c3a5bc5.png)

### Next steps

See [the parent project README](<../../../README.md>) for canonical Better Workspace task IDs and the matched Notion row.

- **ChewSenseReview / mac:** Turn this candidate into a fixture-backed simulated flow with explicit assertions and capture its result.

Detailed continuation: [next steps](<docs/NEXT_STEPS.md>). No archive or release operation was performed.
<!-- WORKSPACE-VERIFICATION:END -->

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
