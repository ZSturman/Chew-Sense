# Physical guided-loop validation — November 2, 2026

## Current evidence

Collect 1.2 (7) was built, installed in place, launched on the connected iPad
Pro 12.9-inch (5th generation), and paired to the Mac service. The wearer used
one AirPod and completed the 90-Second Baseline. The recording contains
94.48 seconds of video, 4,699 motion samples, all nine completed activities,
and a valid registered guided definition. The Mac audit reports 99.405% sensor
coverage, 50 Hz median sampling, and two sensor gaps of approximately 134 and
139 ms. These gaps and the initial missing samples must remain masked.

The device's face/motion quality checks passed. This is capture evidence,
not measured event accuracy or confirmation that the nods are synchronized.
The session was assigned permanently to independent evaluation.

The first app upload preceded coarse labeling. The wearer subsequently watched
the recording, marked two actual chewing intervals and shared a reviewed ZIP
to Downloads. Before any candidate revision was generated, recovery verified
the exported video/motion hashes and identical clock metadata against the
received source, then copied only the reviewed coarse labels into the assisted
and blind working copies and initialized blind review. The received original
remains unchanged. Local evidence and the recovery receipt are in ignored
`workspace/experiments/device-20261102/`; normal pipeline artifacts are in
`workspace/loop/`.

Vision completed on the separate assisted copy, with face detection throughout
the recording. Its automatic synchronization check is inconclusive: one of
three nod windows produced a distinct match. Timing is still unverified.
Review is open on `workspace/loop/blind`, with zero detector proposals and an
independent labeling timer. Blind individual-event review is pending.

Physical playback exposed forced-portrait rotation on the landscape iPad.
Capture and preview now use AVFoundation's rotation coordinator, with capture
orientation/dimensions fixed per take. The wearer confirmed both the new
preview and saved video are upright. The new 21.74-second check contains 1,072
motion samples, approximately 50 Hz, with no recorded sample gaps. Original
media was not rotated or replaced. Review provides a per-video display-only
rotation setting for the older sideways baseline, verified upright in the UI.

## Changes made during testing

- Collect's **What happens next** panel shows Mac review work, processing
  errors, exact training blockers and separate Vision/AirPods-model readiness.
- Guided **Send to Mac**, including automatic uploads, now waits for coarse
  review or the explicit no-food attestation. Completion offers **Review chewing
  intervals**. Finishing review or attestation triggers enabled automatic upload.
  **Share / Share Again** remains a separate ZIP export; it does not update an
  earlier Mac-service upload.
- Review's controls now sit outside the video, signal charts are optional,
  and the session sidebar collapses after selecting a recording. The **Sections**
  panel imports phone intervals and actual guided activity times, with separate
  toggles and a chewing-only filter. Clicking a section seeks/zooms directly to
  it and focuses the candidate queue. Explicit acceptance applies only to
  proposals the wearer reviewed in that section; hidden events stay pending.
  A single undo restores the group decision. Independent benchmarks hide
  predictions and start with requested-action navigation off; the wearer can
  explicitly enable it as context without generating labels. Phone intervals
  remain available. Timeline dragging now scrubs by default, with explicit
  **Edit chews**, **Add chew**, **Delete selected**, and **Undo** controls.
  The wearer's accidental timeline addition was undone and the draft saved.
  Live UI checks then dragged the timeline to 39.565 seconds with zero added
  events, enabled requested actions and jumped directly to the actual 23.180
  second instruction start. The wearer confirmed the revised layout and
  section jumping work. The Quiet Sitting / AirPod-removal results are recorded
  in the follow-up below.
- Concurrent status writers use unique temporary files. Previously, the admin
  API and worker could race on `.status.json.tmp`, stranding a running job.
- Graceful shutdown terminates Vision. Isolated Vision workers also watch their
  service parent, so a forced restart cannot leave an orphan writing outputs.
- Failed training exits/missing results are failures; failed attempts count
  toward the weekly limit. Experimental training disables external W&B logging.
- Training now evaluates both locked validation and locked test partitions.
  Holdout membership persists between runs, with an exact per-run snapshot.
  Registry assessment remains separate from promotion; no model is auto-deployed.
- Guided confound metadata identifies talking/walking/drinking/head-movement
  context for reviewed negatives. Instructions still never become labels.
  Training readiness requires 20 reviewed hard negatives, consistent with the
  existing promotion policy; quiet sitting alone cannot satisfy that requirement.
- Temporal evaluation reports activity/food breakdowns. Locked-test motion
  failures feed focused collection requests. The planner prefers a focused
  experiment over a mixed baseline sharing its tag. Good Vision scores cannot
  stop the loop while the motion model fails its own gates.

## AirPods interruption follow-up

The first physical Quiet Sitting interruption test **failed recovery**. The
wearer removed/replaced the single AirPod; the instruction timer stayed paused.
The retained incomplete session has 135.53 seconds of uninterrupted video,
approximately 19 seconds of motion (14% coverage), a `motion_missing` pause and
no restoration event. The wearer ended the take early. Its metadata/sidecar
are preserved under `workspace/experiments/device-20261102/reconnect/`; the
original media remains on the iPad. This is failure evidence, not a passed
disconnect/reconnect check.

The collector now uses a sustained motion subscription and fresh-sample health
rather than treating `isDeviceMotionAvailable` as live readiness. Connection
callbacks and a 2.5-second bounded retry recover stale subscriptions without
restarting video or resetting the host-clock origin. Old callbacks have stream
identities and cached/stale/nonfinite samples cannot declare recovery. Readiness
checks no longer start/stop competing trial subscriptions or report a running
recording unavailable merely because a trial is prohibited.

**AirPods** is accessible from Recordings, manual capture and guided capture,
including while the instruction timer is paused. The panel shows actual audio
output and motion readiness, the system output picker, a short optional test
sound and **Retry motion connection**. Recovery does not replace an active
recording's audio category. Initial Bluetooth pairing still uses system
settings; ordinary reconnection can be attempted inside Collect.

Validation after the repair: physical-device build/install/launch succeeded;
all **28 Collect unit tests** passed, including stale-stream retry without an
availability transition, stale-sample rejection, instruction resume and retained
invalid intervals.

**Physical recovery passed in the follow-up test:** the wearer confirmed the
timer resumed automatically after removing/reinserting the single AirPod.
The intentionally shortened clip has 42.865 seconds of uninterrupted video,
1,531 motion samples and a real 11.517-second outage followed by resumed data.
CSV timestamps remain strictly increasing and its SHA-256 matches the device
manifest. The sidecar records `motion_lost`, `activity_paused`,
`motion_restored`, `quality_restored`, and `activity_resumed`; the invalid
interval remains present. No clock origin was reset and the outage was not
filled with synthetic samples.

The same clip transferred through Collect and completed all Mac processing
steps without an error (Vision: 93.6 seconds). The Mac timing audit reports
71.15% sensor coverage and 50 Hz median rate outside the gaps. It correctly
remains **incomplete**, because the wearer ended early for this check, the
complete guided sequence was not performed and coverage includes the deliberate
dropout. This is successful recovery evidence, not a complete training example
or proof of synchronization/recognition accuracy. Local follow-up artifacts
use the `recovered-*` names in the same ignored reconnect evidence folder.

Apple API references: [headphone connection callbacks](https://developer.apple.com/documentation/coremotion/cmheadphonemotionmanagerdelegate)
and [Bluetooth audio routing](https://developer.apple.com/documentation/avfaudio/avaudiosession/categoryoptions-swift.struct/allowbluetootha2dp).

## Timing check and completed software feedback loop

The wearer completed and uploaded **30-Second Timing Check**
(`Guided-timing-check-20261102-183110-5C9F98FC`). It has 30.638 seconds
of video, 1,498 strictly increasing motion timestamps, 97.52% sensor coverage,
three short retained gaps and a valid complete definition. It is permanently
**diagnostic**: it contributes neither training examples nor evaluation truth.

Testing exposed a measurement defect: automatic timing used only pitch/yaw
while Review's visual chart and gyro magnitude include all three axes. On the
third down/up nod this selected opposite motion phases and reported a false
~981 ms mismatch. The checker now consumes the actual radian pose values,
includes roll, and marks competing substantial phase matches ambiguous. Version
3 produces three distinct correspondences: visual-minus-motion residuals
**−21.8, +18.3, +85.3 ms** (absolute median **21.8 ms**, p95 **78.9 ms**).
This passes the pilot automatic limits (median ≤50 ms; p95 ≤100 ms).
The wearer then watched all three nods, confirmed their correspondence and
saved synchronization in Review. The real service subsequently reported
**one human-confirmed session**, removed the diagnostic task and changed the
next action to the baseline's independent blind review. This confirmation does
not confirm the baseline's clock or make any event labels verified.

Original media SHA-256 values were rechecked against all three manifests and
match. Earlier automatic assessments remain in `sync-assessment-v1.json` /
`sync-assessment-v2.json`; only derived reports and session state were updated.
The comparison plot is local diagnostic evidence at
`workspace/experiments/device-20261102/timing-marker-comparison.png` (the plot
illustrates the earlier two-axis failure, not the corrected three-axis result).

Collect was rebuilt, installed in place and launched. The wearer confirmed
**3 received / 2 complete / 1 incomplete**, goals **0/20 positive**, **0/20
hard-negative**, **0/6 independent evaluation**, **0/300 independent events**,
and expandable pipeline progress. A direct recommended-recording link reuses
the normal preparation, overlays, validation and upload path. Recorded catalog
counts are explicitly distinct from verified readiness. Completed timing
checks lead to inspection rather than an endless repeat request. Once timing
is consistent and human-confirmed, the timing request is retired from the
catalog while its definition and received evidence remain intact.

The isolated fixture test now executes **two learning cycles**: chunked upload,
processing, synthetic known-truth review/finalization, actual dataset creation,
small real model training, locked validation/test evaluation, registry and
promotion assessment, error-derived experiment request, another upload/review,
then a second training run. Holdout assignments remain fixed; the independent
benchmark is excluded from training; no checkpoint is promoted or exported.
This proves orchestration, not wearer accuracy. Production thresholds are
unchanged. Talking coverage now selects its focused experiment, rather than a
mixed-tag walking definition.

Training runs in the background, active jobs block overlaps, failed/missing
results are failures, and completion requests reanalysis so new test errors
reach the catalog. A stopped service terminates the training process group;
restart preserves outputs and marks interrupted work failed. Saved timing-only
confirmation also triggers reanalysis without requiring a full event pass.
The physical data still has **zero verified training/evaluation examples and
zero wearer training runs**.

The sync checkbox was initially checked in memory but ⌘S while its popover was
open did not persist it. Clicking the main Save button wrote the actual draft;
the service observed that saved file. Review now has **Save review draft** and
save status inside the tools panel. The rebuilt app was reopened on the saved
diagnostic; ⌘S from the open tools panel now visibly saves the draft, retaining
the wearer's sync confirmation and leaving full-pass/verified false.

## Validation

- 116 focused Python tests passed: guided-loop contracts, real synthetic training
  handoff, timing, temporal inference/CoreML parity, promotion, concurrent writes
  and actual worker-parent termination. Single-class AUC is reported as null
  so strict JSON publication succeeds. One existing CoreML output-rename warning remains.
- 29 Collect Swift unit tests passed (17 guided + 12 existing), including status decoding, rotation dimensions
  and withholding uploads until coarse review. 19 Review contract tests passed,
  including actual-time navigation, blinded context and focused acceptance.
  Collect's device build and Review's macOS build
  passed. Build directories were reused. Collect evidence: `build/Pipeline-Collect.xcresult`;
  Review evidence: `build/Pipeline-Review.xcresult`.
- The synthetic handoff test finalizes isolated fixture reviews, materializes
  their motion dataset, trains a small model for one epoch, evaluates both
  holdouts and registers a non-promoted checkpoint in a temporary workspace.
  Synthetic fixture scores are never wearer evidence or performance claims.
- No wearer-trained model, promotion, independently measured accuracy, baseline
  clock confirmation, full Live test or
  full application/UI suite has been completed.

## Human review is a bounded calibration step

Phone start/stop markers are coarse labels, not individual event anchors. They
are reused directly. Independent benchmarks measure the visual teacher's error;
normal assisted review starts with computer-generated events and requests
corrections, rather than manually creating every chew. The section controls
reduce navigation and repeated acceptance. They do not silently declare unseen
events correct. The current policy still requires sufficient verified training
and independent evaluation data before a wearer model can train; no accuracy
gate was lowered to make the demo pass.

## Continue the physical experiment

1. In the already-open Mac blind review, start the labeling timer. Independently
   annotate each visible closed → open → closed chew, with the event at the
   closed end. Use A to add, 1/2/3 for anchors, and arrow keys for frames. Mark
   unobservable/uncertain spans rather than guessing. Watch the full video,
   click **Full pass**, **Verify**, and **Save**. Leave synchronization
   unconfirmed until the independent event pass is complete.
   **More → Rotate video right/left** adjusts display of older clips without
   editing media or timestamp mappings. **Sections** jumps to phone-labeled
   ranges. Requested actions use actual recorded times. They start off in the
   independent benchmark but can be enabled as navigation context; detector
   proposals remain hidden throughout independent event annotation.
2. After the blind revision freezes, check the recorded nods and signal timing,
   complete the assisted review, and assess proposals against blind gold.
3. Record a food-free experiment and a controlled AirPods interruption. Confirm
   the instruction timer pauses, capture continues, freshness recovers, gaps
   remain excluded and the app offers any necessary repeat segments.
4. Refresh **Collect Required Data** to see the returned catalog and blockers.
   Repeat requested recordings and independent review until data gates pass.

From the active repository, use these local service commands:

```sh
tools/loop-service status
.venv/bin/chewsense-vision loop analyze
```

`status` shows the current processing step and review-task count. `analyze`
refreshes evaluation, readiness and the published catalog; experimental training
can start only if every gate passes. Do not force-restart the service merely to
refresh status. Use Review → Pipeline for routine controls.

Focused regression command:

```sh
.venv/bin/python -m pytest pipelines/vision/tests/test_loop.py \
  pipelines/vision/tests/test_pipeline_round_trip.py \
  pipelines/vision/tests/test_loop_training.py \
  pipelines/vision/tests/test_loop_process_lifetime.py \
  pipelines/vision/tests/test_phase0_timing.py \
  pipelines/motion/tests/test_temporal_chewnet.py \
  pipelines/motion/tests/test_promotion.py -q
```

Expected: 116 tests pass. Wearer data and models remain under ignored workspace
paths. Existing source changes, icons, app identities and signing settings are
preserved.
