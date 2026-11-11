# Phase 0: first video-assisted AirPods experiment

> The manual workflow below still works. The [guided collection loop](GUIDED_LOOP.md)
> now automates transfer, working copies, Vision, finalization of verified drafts
> and evaluation; use its 90-Second Baseline experiment for the first recording.

> November 10, 2026: a [validity study](VALIDITY_2026-11-10.md) of the chewing-bout
> detector found it responds to 5–25 Hz jaw vibration, and that the resampling
> grid damps that band by an amount that drifts in Live. Experiment folds now
> keep recordings of one sitting together. Next recordings: talking, drinking,
> gum, soft and crunchy food.

Updated October 26, 2026. **Ready for a device preflight and first experimental
recording. Physical synchronization and recognition accuracy are not validated.**
No model was trained or promoted. Existing recordings remain legacy diagnostics.

## What changed

The existing Collect → Vision → Review → Motion path remains authoritative.

- Python motion resampling uses absolute timestamp tolerances and recognizes
  rounding on either side of an actual sample. Swift follows the same rule;
  it rejects clock resets instead of sorting them away.
- Event projection requires a sample within 40 ms, inside measured coverage,
  outside gaps over 80 ms. Phase requires coverage across the complete cycle.
  Multiple events cannot silently collapse onto one row.
- Temporal dataset masks cannot borrow valid labels across outages or pre-roll.
  Missing coarse review, unknown spans, unverified reviews and unconfirmed
  synchronization cannot silently become negative supervision.
- Video decoding uses ffprobe display PTS and checks the decoded frame count.
  Original leading empty edits and variable frame times are retained. Existing
  frame-based vision rules run on a 30 Hz nearest-frame analysis grid; missing
  evidence farther than 50 ms is masked. Source indices/PTS are exported.
- MediaPipe Face/Hand Landmarker float16 **version 1** files are checksum-pinned,
  downloaded atomically and checked on every cache hit. Config, source code
  digest, model hashes and source media provenance accompany proposals.
- Optional manifest audio is used for VAD. Its initial offset is retained by
  timestamp-aware audio extraction. Missing audio means unknown speech status.
- Collect serializes appends, buffers, flushing and stop; stale readiness checks
  cannot stop subsequent captures. Capture-clock PTS/DTS are explicitly converted
  to the host clock. New session names avoid same-second collisions.
- Export creates a real ZIP using native file coordination, checks required
  media and hashes, and includes nested annotations and optional audio.
- Review supports free uncertain/unobservable ranges, an explicit active-time
  timer, and a synchronization confirmation. Signal playback works around the
  playhead even without a selected chew. No fabricated signal substitutes appear.
  New telemetry includes visual head speed for marker inspection.
- Review parses numeric tokens without Foundation decimal-to-double rounding
  before computing Python-compatible candidate hashes. Long confidence values
  from actual video inference now retain their exact lineage.
- Independent blind review uses a separate working workspace with an empty
  immutable candidate artifact. It exposes original video and manually added
  events; model traces, proposals and motion evidence stay hidden.

## Requirements and one-time startup on the Mac

Use the active repository, Xcode, Python 3.12, and the checked-in lockfile.
The existing iPhone target requires iOS 26.1 or later; Review requires macOS 14
or later. ffmpeg **and ffprobe** must be on PATH.

```sh
cd '/Users/zacharysturman/Workspace/Projects/Chew Sense/ChewSense'
uv sync --locked
command -v ffmpeg
command -v ffprobe
open apps/collect-ios/ChewSenseCollect.xcodeproj
```

The first command restores the locked environment. The two checks should print
executable paths. In Xcode select **ChewSenseCollect**, your signing team, and
your connected iPhone; enable Developer Mode/trust if needed, then Product → Run.
Use an actual iPhone with AirPods for capture, rather than a simulator.

Models are cached in `workspace/models/mediapipe`, or `CHEWSENSE_MODELS_DIR`.
Downloads total about 12 MB, not an external dataset. A hash mismatch fails
visibly; move the invalid cache file aside and retry rather than bypassing the
check. Assets:

| File | SHA-256 |
|---|---|
| face_landmarker.task | `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff` |
| hand_landmarker.task | `fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1` |

## First recording on the iPhone

1. Wear and connect the AirPods to this iPhone. Open Collect, allow camera/motion
   access, and wait for motion availability. Keep review audio off for the first
   preflight, or explicitly opt in and allow the microphone. Record device,
   iOS, AirPods model/firmware and audio choice in your experiment notes.
2. Prop the iPhone upright, front camera facing you, with mouth and head clearly
   visible and stable light. Avoid handling the phone during recording.
3. Tap **Eating** for a mixed chewing/non-chewing session. This is a capture
   context choice; individual chews still require review. Wait until the UI
   says **Recording**, then remain still for about 3 seconds.
4. Make a distinctive marker: one clear nod, a pause, then two slower nods.
   Keep the mouth still. This must move the AirPods and be visible in video.
   Repeat the pattern around the middle and end. Markers are excluded from
   chewing supervision.
5. A simple repeatable 90-second plan is below. Approximate times suffice for
   execution; annotate actual transitions after watching the video.

   | Approximate time | Action |
   |---|---|
   | 0–10 s | Still baseline, then beginning nod marker |
   | 10–20 s | Still, not chewing |
   | 20–40 s | Chew a small mouthful, mouth visible |
   | 40–50 s | Finish/swallow, then middle nod marker |
   | 50–65 s | Speak a sentence, smile, then look left/right without chewing |
   | 65–80 s | Another short chewing segment |
   | 80–90 s | Finish/swallow, ending nod marker, then stillness |

6. Tap **Stop**, then wait for **Saved**. Any recorder error, missing motion or
   interruption makes this a diagnostic recording until checked. Resume after
   motion loss creates a separate segment; do not join segments by assuming a
   continuous clock.
7. Open the recording detail, watch it, and mark the actual coarse chewing
   intervals using the start/stop markers. Complete labels. Export with
   **Share Session**, AirDrop the ZIP to the Mac, and unzip it into a new folder.
   Keep that received ZIP/folder unchanged as your source recording.
8. Confirm the extracted folder contains `session.json`, `.mov`, `.csv`,
   `annotations/coarse_labels.json` and, if opted in, `.m4a`. A share-sheet
   success is not evidence of complete transfer; the audit below checks it.

First do a 15-second disposable preflight with beginning/end markers and no
food to confirm video, motion and transfer. Then perform the 90-second session.
Also repeat once with optional audio on and once with immediate start/stop;
record any errors. These device checks have not been performed by Codex.

## Prepare working copies on the Mac

Set `SOURCE` to the **extracted session folder containing session.json**, not the
ZIP or its enclosing Downloads directory. Set `SESSION` to that folder's exact
session ID from `session.json`.

```sh
cd '/Users/zacharysturman/Workspace/Projects/Chew Sense/ChewSense'
SOURCE='/absolute/path/to/extracted/session-folder'
SESSION='exact-session-id'
PILOT="$PWD/workspace/experiments/first-recording"
export CHEWSENSE_MODELS_DIR="$PWD/workspace/models/mediapipe"
.venv/bin/chewsense-vision prepare-experiment "$SOURCE" --workspace "$PILOT/assisted"
.venv/bin/chewsense-vision prepare-experiment "$SOURCE" --workspace "$PILOT/blind" --blind
```

Expected: two new, checksum-verified copies with separate annotations. Copying
uses APFS clones where supported, never hard links or write-through symlinks.
Existing destinations are refused. Old individual-event reviews and model
outputs are not copied. Legacy source media links are dereferenced into copies.

If the source lacks coarse review, `prepare-experiment --blind` leaves event
initialization pending. Watch the raw video first, then use the existing Collect
coarse UI or this small CLI on each fresh working copy **before** proposals:

```sh
.venv/bin/chewsense-vision review-coarse "$PILOT/blind/sessions/raw/$SESSION" \
  --chewing '20:40' --chewing '65:80' --unknown '0:10' \
  --unknown '40:50' --unknown '80:90' --reviewer 'your-initials' --full-video-pass
.venv/bin/chewsense-vision init-blind-review "$PILOT/blind/sessions/raw/$SESSION" \
  --derived "$PILOT/blind/derived/vision/$SESSION"
```

The ranges above are **examples**, not labels to copy without watching. All time
outside the supplied chewing/unknown spans is explicitly reviewed not-chewing.
No `--chewing` options means a full reviewed negative video. The CLI retains a
numbered coarse-review snapshot and refuses edits after candidate generation.
Repeat the coarse-review command with the actual ranges on the assisted copy.
Blind initialization is unnecessary when the original source already had coarse
review and `prepare-experiment --blind` created its candidate artifact.

## Independent annotation and assisted review

For independent accuracy evaluation, complete the blind pass **before looking
at model proposals**. Prefer a different annotator, or counterbalance separate
new sessions, when measuring time saved; familiarity otherwise biases timing.

```sh
open apps/review-macos/ChewSenseReview.xcodeproj
```

Select **ChewSenseReview**, **My Mac**, Product → Run. Choose `$PILOT/blind` as
workspace, then the session. Open **Blind review** in the HUD, start the labeling
timer, watch the entire original video, and add each closed→open→closed chew.
Use A/drag to add, 1/2/3 to set anchors, and frame navigation to correct them.
Count events at the **closed end**. Mark unknown/unobservable ranges using
playhead start/end in the experiment panel. Pause the timer during breaks.
Finish the full-video pass, mark verified and Save. Do not expose model output
in this workspace. The synchronization checkbox can remain off for visual gold.

Now generate proposals in the **assisted** workspace only:

```sh
export CHEWSENSE_WORKSPACE="$PILOT/assisted"
.venv/bin/python pipelines/vision/run_on_sessions.py --only "$SESSION"
.venv/bin/chewsense-vision audit-session "$PILOT/assisted/sessions/raw/$SESSION" \
  --derived "$PILOT/assisted/derived/vision/$SESSION"
.venv/bin/chewsense-vision audit-timing "$PILOT/assisted/sessions/raw/$SESSION" \
  --out "$PILOT/assisted/reports/timing-v0001.json"
```

Expected: timestamped immutable candidates, frame telemetry, provenance and an
`ok` structural audit. An empty proposal list is a valid detector result, not
proof that chewing was absent. Scores are heuristic, not calibrated probabilities.

Choose `$PILOT/assisted` in Review. Start the timer; accept/reject/edit proposals,
add missed cycles and food entries, and mark uncertain/unobservable sections.
The graph shows normalized visual jaw/head motion and AirPods magnitudes; it is
review evidence rather than a calibrated displacement measurement. Deselect a
chew to inspect a six-second window around the playhead. Complete a full pass.

## Synchronization confirmation and safe label transfer

The intended mapping is `video_time = motion_host_time - video_clock_origin`.
A native writer regression and the synthetic MOV fixture establish the software
edit mapping, not camera/Bluetooth latency. Apple's
[startSession contract](https://developer.apple.com/documentation/avfoundation/avassetwriter/startsession(atsourcetime:))
describes empty edits for delayed first samples; frame-index/FPS time would lose
that information. Vision uses the display PTS instead.

Identify the **same individual nod motion** in beginning, middle and ending
markers. Compare visual head angular-speed features (all three exported Euler
components) with AirPods gyro magnitude and inspect the actual video. Reject
ambiguous correspondences. Do not synchronize using chewing periodicity or
stretch streams to equal lengths. Store corresponding video-relative and raw
motion-host times in a JSON array, for example:

```json
[
  {"video_ts": 5.20, "motion_host_ts": 12345.20},
  {"video_ts": 45.30, "motion_host_ts": 12385.30},
  {"video_ts": 85.40, "motion_host_ts": 12425.40}
]
```

Those are invented format examples, not measured anchors. Codex can help extract
and inspect marker traces from your exported bundle. Run:

```sh
.venv/bin/chewsense-vision audit-timing "$PILOT/assisted/sessions/raw/$SESSION" \
  --markers '/absolute/path/to/your-measured-markers.json' \
  --out "$PILOT/assisted/reports/timing-markers-v0001.json"
```

The report measures residuals under the **existing** mapping, diagnostic drift,
frame gaps, missing sensor seconds and coverage. It never changes the mapping or
automatically confirms physical accuracy. Pilot event target: median absolute
residual ≤50 ms and p95 ≤100 ms; marker/annotation uncertainty must be reported.
A minimum of three ordered correspondences is required. Check beginning/end
alignment in Review only if the evidence supports it. Keep it unchecked if
uncertain. Nonzero stored drift is rejected because this version does not apply
an affine correction. Phase accuracy is a separate, stricter device question.

Mark the visual review verified and Save. Freeze each reviewed workspace:

```sh
.venv/bin/chewsense-vision finalize-session "$PILOT/blind/sessions/raw/$SESSION" \
  --derived "$PILOT/blind/derived/vision/$SESSION"
.venv/bin/chewsense-vision finalize-session "$PILOT/assisted/sessions/raw/$SESSION" \
  --derived "$PILOT/assisted/derived/vision/$SESSION"
.venv/bin/chewsense-vision audit-session "$PILOT/assisted/sessions/raw/$SESSION" \
  --derived "$PILOT/assisted/derived/vision/$SESSION"
```

Expected: new immutable `review-vNNNN.json`, `materialized-vNNNN.json`,
`motion-labels-vNNNN.csv`, canonical pointers and existing training manifests.
No training runs. Unconfirmed clocks yield **zero supervision masks** while
preserving the independently reviewed visual events. Excluded events remain in
annotation lineage but do not move to distant samples. Inspect the masks and
coverage rather than assuming every raw CSV row is eligible. Never manually
change an immutable revision; make a new draft/revision.

## Evaluate prediction quality and human effort

Use the exact candidate revision reviewed in the assisted pass and the frozen
blind review. Adjust numbered filenames if you created later revisions:

```sh
.venv/bin/chewsense-vision evaluate-experiment \
  --predictions "$PILOT/assisted/derived/vision/$SESSION/candidates-v0001.json" \
  --gold-review "$PILOT/blind/sessions/raw/$SESSION/annotations/review-v0001.json" \
  --gold-candidates "$PILOT/blind/derived/vision/$SESSION/candidates-v0001.json" \
  --assisted-review "$PILOT/assisted/sessions/raw/$SESSION/annotations/review-v0001.json" \
  --tolerance 0.25 --out "$PILOT/assisted/reports/evaluation-v0001.json"
```

Expected: one-to-one precision/recall/F1, missed/extra IDs, count error, matched
closure timestamp MAE/p95, coverage, active correction time and time saved.
Matching maximizes count before minimizing timing error. Unknown ranges are
excluded; results report the coarse gate and are conditional on that review.
Repeat with 0.10 and 0.05 tolerance in separate report files. Empty or absent
human-time measurements produce null time savings rather than invented values.
Timing covers active event annotation/correction only; capture, coarse review,
model processing and breaks are outside that measurement.

Gold must be a frozen verified blind full-video review with only manually added
events and an empty proposal artifact. Exact source video hashes must match.
Legacy imports and copies marked legacy diagnostic are refused as independent
evaluation truth. Phase 0 legacy diagnostic copies also refuse finalization as
verified training data. Never evaluate proposals against their own accepted edits.

## Evidence from this implementation run

- Python vision and temporal regression group: 88 passed; existing CoreML
  output-name warning only. This includes synthetic end-to-end review →
  finalization → motion dataset materialization and independent-evaluation gates.
- Collect: generic iOS build passed; 12 simulator unit tests passed, including
  ZIP contents/hash checks and native delayed-writer edit mapping. No physical capture.
  Retained evidence: `build/Phase0-Collect.xcresult`.
- Review: 16 contract tests passed. Native inspection opened actual generated
  candidates, played original video with signals, displayed experiment controls
  and confirmed empty proposals/hidden traces in blind mode.
  Retained evidence: `build/Phase0-Review.xcresult`.
- Swift resampler compiled directly from the actual implementation and passed
  origin 0, 3600, 100000 and 1e9 tests; reset inputs were rejected.
- All four paired legacy videos processed on working copies. Initial results:

  | Session | Raw cycle hypotheses | Reviewable chews | Food entries |
  |---|---:|---:|---:|
  | Eating-fieldtest-20260525-185356 | 40 | 25 | 0 |
  | Not-eating-20251212-061659 | 0 | 0 | 0 |
  | video-20250919-000138 | 23 | 23 | 2 |
  | video-20251021-225301 | 5 | 5 | 1 |

  All four passed structural audit; all source-file hashes remained unchanged.
  A final short-clip rerun retained a second immutable candidate revision.
  Outputs: `workspace/experiments/phase0-20261026/smoke/`; source inventory and
  summary are under its `reports/`. These are diagnostic proposals. No human
  review was fabricated, no legacy proposal was finalized as ground truth, and
  no legacy recording was used to claim accuracy.

## Focused checks and remaining limits

```sh
.venv/bin/python -m pytest pipelines/vision/tests pipelines/motion/tests/test_temporal_chewnet.py -q
xcodebuild -project apps/review-macos/ChewSenseReview.xcodeproj -scheme ChewSenseReview \
  -destination 'platform=macOS' -derivedDataPath build/DerivedData-mac \
  CODE_SIGNING_ALLOWED=NO -only-testing:ChewSenseReviewTests test
xcodebuild -project apps/collect-ios/ChewSenseCollect.xcodeproj -scheme ChewSenseCollect \
  -destination 'platform=iOS Simulator,id=6D66A986-4F1A-416D-AD4C-43D9260FDE2E' \
  -derivedDataPath build/DerivedData-ios -parallel-testing-enabled NO \
  CODE_SIGNING_ALLOWED=NO -only-testing:ChewSenseCollectTests test
```

Use an existing available simulator ID from `xcrun simctl list devices available`
on another Mac. Full Live/application/UI suites were not run. Device marker
accuracy, microphone offsets/routes, disconnect/reconnect and rapid start/stop
must still be checked physically. The current constant-origin map does not fit
or apply affine/piecewise drift. All phase supervision requires careful marker
assessment; a checkbox is a human assertion, not an accuracy measurement.

The existing wearer ID remains `local-user`; the first pilot is a single wearer.
Assign pseudonymous participant identities explicitly before a multi-person
study. Confidence remains heuristic and detector effectiveness/time savings
remain unmeasured until new independent reviews are available. Existing startup
screenshots are historical startup evidence; capture an updated representative
Review screenshot only when requested, without exposing participant video.
