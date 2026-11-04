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
