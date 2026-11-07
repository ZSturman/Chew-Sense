# Autonomous continuation — November 6, 2026

Resumed at clean commit `3e8fb8c` on `claude/phase2-2026-11`; all requested
Claude Phase 2/privacy/catalog changes are already present. No merge needed.
Loop service PID 42269 is running, idle, with three completed uploads and
three outstanding review tasks. The physical iPhone 16 Pro Max is connected;
Collect is installed at 1.1 (6), Live at 1.0 (1). Device execution is pending.

Before changes, recorded source/blind/split SHA-256 hashes and copied non-media
state to ignored `workspace/checkpoints/resume-20261106/`. Original recordings,
verified revisions and locked splits must remain intact. Test consultations: 2.

Blind-review integrity: saved draft has zero added events, zero review seconds,
full-pass/verified/sync false, and empty blind proposals. Independence is still
available to a human reviewer. This continuation has read Phase 2 aggregate
results but has not inspected assisted event/proposal artifacts. Automated
event suggestions must not be inserted into this blind workspace or called
human truth. Finish/freeze independent review before assisted review.

Integrity fix checkpoint: the canonical eating-data loader now checks declared
motion hashes, confines media references to their session, isolates non-numeric
CSV failures, and rejects explicitly incomplete/unverified guided completion
even if human labels exist. Missing historical hashes remain eligible but are
reported as unverified integrity. Diagnostic motion replay/proposal generation
remains possible. Split reuse no longer rewrites the locked file's timestamp.

Actual consequence: the Phase 2 reference run included 46 negative development
windows from the incomplete Quiet Sitting take. Corrected discovery yields
31 eligible recordings (previously 32). Historical results and bundled models
are retained as historical, not revalidated. A new development-only run is
needed; no new test consultation is justified.

Physical checks: Collect 1.2 (7) built/installed/launched in place on the iPhone
16 Pro Max (iOS 27.2); Live also built and installed in place. Review rebuilt:
25 passed, 1 optional render skipped; actual UI confirms blind predictions stay
hidden and Chewing spans is disabled. Initial focused Python group: 83 passed;
affected integrity/runtime/assistance/status/end-to-end group: 36 passed before
the additional split no-rewrite regression. Local Network permission is waiting
on the wearer before pairing. No wearer activity was simulated.

An existing phone recording, `Not-eating-20261026-174428-C27C9380`, was copied
over USB to ignored `workspace/experiments/device-20261106/received/`. This is
25.255 s of actual prior capture with 1,249 motion samples and existing wearer
coarse not-chewing labels, not a new human activity or newly verified labels.
Its structural/media-hash audit passes and it is not already in the dataset.
Next: process this new-to-Mac recording through the real sandboxed loop and
inspect privacy and completeness evidence; retain the phone original.

Privacy guard hardening: reproduced a fail-open check under `EMFILE` (socket
resource exhaustion was misread as network isolation). The guard now requires
explicit `EPERM` from at least one family, skips only unsupported families,
and rejects inconclusive resource/permission errors. The permanent network/DNS
sandbox profile and no-override policy are unchanged. New targeted coverage
includes exhaustion, both-family unavailability and explicit sandbox denial.

Immediate work: validate service/catalog, protected hashes, sandbox tests and
focused pipeline tests; rebuild Review with blindness intact; build/install
Collect and Live in place on the physical iPhone, then inspect actual runtime
and hardware readiness. Only actual wearer activity and independent judgment
may unblock new ground truth. No new recordings, reviewed labels, retraining,
physical inference result or model promotion claimed at this checkpoint.

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
