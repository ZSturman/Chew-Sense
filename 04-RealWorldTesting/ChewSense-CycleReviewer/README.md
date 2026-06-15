# CycleReviewer

CycleReviewer is the sole authoritative human-review surface.

For every proposed chew it loops a closed-to-open-to-closed clip with at least
five frames of context. Reviewers can accept, reject, edit boundaries, add
missed chews, review food-entry bite events with wider context, and perform a
final full-video pass.

A session can be verified only after every proposed chew and bite has been
assessed and the full-video pass is acknowledged.

## Build

Open
`ChewSense-CycleReviewer/ChewSenseCycleReviewer.xcodeproj` in Xcode and run the
macOS target.

## Main Controls

| Key | Action |
| --- | --- |
| Space | Play or pause |
| A | Add chew at playhead |
| B | Add bite event at playhead |
| Delete | Reject selected chew or bite |
| N / P | Next or previous chew |
| 1 / 2 / 3 | Set chew start, peak, or end |
| Arrow keys | Frame navigation and marker nudging |
| Command-S | Save the working review and immutable numbered snapshot |

Saving preserves automatic predictions and writes review deltas. Final
canonical materialization is performed by `chewsense finalize-session`.

See [`../../docs/PIPELINE_RUNBOOK.md`](../../docs/PIPELINE_RUNBOOK.md).
