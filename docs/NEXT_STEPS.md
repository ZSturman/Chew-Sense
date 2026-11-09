# Checkpoint — November 9, 2026 (night): Pi mailbox deployed and validated on devices

- **Deployed:** Home Services `0.4.9-20261109T142058Z` (gateway only; built
  from the deployed 0.4.8 code plus the relay; `0.4.8` kept for rollback).
  `doctor` passed. Existing clients, workers and the Opportunity backend were
  unaffected.
- **Tokens:** `Chew Sense Mac` (`chewsense-mac`) and `Chew Sense iPhone`
  (`chewsense-phone`). Their secrets went straight from the Pi into
  `workspace/loop/service/mailbox-{mac,phone}.token` (0600, git-ignored). The
  deposit key is `loop/service/mailbox.key` (0600). The loop service was
  restarted in place (same identity); its collector is running.
- **Phones:** Collect and Live received the home server by themselves; both
  run the final builds.
- **Device validation** (real recordings resent as duplicates, nothing new
  imported): Pi fallback, interrupted deposit and direct transfers,
  collection on wake, cleanup, background retry. See the table at the end of
  [COMMUNICATION.md](COMMUNICATION.md).
- **Fixes found on the way:** a duplicate import no longer overwrites the
  session record's original `archive_sha256`/`received_from`. The collector
  removes its empty working folder after acknowledging. Cancelling a transfer
  (app closed) is not counted as a failure.
- **Network switching on the device** (guided with you): cellular with the Mac
  away → waits, nothing over cellular; Mac on the iPhone's hotspot → direct to
  its new address; back on Wi-Fi → direct. All passed; no Pi deposits.
- **Not run on the device:** recording while the phone is offline; iOS choosing
  to run the background task on its own.

---

# Checkpoint — November 9, 2026 (evening): Pi mailbox built and tested, not deployed

- **What it does:** when the Mac doesn't answer, Collect seals the recording
  (AES-256-GCM, key from the Mac) and deposits it in a new Home Services
  module. The Mac's loop service collects it when awake, verifies it, imports
  it through the normal path and acknowledges; the Pi then deletes its copy.
  Phones learn the mailbox from the Mac's pinned status (no setup screen), and
  show the Mac's last published status while it sleeps. Direct to the Mac
  stays first; recordings never use cellular for the mailbox. Design and
  evidence: [COMMUNICATION.md](COMMUNICATION.md).
- **Home Services:** branch `chewsense-mailbox` in the worktree
  `~/Workspace/Projects/_worktrees/home-services-chewsense-mailbox`, from
  `f308098`, **uncommitted**. Its patch applies cleanly onto the main working
  tree, which still carries another session's uncommitted work. The full
  suite passes. Regenerate `contracts/v1` after merging.
- **Mac:** `loop/mailbox.py`, `loop mailbox-configure`; `cryptography` added to
  the lock (only additions). The running service is unchanged until it is
  configured and restarted. Without configuration it tells phones there is no
  mailbox.
- **Tests:** HS full suite pass; Python loop 87 passed; Collect unit 43 passed
  (+4 opt-in); Live 42 passed (+1 device-only); `tools/loop-e2e` with
  `HOME_SERVICES_SRC=<worktree>/src` passed both tests. Covered: Mac awake →
  direct; Mac asleep → Pi; Pi outage mid-deposit → resume; neither reachable →
  wait; Mac sleeps mid-transfer → resume; wake → collect and import. Result:
  `build/LoopE2E-Collect.xcresult`.
- **Needs you:** approve the Pi deployment (gateway-only restart) and token
  creation; then `chewsense-vision loop mailbox-configure` and a service restart.

---

# Checkpoint — November 9, 2026: phone ↔ Mac communication redesigned

Decision record and rules: [COMMUNICATION.md](COMMUNICATION.md). In short: the
phone is offline-first, and the Mac is an optional worker reached by identity
(pinned certificate) over the routes it reports, never by a typed address.

- **Mac:** `loop/routes.py` reports routes (LAN address, `.local` name, tailnet
  MagicDNS name) to paired devices in the pairing reply and `/v1/status`. The
  service was restarted in place with your approval. Certificate, key, admin
  token, the three pairings, ports and catalog v13 were unchanged.
- **Phones:** one shared client, `apps/shared-capture/LoopLink.swift`, with the
  shared pairing form and Details view in `LoopPairingForm.swift`. Collect's
  `MacConnection` and Live's `LiveMacStatus` are thin wrappers, so Live now
  re-finds the Mac too.
- **Outbox:** sending is on by default. A Mac that is away never fails a
  recording (stays queued, offset kept). Nothing is sent during a recording.
  Recordings never cross cellular over the tailnet route. The app keeps sending
  briefly after it closes, and a background task (`com.chewsense.collect.sync`)
  retries. The Mac screen shows status and one action; addresses are under
  Details.
- **Tests:** Python loop 81 passed (route tests added). Collect unit 41 passed
  (+2 opt-in). Collect UI 11 passed (+2 device-only). Live 42 passed
  (+1 device-only). `tools/loop-e2e` passed: real Collect code against an
  isolated service with synthetic recordings (stale address, Mac unreachable,
  two queued recordings, processing, duplicate re-send). Results:
  `build/LoopE2E-Collect.xcresult`, `build/Connectivity-*.xcresult`.
- **iPhone:** Collect left the stale hotspot address and found the Mac on home
  Wi-Fi by itself; a real recording showed "Processed on the Mac". Live, which
  timed out this morning, fetched the status. See
  `build/Device-Routes-Collect.xcresult` and `build/Device-Status-Live.xcresult`.
  Development builds of both apps are installed.
- **Blocked:** the tailnet route. iOS ATS rejects the self-signed certificate
  on `100.x`/`*.ts.net` (`-9802`). An ATS exception was refused as weakening
  TLS and is not in the apps. Options are in COMMUNICATION.md. The
  recommendation is the Pi mailbox.
- **Pi mailbox:** approved for design and synthetic testing; designed in
  COMMUNICATION.md, not built. It waits for the Home Services working tree
  (another session's uncommitted router/auth/config edits). Deployment needs
  another confirmation.
- Not verified on the device: Mac asleep, iPhone offline, hotspot with the new
  build, interrupted device transfer, background task timing.
- Nothing committed.

---

# Checkpoint — November 9, 2026: Collect lighting and Mac reconnect

**Lighting.** Brightness is now measured on the detected face, not the whole
frame, so a dark background no longer counts against a lit face. Dim light
never pauses a guided run while the face is still detected. It is only a
blocking issue (`low_light` plus `face_missing`) when the face cannot be
found. Dim time is still reported as `low_light_fraction`. When a dim frame
shows no face, detection is retried on a brightened copy. The recorded video
is unchanged. The new **Screen light** (bulb button / preflight toggle) draws
a white frame and sets the display to full brightness while recording. The
front camera also enables the system low-light boost where supported. The
`min_brightness` catalog value (0.15) is unchanged but now applies to face
luma. Thresholds still need checking in real dim rooms.

**Mac not reachable.** The pairing saved a fixed IP, and the app never looked
again, so it failed whenever the Mac changed networks (on Nov 9 the Mac was on
the iPhone's Personal Hotspot, 172.20.10.13). Collect now searches over
Bonjour and the Mac's `.local` name when the saved address fails (at most
every 30 s). It switches addresses only when the Mac presents the pinned
certificate and accepts the token. The Mac Connection screen offers
**Find my Mac again**, a new-address field (no re-pairing), and fix-it steps.
Live has its own connection code and was not changed.

Validation: `GuidedCollectionTests` 21/21 on the iPhone 17 Pro simulator
(2 new lighting tests); development build installed on the iPhone. After
launch the iPhone reconnected on its own (`devices.json` last_seen moved from
02:28 to 11:14:47 UTC, no re-pairing). A dim-room recording still needs
confirming on the device.

---

# Note — November 9, 2026: why Vision misses chews at the start of a bout

Seen by the wearer in the 90-Second Baseline (Nov 2) while reviewing; diagnosed
from Vision's own `mouth_states.csv` / `mouth_cycles.json` (not the blind gold).
- The cycle detector cuts a chew only at a **closed** state (opening score ≤ 0.5 z
  and nearly still). Right after food goes in (24.2 s), chewing is fast and the jaw
  turns from closing straight into the next opening. Consecutive chews then merge.
- Examples: 26.14–27.97 s is read as one 1.83 s cycle and 29.90–31.87 s as one 1.97 s
  cycle. Both exceed `cycle.max_duration_s` (1.5 s) and are discarded, about three
  chews each. 24.0–26.1 s (food entering, hand at mouth) never reaches "closed".
  The face is visible throughout.
- **Not changed:** this recording is locked test, so tuning on it would invalidate
  the evaluation. Candidate fix, to pre-register and develop on development
  recordings with hand-marked chews: split over-long runs at closing→opening
  turning points instead of dropping them.

Review also gained an **Add food entry** button (B) in Watch and finish and a
"Food in" flash on the video; entries within 0.3 s of another are refused so the
draft stays valid.

---

# Checkpoint — November 9, 2026 (retired the mimed-chewing recording)

- **Retired** `Not-eating-20261026-174428-C27C9380` (mimed chewing, no food) at the wearer's
  request. The record is in [SPLIT_CHANGES.md](SPLIT_CHANGES.md). Nothing was deleted or modified:
  95 protected files (all loop annotations and drafts, the recording's files, `splits.json`,
  `index.json`, pairing/TLS files, session records) were byte-identical before and after.
  New: `chewsense-vision loop retire-recording` / `loop retired`; the loop, the eating experiment
  (rejects it with the reason, including byte-identical copies) and Review (watch-only "Kept for
  research") all honour `state/retired/`.
- Loop service restarted in place (`launchctl kickstart -k`, same plist, ports and certificate
  fingerprint; catalog still v12). Up next is now the 90-Second Baseline's suggested chews.
- Review: playback no longer shows "Pause" after the video ends; Play at the end restarts.
  Reinstalled in `/Applications` (Apple Development signed).
- Tests: Review 39 passed + 2 opt-in skips (`build/Review-Guided.xcresult`); Python loop + motion
  171 passed (new `test_loop_retirement.py`, eating-dataset retirement test).
- **Not verified on the iPhone:** the Mac is on the iPhone's Personal Hotspot (172.20.10.x) and
  Live's request to it timed out, so Live showed its cached status ("2 labelling tasks") in
  `build/Device-Retirement-Live.xcresult`. On a shared Wi-Fi network, rerun Live `DeviceStatusTests`
  and Collect `DeviceStatusUITests` with `TEST_RUNNER_CHEWSENSE_EXPECTED_HEADLINE` and the new
  optional `TEST_RUNNER_CHEWSENSE_EXPECTED_SUMMARY` from `workspace/loop/project_status.json`.
- Pre-existing, not changed: a race in `write_json_atomic` (fixed temp name) once logged a
  `status.json` traceback in `~/Library/Logs/ChewSense/loop.log`; the service survived it.

---

# Checkpoint — November 9, 2026 (guided Review)

- Review redesigned around one next action: **Up next** home (from `project_status.json`,
  locally finished tasks hidden), then per-recording steps with a guide panel
  (assisted: Check suggestions → Watch and finish → optional Chewing spans; blind: Mark
  chews → Finish). Autosave of the working draft (never sets verified), save on Up next /
  recording switch / quit; unsaved span decisions are saved before switching recordings
  (previously discarded). Old toolbar, Review Queue and Sections panels folded into the guide,
  More options and the ⋯ menu. Validation rules, finish blocker and file formats unchanged.
  Details and images: `apps/review-macos/README.md`.
- Tests: Review 37 passed + 2 opt-in snapshot skips (`build/Review-Guided.xcresult`); new tests
  cover step derivation and resume, blind/empty/required-spans/blocked recordings, autosave,
  names and Up next filtering. The snapshot render left all drafts byte-identical.
- Installed: Release, Apple Development signed, at `/Applications/Chew Sense Review.app` and
  launched (old instance had no unsaved changes: green save dot, nothing marked). 14 annotation
  files hash-identical before and after.
- Not verified by the agent: clicking through a real review (it would write ground truth);
  the user's own pass through the blind and assisted tasks is the remaining check.

---

# Checkpoint — November 8, 2026 (review simplification)

- Review: **Finish review** replaces the separate Full pass and Verify toggles. One
  confirmation ("you watched the whole recording and marked every chew you saw, or
  none") sets both flags the Mac requires and saves; the loop service then freezes it
  (`auto_finalize_verified_drafts: true`). The button explains what blocks it
  ("Accept, fix or reject the 3 suggested chews first"). Instructions above the video
  now say what to do in one line. Review 31/31 (+1 opt-in skip); reinstalled in
  `/Applications` and reopened on the blind Not-eating review (draft unchanged).
- TestFlight upload of Collect 1.2 (8) still fails after you signed in: "App Store
  Connect access for MF8J7AY2X3 is required" (also outside the shell sandbox). The
  signed-in account is not seen as having App Store Connect access for that team.

---

# Checkpoint — November 8, 2026 (close): cleanup and Collect 1.2 (8)

- Removed, with your approval, the four empty legacy iPhone apps after re-checking their
  bundle IDs and data (only data: a 3 KB idle log, backup verified byte-identical).
  On the phone now: Chew Sense Collect 1.2 (8), Chew Sense Live 1.0 (1), and Xcode's UI-test runner.
- Collect 1.2 (8) (`125b0df`): archived and signature-verified
  (`build/StoreRefresh/chew-ios-1.2-8/`, receipt included); development build installed on the
  iPhone; recordings still present. **TestFlight upload failed:** Xcode has no signed-in
  Apple account ("App Store Connect access for MF8J7AY2X3 is required"). TestFlight
  still serves 1.2 (7). Internal-group assignment waits on the upload.
- Device checks after reinstall (no other project using the phone): Live fetched the
  shared status with its existing pairing; Collect showed the card; Collect → experiment
  and Collect → Live links passed.
- Cleanup: removed six uncited simulator result bundles and a stale Oct 26 Collect build
  in Xcode's default build folder (~612 MB). Kept cited results, release archives, stable build folders,
  `ChewSense copy/` and `ChewSense Collection/`.

---

# Checkpoint — November 8, 2026 (late): one ecosystem — names, links, installs

Full inventory and how to move between apps: [APPS.md](APPS.md).

**Installed and verified:**
- `/Applications/Chew Sense Review.app`: Release build, Apple Development signed,
  `codesign --verify --deep --strict` OK. Launch Services now resolves the bundle ID
  there (build-folder copies unregistered, files kept). Settings carried over
  (same bundle ID and preferences). After quit and relaunch it reopened the saved workspace.
- iPhone: **Chew Sense Collect** 1.2 (7) and **Chew Sense Live** 1.0 (1), development builds
  of `b079810`+, installed in place; data, pairing and Keychain preserved (Live's
  device test fetched status with its existing pairing after reinstall).

**Navigation added:** shared `ChewSenseLink` (`chewsense-collect://experiment/<id>`,
`chewsense-live://status`, `chewsense-review://session/<id>?workspace=…`). The
shared card's steps gain open buttons in each app; Review gains a **Go** menu.
Links only navigate.

**Verified on the iPhone (`build/Device-Links-Collect.xcresult`, `build/Device-Status-Live.xcresult`):**
the Collect link opened the Talking v2 preparation sheet (nothing recording); the
Live link brought Chew Sense Live forward with iOS's "◀ Chew Sense Collect" return;
Live showed "Open in Chew Sense Collect" on record steps and "Connected to" the Mac;
Collect showed the shared card. **On the Mac:** links opened the baseline (assisted)
and Not-eating (blind: "predictions hidden", 0 candidates) recordings; 19 annotation
files hash-identical before and after. Suites: Review 30 (+1 opt-in skip), Collect 30,
Live 41 (+1 device-only skip) in the simulator.

**Not done / limits:** removing the four empty legacy iPhone apps
(`com.chewsense.archive.*`, `com.audit.howsmyeating…`) was blocked by the permission
system as an irreversible deletion; their 3 KB of data is backed up in
`workspace/backups/iphone-legacy-apps-20261108/`. "Companion app not installed" was
checked in code (buttons hidden unless `canOpenURL`), not by uninstalling Live.
No real recording or live model check was started.

---

# Checkpoint — November 8, 2026 (night): false-alarm decoder, one-nod protocols

**AirPods false alarms: decoder persistence (pre-registered, confirmed once).**
Development out-of-fold predictions of `eating-20261108-105237` (22 recordings,
17 human bouts): 4 of 7 false bouts were 1–2 s blips. Requiring 3 s of
persistence before a bout starts (`min_start_windows` 3 → 6) gave 17/17 bouts,
false bouts 7 → 3 (9.1 → 3.9 per negative hour), chewing-time recall .782 → .711,
median onset 4.1 → 5.6 s. The choice was committed before any test result
(`docs/preregistrations/decoder-persistence-2026-11-08.md`, SHA-256 `e9e1aeef…`).
It was then confirmed once on the saved locked-test predictions of the run behind Live's detector
(`eating-20261104-015911`; 9 recordings, 7 bouts, only 0.11 negative hours):
7/7 bouts both; false bouts 2 → 0; chewing-time recall .872 → .811; onset 2.1 → 3.6 s.
Locked-test consultations: 3 (recorded in `index.json`). Tool:
`chewsense-motion eating-decoder-study`. **Not deployed:** Live still uses 3
windows. Adopting 6 needs your approval and a new Live check.

**Video chew detector diagnosis (no tuning: all gold chews are locked test).**
Opening-peak precision is .97 (31/32); recall .69. 12 of 14 misses fall in the
first bout (Vision found 15 of 27), where the AirPods cadence count got 28.
Misses, not false proposals, limit it. A recall change needs development
recordings with hand-marked chews.

**One-nod protocols published (catalog v12).** 11 of 13 experiments had only
two nods, so their timing could never pass the three-marker check. Baseline,
Talking, Slow & Fast Chewing and Head Movement are now v2. They use Natural
Meal v2's prompt ("Nod down once, then slowly return upright"), and the three
that had two nods gain a midpoint nod. Every other activity is identical to v1;
v1 digests are unchanged and progress was kept (baseline still 1/3). Registry
backup: `workspace/backups/loop-experiments-20261108-pre-v2/`. The phone synced
catalog v12. Verified read-only from the iPhone's stored copy (`xcrun devicectl device copy from --domain-identifier zachary-sturman.ChewSense-DataCollectionAndLabeling --source "Library/Application Support/Guided/experiment-catalog.json"`): v12, all four v2 one-nod prompts. The device UI status test passed again; a UI scroll test for the catalog was dropped as flaky while another project's UI tests shared the phone.

**Smallest useful next batch (≈ 15 min of recording, one sitting):**
90-Second Baseline ×2 (completes 3/3; the loop's hash makes some evaluation,
some training), Talking ×1 and Head Movement ×1 (the two largest false-alarm
contexts), Slow & Fast Chewing ×1 (speed), Natural Meal v2 ×1 (realistic
meal). Expected yield after review: evaluation recordings 1 → about 3–4,
first development recordings with hand-checked chews, first labelled
talking/head-movement negatives. Full gates (6 evaluation, 300 gold chews,
20 + 20 verified recordings) need about 3–4 such sittings.

---

# Checkpoint — November 8, 2026 (evening): timing, existing data, device suites

**Timing (video ↔ AirPods), 90-Second Baseline.** Not misaligned, but not
formally confirmable from this take. The end nod is distinct: residual −8 ms.
The middle nod agrees (+84 ms) but its motion peak is too weak (prominence 1.9 < 4).
The start window holds about three nods. Matching all peak pairs fits two
offsets equally well (6 pairs at ≈0 s and 6 at ≈−0.75 s, one nod apart), so the
"ambiguous" verdict is correct and the 3-marker rule was left unchanged. Chewing
rhythm cross-correlation gives +0.04 s in bout 1; −0.6 s in bout 2 is one chew
period of slip. Video and motion share the iPhone host clock, and the Nov 2 Timing
Check on the same phone and app version was human-confirmed consistent.
Conclusion: no evidence of a timing error. The fix is in recording: exactly one
slow nod per prompt. Confirmation stays human (Review, during the per-chew check).

**Existing recordings cannot add labelled data.** Every rejected recording was
rechecked. Not-eating-20251212-181452 has an empty motion file. The two
video-2025 recovery files have 28 s / 11 s of motion, no activity labels and
unverifiable clocks. Eating-fieldtest-20260525 points outside its bundle and is
locked test anyway. Nothing was relabelled automatically. New development
chews come from 90-Second Baselines that the loop's fixed hash assigns to
training: your per-chew check there creates development truth. Evaluation
baselines get a blind review.

**No retraining this round.** The development data is unchanged since
`eating-20261108-105237` (dev F1 .8683, AUC-PR .9444, 7 false bouts; locked test
consultations still 2). Chew-count changes (e.g. trimming span padding, counting
cycles instead of cadence × duration) need development recordings with
hand-marked chews; every verified chew so far is in the locked test recording,
so nothing was tuned on it.

**Physical iPhone 16 Pro Max suites:** Live 42/42 (`build/Device-Live.xcresult`),
Collect 31/31 (`build/Device-Collect.xcresult`). Fixed: two Collect tests failed
only on device (`/private/var` vs `/var` path comparison). One returned before
deleting its temporary folders from Collect's real Documents. Both now always
clean up; only the two real recordings remain on the device (listed with
`devicectl`).

**Your tasks, one at a time:**
1. Mac, Review: switch to the blind workspace (`workspace/loop/blind`). Open
   Not-eating-20261026, play its 25 s, then press **Full pass**, **Verify**, **Save**.
2. iPhone, Collect: **Start 90-Second Baseline**. At each nod prompt, make
   exactly one slow nod down and up, then stay still. Send it to the Mac.
3. Then follow the shared card. It names whether that take needs a blind
   review (evaluation) or a per-chew check (training).

---

# Checkpoint — November 8, 2026 (afternoon): physical-device verification, chew-count evidence

**Verified on the physical iPhone 16 Pro Max (iOS 27.2):**
- Live pairs as its own status-only device (paired 17:55 UTC). The in-app device
  test `ChewSenseLiveTests/DeviceStatusTests` fetched `/v1/status` over the pinned
  certificate with Live's real pairing; the headline matched
  `loop/project_status.json` (`build/Device-Status-Live.xcresult`).
- Collect, launched normally (not fixture mode), opened Collect Required Data
  and showed the same card, steps and answers with its own steps marked
  "This app" (UI test `DeviceStatusUITests`, `build/Device-Status-Collect.xcresult`,
  screenshot attachment). It navigates and reads only; nothing was recorded.
- Review (rebuilt, relaunched) shows the same card. All three now report the
  same "updated" time: the service sends its stored copy instead of stamping
  every request.

**Fixed:** the span-review queue had dropped label-less new uploads (caught by
`test_upload_to_retraining_with_assisted_labels`); only incomplete guided takes
and diagnostic recordings are skipped now.

**New measurement — AirPods-only chew counting against hand-marked chews.**
`chewsense-motion chew-count-eval` (reads no model output, fits nothing) scores
the fixed cadence × duration method inside human chewing spans against the 45
frozen blind chews of the 90-Second Baseline (locked test recording; behaviour
measure only): cadence 1.465 vs 1.429 Hz (+2.5%) and 1.562 vs 1.550 Hz (+0.8%);
count 28 vs 27 and 22 vs 18, total 50 vs 45 (+11%). The excess comes from span
padding after the last chew. One wearer, one recording, one pace, human span
boundaries: pilot evidence, not validation.
`workspace/experiments/behavior/chew-count-latest.json`.

**Automation evidence.** `pipeline-status` now records how people treat
automatic span proposals: 7/7 agreements (84 of 94.5 s) accepted unchanged;
6 flagged clips (10.5 s) took 31.9 s active. One recording only, so agreements
still require confirmation.

**Labelling that stays with the wearer (not done by the agent, on purpose):**
the blind review of Not eating (Oct 26) and the per-chew check of the
90-Second Baseline are human ground truth and human-assisted measurements.
An agent-written review would be model output stored as verified truth. The
blind workspace was audited: 0 candidates, untouched draft (0 s active).

---

# Checkpoint — November 8, 2026: assisted span review saved; one shared status in every app

**Assisted chewing-span review of the 90-Second Baseline (Nov 2) is saved and audited.**
`loop/assisted/sessions/raw/Guided-baseline-90s-20261102-171334-F83AB32C/annotations/coarse-review-v0001.json`
(saved 10:36 UTC) cites proposal revision `coarse-proposals-v0002.json` by its
content hash (`2a36e45f…`, matches). All 13 spans are decided: 7 agreements
accepted in one batch, 6 flagged clips labelled individually, no automatic
proposal overturned. Active time **31.9 s** for a 94.5 s recording (the
earlier blind pass was untimed, so no saving is claimed). Independent check:
all 45 frozen blind chew cycles fall inside the 33.0 s confirmed as chewing
(27 in 24.75–43.75 s, 18 in 61.75–75.75 s). The recording is in the locked
test split, so this refines evaluation labels and adds no training data. The
frozen blind review is unchanged.

**Where the process is (answers to the three questions):**
- *Can a model be tested?* Yes, as an experiment. Live already carries the
  exported AirPods chewing-span detector (`eating-hgb-20261104-015911-7a28dd15`,
  development F1 0.87, one wearer, shadow mode). Its accuracy in real use is
  unmeasured; no labelled Live check exists yet.
- *Is visual chew detection working?* Partly. Vision runs automatically on every
  recording. Against the 45 hand-marked chews it found 28 (F1 0.73 at ±250 ms;
  target 0.85). It is a labelling assistant, not a chew counter.
- *More data?* Yes. Loop gates before the next model: verified chewing
  recordings 0/20, verified talking/walking/drinking/head-movement recordings
  0/20, blind-reviewed test recordings 1/6, hand-marked chews 45/300.

**One message in Collect, Review and Live.** New
`pipelines/vision/chewsense/loop/project_status.py` derives a single status
from existing loop files: phase (Collect → Label → Train → Test), numbered next
steps tagged with the app they happen in, and the three answers above. It never
analyzes, trains or publishes a catalog.
- Service: `GET /v1/status` adds `pipeline.project`. The legacy `next_action`
  line and `next_experiment_id` are now taken from it, so even the installed
  Collect 1.2 (7) shows the aligned message after a service restart. The
  periodic tick rewrites `workspace/loop/project_status.json` when it changes.
  `chewsense-vision loop project-status` writes it without the service.
- Collect: the shared card leads **What happens next**; older details moved
  under **Details**. PinnedTrust/Keychain moved unchanged to
  `apps/shared-capture/LoopServiceTrust.swift`.
- Review: sidebar line, empty main window, and the top of Pipeline (⇧⌘P) and
  Training (⇧⌘T). It compiles the same `apps/shared-capture/ProjectStatus.swift`.
- Live: new **Where we are** section with a status-only Mac pairing (same
  code/certificate proofs as Collect; Bonjour in `Config/Live-Info.plist`, Local
  Network description added). The last message stays visible offline.

Current message (written to `loop/project_status.json`): **Label recordings in
Review** — 1. Blind review: Not eating (Oct 26) · 2. Check suggested chews:
90-Second Baseline (Nov 2) · 3. Record 90-Second Baseline (1 of 3) · 4. Record
Natural Meal (0 of 3) · optional: try the experimental model in Live.

**Training-status fixes.** `pipeline-status` no longer asks for a development
re-run when only locked-test labels changed: runs now store per-recording
fingerprints (`sessions_overview[].fingerprint`) and status reports
`development_current` / `development_changed_recordings`. Incomplete or
diagnostic takes are no longer queued for span review.
Dev-only run `eating-20261108-105237` (fingerprints recorded) reproduced HGB development F1 .8683, AUC-PR .9444, 7 false bouts; locked test untouched (consultations still 2; `splits.json` and `latest.json` hashes unchanged). `pipeline-status` now reports trained on current data.

**Verification.** Python: 67 loop tests, 8 project-status tests and 5 status
tests passed. Swift: Review 8/8 focused tests plus the opt-in snapshot render
(`build/ProjectStatus-Review.xcresult`, `build/ProjectStatus-ReviewSnapshot.xcresult`);
Collect 19/19 `GuidedCollectionTests` (`build/ProjectStatus-Collect.xcresult`);
Live 12/12 focused tests (`build/ProjectStatus-Live.xcresult`). Simulator and
macOS only. No physical-device run, Live pairing or service restart was done.

**Done with your approval (Nov 8, ~11:00 UTC):** loop service restarted (same
port 48765 and certificate `8da810cf…`; catalog still v11; no analysis ran) and it
now rewrites `loop/project_status.json`. Review was quit normally, rebuilt with the
README command and relaunched; the card shows on the main window. Collect and
Live development builds were installed on the iPhone 16 Pro Max, replacing
TestFlight Collect 1.2 (7). Still open: pair Live once (Live → Where we are →
Connect to your Mac, with a code from Review → Pipeline → Pair iPhone) and confirm
the card on both phones. No on-device check of the card has been done yet.

**Your next steps (same as the apps):** the two short Review tasks above, then
record the 90-Second Baseline and a complete three-nod Natural Meal v2, then
hard negatives (talking, drinking, head movement, gum, talking while eating).

---

# Autonomous checkpoint — November 7, 2026

Details and reproduction: [device/pipeline report](DEVICE_PIPELINE_2026-11-06.md).
Continues `claude/phase2-2026-11`; Claude's `3e8fb8c` checkpoint was already
present. Source/blind/non-media state was checkpointed before edits under
ignored `workspace/checkpoints/resume-20261106/`.

**Blind baseline completed on November 7.** The wearer confirmed a full review
of the 94.477 s clip, with 45 hand-marked chew cycles and zero visible proposals.
Saved and froze verified `annotations/review-v0001.json` in the blind workspace.
The post-freeze audit passed. Only one timing marker was usable, so all 4,699
motion rows remain masked for supervision. The blind timer was not used.

**Offline Vision comparison.** At ±250 ms, the stored Vision candidate revision
matched 28 of 45 closure anchors (4 extra, 17 missed, precision .875, recall .622,
F1 .727). F1 at ±100/50 ms was .364/.104. All 45 human cycles retain the Add chew
default ±210 ms around the marked opening. On the marked opening timestamps,
Vision matched 30 at ±100 ms (2 extra, 15 missed, F1 .779, mean matched error 45 ms).
The first chewing section accounted for 12 of the 17 closure misses. These are
one recording's offline video-annotation results. Neither the AirPods model nor
the locked motion test was rerun. Evidence is in
`workspace/experiments/independent-baseline-20261107/`.

**Assisted baseline staged.** The 45 frozen labels are separate from the assisted
copy. The wearer found the original panel confusing. Its first attempt showed
25 s timed and zero decisions. The new flow shows one of six flagged clips
at a time (10.5 s total), then offers the seven suggested labels together or
one at a time. **Play clip** starts timing and plays only that clip. The timer
pauses on save, panel close or app switch. The old trial time is recorded
separately. Twenty-five focused native tests passed. No assisted human decisions
or time saving are claimed until the wearer saves a revision.
Quiet Sitting has zero Vision chew proposals and 71.1% motion coverage with one
11.5 s gap. Its two no-motion review spans (17.25 s) stay diagnostic, and a full
new take is higher value than labelling the failed one.

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

**Playback changes used for the blind baseline:** the wearer reported unreliable frame stepping. Review
now uses the video's actual frames, keeps keyboard focus available to controls,
and applies the selection loop only during playback. Rebuilt and reopened the
blind baseline with predictions hidden. Verified Right and Previous frame on
the actual baseline and a 25 fps regression fixture. At restart the saved draft
had 0 events, 0 timer seconds and no full pass, verification or sync confirmation.
After a report of flashing during scrubbing, Review switched to one persistent
video layer. The 25 focused macOS tests passed, including checks that stepping
retains the video layer and its bounds. On the actual blind recording, two Right
presses and two Left presses moved by one frame each and returned to 25.973 s.
The draft hash was unchanged and Review was left paused there.
The wearer used the phone-labelled sections and confirmed the full-video pass.

**Next Codex work:** audit the saved assisted revision for proposal hash, decisions
and elapsed time. Record time for the assisted pass alone because the blind pass
was untimed. Collect a complete three-marker Natural Meal
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
