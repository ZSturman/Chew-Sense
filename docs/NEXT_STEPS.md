# Autonomous checkpoint — November 7, 2026

Details and reproduction: [device/pipeline report](DEVICE_PIPELINE_2026-11-06.md).
Continues `claude/phase2-2026-11`; Claude's `3e8fb8c` checkpoint was already
present. Source/blind/non-media state was checkpointed before edits under
ignored `workspace/checkpoints/resume-20261106/`.

**Completed:**
- Built, installed in place and launched Collect 1.2 (7) and Live on the physical
  iPhone 16 Pro Max (iOS 27.2). Live: 22 focused device tests passed, including
  exact synthetic-stream parity; Debug CPU median 17.7 ms/p95 19.8 ms per valid
  window (500 ms stride). A separate real capture is recorded below.
- Collect: 18 guided device tests passed; copied the actual device-generated
  fixture sidecar back to the Mac and passed the Python contract check. Fixed
  Mac-only resource paths in both app test suites and isolated catalog test
  preferences so fixture syncing cannot reset wearer progress.
- Rebuilt Review: 25 passed, 1 optional render skipped; actual blind UI still
  has zero proposals and disables assisted review.
- Copied prior real iPhone take `Not-eating-20261026-174428-C27C9380` over USB,
  verified hashes/channels/timing and processed it through the running loop.
  25.255 s, 1,249 samples, 50 Hz, 98.828% coverage, no internal gaps; actual
  sync unverified. Existing wearer coarse negatives retained. It is fixed
  evaluation with an untouched blind copy; no new human truth was created.
- First real in-service sandbox run verified: two Clearcut attempts failed;
  same worker PID's system logs show DNS service denial/EPERM/URL error -1003.
  Evidence: `workspace/experiments/device-20261106/network-denials.log`.
  Hardened the privacy probe's resource-exhaustion fail-open case; sandbox and
  no-override policy unchanged. 14 privacy tests passed.
- Fixed data integrity: reject hash mismatches, escaped motion paths, malformed
  numeric CSVs, wrong-session phone/assisted labels, and incomplete guided takes
  even after review. Identity hardening preserved all 32 eligible recordings;
  33 affected data/assistance/end-to-end tests pass. The Phase 2 run
  had accidentally included **46 negative development windows** from incomplete
  Quiet Sitting. Its historical results and original data are retained.
- Trained all five candidates development-only in `eating-20261107-011934`
  (November 6 local): 22 dev/10 locked-evaluation recordings. HGB remains best:
  precision .8664, recall .8703, F1 .8683, AUC-PR .9444, FPR .0284; 17/17 bouts,
  7 false bouts (9.1/negative hour), median onset lag 4.08 s. Pilot/legacy labels,
  not real-world or individual-chew validation. No new locked-test predictions;
  test consultations remain 2. The prior 32 split records are unchanged and one
  evaluation assignment was appended. Protected recordings, verified labels and
  received files match their checkpoint hashes. Saving the blind draft before
  reopening Review changed only its timestamp.
- Dashboard now shows the newest completed development check and its own scores
  rather than stale evaluated scores; installed/export models remain unchanged.
  It also displays the scoped physical fixture/CPU evidence for the matching ID.
- Collect now reports Bonjour policy denial with Open Settings/Retry discovery
  instead of searching forever. Actual device confirmed **Local Network is off**.

**New meal processed:** the wearer enabled Local Network; authenticated pairing
and catalog sync succeeded. The real meal take
`Guided-natural-meal-20261106-174058-8029B059` saved 210.59 s / 10,518 motion
samples / 99.869% sensor coverage / one 240 ms gap; device face visibility was
99%. The wearer confirmed intentional End Early. It lacks the final nod and
remaining periods, is explicitly incomplete, and stays excluded from training/
evaluation. Originals are preserved. Actual service processing finished:
521.2 s Vision, 159 automatic cycles and 15 food-entry proposals; coarse proposals
created. Synchronization is inconclusive. These are automatic, unverified outputs.
The meal's Vision worker logged 16 failed Clearcut attempts. System logs for
that worker show 16 mDNS denials and 16 zero-byte HTTP failures.
Six review tasks remain. Natural Meal v1 also defines only two marker windows,
while the automatic synchronization pass needs three. The running service now
serves catalog v10, with a new 6:15 Natural Meal v2 and three nod prompts. The
v1 digest and all other catalog entries match the pre-publish checkpoint.
On the iPhone, **Mac connection → Sync now** will fetch v2 for the next take.

**Live simplified and tested with AirPods:** use **Connect AirPods → New model
check → Start model check → Stop and save**. Collect and Live share the same
audio route picker, test sound and fresh-motion/retry panel. A check can start
once fresh samples arrive. Reference video defaults off. Camera-free checks
record no video/audio, stay unlabelled and cannot be benchmark candidates.
Saved checks distinguish no motion, motion without predictions and unreviewed
predictions. Prior empty captures remain intact.

The wearer reported **Motion is ready** after selecting the AirPods, playing
the tone and retrying. The device diagnostics confirmed authorized fresh motion.
`FieldTest-20261107-023850-E7978B9B` saved 32.13 s, 1,590 real samples across six
axes, two gaps (largest 290 ms), 313 ChewNet windows and 59 valid experimental
shadow windows. AirDrop and USB copies match and the motion SHA matches. Mac replay
matched all 59 experimental decisions (maximum probability difference 4.97e-9).
ChewNet reported 19 chewing windows and the shadow model none; the check was
unlabelled. No recognition accuracy or battery measurements follow from it.
Reproduce with `.venv/bin/chewsense-motion eating-parity --live-check` and the
saved folder (exact path in the report). This reads no dataset or split.

**Independent labeling:** the wearer reported unreliable frame stepping. Review
now uses the video's actual frames, keeps keyboard focus available to controls,
and applies the selection loop only during playback. Rebuilt and reopened the
blind baseline with predictions hidden. Verified Right and Previous frame on
the actual baseline and a 25 fps regression fixture. At restart the saved draft
had 0 events, 0 timer seconds and no full pass, verification or sync confirmation.
Use phone-labelled sections 24.1–44.2 s and 61.5–75.3 s, then check the rest.
Freeze it before inspecting baseline assisted proposals. Quiet Sitting stays
diagnostic. Measured review time and verified event quality remain pending.
Once the wearer finishes the full pass, verifies and saves, use the
[Phase 0 finalize command](PHASE0.md#synchronization-confirmation-and-safe-label-transfer) with
`workspace/loop/blind` and the baseline session ID. Confirm clocks only from
evidence. If they are uncertain, freeze the visual labels with motion targets
masked.

**Next Codex work:** freeze a completed human blind draft, then run the assisted
workflow and record review time. Collect a complete three-marker Natural Meal
v2 and completed Quiet Sitting, talking, drinking, head movement, gum and mixed
speech/eating takes. Prioritize synchronized positive and hard-negative examples
for development before another training run. Compare a labelled on-device check
to independent truth and measure sustained runtime. Keep the locked test split
and the 20 positive/20 hard-negative/6 evaluation/300 gold-event gates intact.

Native evidence: `build/Device-Live.xcresult`, `build/Device-Collect.xcresult`,
`build/DerivedData-mac/Logs/Test/Test-ChewSenseReview-2026.11.06_17-13-01--0800.xcresult`.
No developer build/command action is needed; Codex already ran these checks.

---

# MediaPipe privacy fix — November 4, 2026

Details: [PRIVACY_MEDIAPIPE_2026-11-04.md](PRIVACY_MEDIAPIPE_2026-11-04.md).
Same branch.

**Done:** MediaPipe 0.10.35 contains Google's Clearcut usage reporter, and
Google offers no switch to disable it. Its schema holds metadata (task, mode,
latencies, frame counts, versions), not frames. Earlier runs probably sent
reports. Landmarkers now run only in a process with all sockets and DNS
denied; outside one they refuse to start. This takes effect for the running
service's Vision step without a restart, because that step starts
`run_on_sessions.py` from disk and the script re-runs itself sandboxed.
Vision output is unchanged: the 30 s timing check is bit-identical apart from
the Phase 2 calibration. 64 tests passed.

**Triage of the Phase 2 outstanding items:**

| Item | Who | State |
| --- | --- | --- |
| MediaPipe telemetry | agent | done (above) |
| Dev-only experiment rerun + `pipeline-status` | agent | waiting for new reviewed data; a rerun now would reproduce `eating-20261104-030715` |
| Assisted review of the two proposal sets | you (Mac, Review) | open |
| Service restart (publishes Natural Meal, enables automatic proposals) | agent | done Nov 4 |
| Live shadow test with AirPods | you (iPhone) | open |
| Natural Meal, talking, head-movement, drinking, gum recordings | you (iPhone) | open |
| Pace cue, drinking attestation | agent, on your approval | needs a Collect build and an explicit v2 publish |
| Pin `mediapipe<=0.10.21` instead of sandboxing | you decide | not done; changes the inference binary behind all stored Vision outputs |
| Merge/push the branch | you decide | open |

**Service restarted** (Nov 4, 03:24, after commit `eb9ae80`): same port and
certificate, idle, nothing pending. Seeded Natural Meal, chewing-pace,
facial-expressions, gum-chewing and talking-while-eating. `loop analyze`
published catalog v8 (13 experiments); no training ran. The privacy override
was removed: nothing bypasses the sandbox. All 242 pre-restart hashes match
except runtime timestamps, the catalog and the analysis report. A non-media
state copy is in `workspace/backups/loop-state-20261104-pre-restart/`. Other
packages were audited: no automatic telemetry. LM Studio is loopback-only.

Review still runs the Nov 2 build (no Chewing spans panel or Training window).
Finish the blind review in it, quit, then rebuild with the README command.

---

# Phase 2 checkpoint — November 3, 2026

Details: [PHASE2_2026-11-03.md](PHASE2_2026-11-03.md). Branch
`claude/phase2-2026-11` (from `claude/audit-2026-11`). All work is committed.

**Completed and verified (do not redo):**
- On-device detector: `eating-export` spec `eating-hgb-20261104-015911-7a28dd15`
  bundled in Live (shadow by default, experimental mode in Settings). Swift
  vs Python parity passes on 32/32 recordings (bit-identical features,
  probability difference 0). Live simulator tests 33/33.
- Assisted labeling: `assist-propose` (also a loop step after Vision),
  Review → Chewing spans panel, `coarse-review-vNNNN.json` saved revisions,
  training-only tiers. `assist-eval`: 374/374 agreement windows correct,
  59% coverage of 637 labelled windows. Proposals exist for the two loop
  recordings (`loop/assisted/derived/assist/*/coarse-proposals-v0002.json`).
- Transparency: `pipeline-status` plus Review's Training and Evaluation
  window (⇧⌘T). Review tests: 25 passed, 1 opt-in skip.
- Behaviour foundation: `pipeline/behavior/`, `behavior-eval`. Vision chew
  confidence is now rhythm-calibrated (AUC 0.93 vs 0.69).
- Natural Meal seed protocol; Collect bundled catalog regenerated (Collect
  29/29).
- Python 207 passed. The development-only rerun reproduces the reference
  development metrics exactly.

**Not done / outstanding:**
- No physical-device test of the exported detector, and no measured
  assisted-review time.
- The loop service still runs pre-Phase-2 code (no restart was made). A
  restart publishes Natural Meal to Collect and enables automatic proposals.
- ~~MediaPipe attempted "clearcut" usage logging during a Vision run.~~
  Resolved on November 4 (see above).
- Not implemented: a chewing-pace target or metronome (needs a Collect build
  plus an explicit v2 publish) and a drinking "no chewing" attestation.
- Branch not merged or pushed.

**Next steps (in order):**
1. Human: review the two proposal sets in Review → Chewing spans, open on
   `workspace/loop/assisted` (your blind review stays on `loop/blind`), and
   save. This gives the first real review-time measurement.
2. Human: decide whether to restart the service:
   `tools/loop-service uninstall && tools/loop-service install`.
3. Human: in Live, record a short session in shadow mode with AirPods, then
   compare `eating_detector.csv` with ChewNet's `predictions.csv`.
4. Record Natural Meal, talking, head-movement, drinking and gum sessions,
   and label them with assisted review.
5. Agent: `chewsense-motion eating-experiment --dev-only`, then
   `pipeline-status`. Run the locked-test experiment only to confirm.

---

# Audit continuation — November 3, 2026

Details: [AUDIT_2026-11-03.md](AUDIT_2026-11-03.md). Branch
`claude/audit-2026-11`.

**Done:**
- Trained and evaluated motion-only chewing-bout detectors on 32 real
  recordings with provenance-tiered human labels, locked session splits
  and an HTML report (`workspace/experiments/eating/`).
- Measured a video-teacher pilot: video and motion agreed on 82% of
  windows, and every agreed window matched the human label.
- Added four confound experiments to the catalog.
- Added detector status to Collect and Review.

**Human next actions:**
- Record `talking`, `talking-while-eating`, `baseline-90s`, a food texture,
  `head-movement` and `drinking`, in that order. Review their chewing spans
  on the phone and send them to the Mac.
- Restart the loop service (`tools/loop-service uninstall && tools/loop-service
  install`) and rebuild Review so they pick up the new code, stage and
  panel. The service was left running untouched.

**Codex/agent next actions:**
- Implement agreement-based coarse proposals in Collect's review.
- Port the detector features to Swift/CoreML and verify parity.
- Rerun `chewsense-motion eating-experiment` after new reviewed data
  arrives. Develop with `--dev-only`; never tune on the locked test.

---

# TestFlight refresh — October 31, 2026

Collect **1.2 (7)** is **VALID / IN_BETA_TESTING** for the existing personal account holder. Store record **6755277802** and bundle identity are preserved. Earlier betas were expired; Apple refused the closed 1.1 train, so the current source/app version is 1.2. Compliance metadata was completed, and the source now declares OS-provided encryption exemption for subsequent uploads. Beta notes describe physical guided-loop validation as outstanding.

Signed archive, deep/strict signature and source-hash evidence: `build/StoreRefresh/chew-ios/`. Existing guided software/unit/contract evidence below was reused; no fresh physical session, model training, promotion or public update submission was performed. Historical shells/debugging apps were not added.

Next: accept the personal TestFlight invitation and update Collect in place. Follow `GUIDED_LOOP.md` for a new disposable baseline, AirPods interruption/gap, exported files and explicit Mac transfer. Complete independent paired-session review before public release/promotion. No duplicate upload is needed. Aggregate evidence is in Home Page `docs/RELEASE_AUDIT_2026-10-31.md`.

---

# Continuation

## Physical guided loop — November 2, 2026

**Current:** all three received physical recordings completed Mac processing.
The 30-second diagnostic now passes corrected three-axis automatic timing
(median 21.8 ms, p95 78.9 ms), and the wearer watched the nods and saved clock
confirmation. The service observed the saved draft, removed that task and
changed Collect's next step to the independent baseline review. This does not
confirm other recordings' clocks or constitute event ground truth.

**Completed:** in-place Collect install and physical confirmation of next
steps/goals/stages; upright capture/playback; AirPod reconnection with genuine
gaps retained; reviewed upload gating; read-only received sources; simpler Mac
review/navigation/scrubbing; dynamic timing request and diagnostic-role
isolation; full two-cycle fixture upload/review/train/test/request/collect/
retrain proof, fixed holdouts, no promotion. Background training and saved
timing-only feedback refresh are tested. Zero wearer-trained models exist.

**Validation:** 116 focused Python tests; 29 Collect tests; 19 Review contract
tests; physical Collect build/install/launch. Native evidence:
`build/Pipeline-Collect.xcresult`, `build/Pipeline-Review.xcresult`.
Details and exact commands: [DEVICE_LOOP_2026-11-02.md](DEVICE_LOOP_2026-11-02.md).

**Next human action:** finish the baseline's bounded independent event review
in `workspace/loop/blind` (reuse the two actual phone-labeled chewing sections;
also check for events outside them). Do not confirm its clock from the separate
timing clip. Routine training takes use automatic proposals with assisted
correction; independent annotation is calibration/benchmark work, not a demand
to hand-label every future recording. The incomplete Quiet Sitting take is
preserved and excluded; replace it later with a complete take when requested.

**Next Codex action:** after that draft is saved verified/full-pass, verify that
automatic finalization evaluates Vision against the independent labels, inspect
actual misses/false positives/timing errors and the resulting focused requests,
and confirm Collect's overlays follow those definitions. Human checks and
remaining production gates stay enforced: ≥20 verified positive and ≥20 verified
hard-negative training takes with confirmed clocks, ≥6 independent evaluation
takes; research accuracy additionally needs ≥300 independent gold chew events.
Do not lower gates or create wearer labels to get a training demonstration.
The same service already owns processing, finalization, evaluation, experimental
training and request publication; no new startup command is needed.

Useful checks from the active repository:

```sh
tools/loop-service status
.venv/bin/chewsense-vision loop analyze
```

Expected: idle/no processing errors; the pending blind review and unchanged
training blockers until real reviews are complete. `analyze` refreshes the
catalog; it never bypasses readiness or promotes a model. Collect refreshes
status/catalog under Collect Required Data. No full Live/device model accuracy
or cross-person generalization has been established.

---

Historical October 26 evidence follows; superseded physical blockers above.

Updated October 26, 2026.

## Guided collection loop (current)

Implemented and software-tested; awaiting physical validation. See
[GUIDED_LOOP.md](GUIDED_LOOP.md).

Evidence from this run:

- Python: `pytest pipelines/vision/tests pipelines/motion/tests/test_temporal_chewnet.py`
  — 135 passed (88 existing + 47 loop tests), one existing CoreML warning.
- Collect: simulator build and 22 unit tests passed (12 existing + 10 guided);
  result bundle `build/Guided-Collect.xcresult`. The Swift-generated sidecar
  validates in Python (`test_swift_generated_sidecar_satisfies_mac_contract`).
- Review: macOS build passed; 16 existing contract tests passed. The Pipeline
  window was built, not exercised in a UI session.
- Local end-to-end run (scratch workspace, real service process): pairing over
  TLS with pinning, chunked upload with a resumed transfer, duplicate re-send
  ignored, automatic import → Vision (real MediaPipe) → review tasks. A Phase 0
  working copy (`video-20251021-225301`) yielded 5 chew and 1 food-entry
  proposals, matching Phase 0; its source bytes were unchanged. LM Studio
  (`google/gemma-4-12b-qat`) produced a validated advisory summary in ~70 s
  in the background. These used synthetic or legacy media; no guided recording
  from a device exists yet.

Next physical actions — developer:

1. `tools/loop-service install` on the Mac; allow incoming connections if asked.
2. Install Collect on the iPhone (scheme ChewSenseCollect, your team); allow
   camera, motion and local network.
3. Review → Window → Pipeline → choose `workspace/loop` → Pair iPhone; enter the
   code in Collect → Collect Required Data → Connect to your Mac.
4. Run **90-Second Baseline** with AirPods; take out one AirPod for ~5 s during
   "Sit still" once to check pause/resume and the recorded gap; Send to Mac.
5. Confirm the Pipeline window shows it processed, then do the blind review if
   it was assigned to evaluation, otherwise the assisted review.
6. Report: device/iOS/AirPods, any prompts that fired wrongly, upload time, and
   whether Pipeline showed the session. Then repeat until six evaluation
   sessions exist.


## Phase 0 state

Implementation prepares the existing Collect → Vision → Review → Motion path
for the first physical experiment. No model was trained/promoted. No original
recording or prior immutable annotation was changed. Existing icon assets and
`tools/verify` edits were preserved.

See [PHASE0.md](PHASE0.md) for changes, exact commands, recording protocol,
annotation workflow, evaluation and limitations.

## Completed automated evidence

- Python focused regression group: 88 passed, one existing CoreML naming warning.
- Collect: generic iOS build passed. Simulator unit suite: 12 passed, including
  ZIP/media/hash export and delayed-writer/edit-mapping tests. Existing tests
  expecting raw-CSV relabeling were updated to assert immutable motion and the
  separate coarse-label artifact.
- Review: 16 contract tests passed, including real confidence-number hash parity
  and clearing predictions/saving a draft on workspace changes. Native smoke
  opened a real candidate revision, advanced original-video playback with motion
  and visual signals, and displayed the timer/exclusion/sync controls. A separate
  blind working copy loaded zero proposals and hid model/motion traces.
- Native result bundles retained by topic: `build/Phase0-Collect.xcresult` and
  `build/Phase0-Review.xcresult`. These are software/simulator evidence only.
- Lint and `git diff --check` passed. Source video/motion/session hashes were
  checked again after native smoke and remained unchanged.
- Actual Swift resampler passed direct compilation/execution at four timestamp
  origins through 1e9 seconds and rejected clock resets.
- Four legacy paired videos produced candidates on checksum-verified working
  copies: 53 reviewable chew proposals and 3 food-entry proposals in total.
  All four structural audits passed and source hashes remained unchanged.
- Local diagnostic artifacts:
  `workspace/experiments/phase0-20261026/smoke/reports/source-inventory.json`
  and `workspace/experiments/phase0-20261026/smoke/reports/smoke-summary.json`.
  These are not human ground truth.

## Required next physical action — developer

1. Install Collect on the iPhone with the existing Xcode scheme and signing team.
2. Connect/wear AirPods; record/export a short marker preflight, then the
   90-second mixed activity protocol in PHASE0.md.
3. Return the exported bundle path, device/AirPods/OS versions, optional audio
   choice and any errors. No large logs are needed.
4. Preserve the received source ZIP/folder and prepare separate blind/assisted
   working copies. Independently annotate before viewing predictions.

Expected result: complete video/motion/session/coarse metadata and optional
review audio reopen on the Mac. Beginning/middle/end markers permit a measured
clock check. Do not confirm synchronization or train until evidence supports it.

## Next Codex action after that input

Audit hashes/display PTS/motion coverage; inspect corresponding marker traces,
record residuals and drift uncertainty, then help finish independent and
assisted reviews and run `evaluate-experiment`. If residuals exceed the pilot
budget, leave supervision masked and investigate the measured clock failure.
An affine correction requires an explicit tested mapping revision; do not edit
origins speculatively or stretch stream durations.

## Remaining validation

Physical start/stop, quick restart, reconnect, microphone route/offset and export
recipient behavior remain untested. No independently measured CV accuracy,
annotation time savings or physical synchronization accuracy is claimed. Full
Live and full application/UI suites were not run. This phase prepares a pilot,
not model training or promotion.
