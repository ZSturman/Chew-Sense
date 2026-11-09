# Guided collection and automated learning loop

Updated November 2, 2026. **Status: software-tested, including an isolated
synthetic training/validation/test/registry handoff. First physical iPad/AirPods
baseline captured, paired and transferred. Independent event review, physical
timing and recognition accuracy remain pending. No wearer-trained model has
been promoted.** See [the physical test record](DEVICE_LOOP_2026-11-02.md).

The loop reuses Collect → Vision → Review → Motion. It adds data-driven guided
experiments, a Mac service that receives and processes recordings, review
prioritization, independent evaluation, weakness analysis and a collection
planner that publishes new requests back to the iPhone.

```text
Collect (iPhone)                       Mac: chewsense-vision loop serve
  Collect Required Data  ── catalog ──  experiment registry + planner
  guided recording + quality checks
  guided_session.json sidecar
  Send to Mac (resumable, verified) ──▶ import (immutable source, receipt)
                                        timing audit → guided check → role
                                        assisted / blind working copies
                                        Vision (run_on_sessions.py)
                                        sync-marker assessment, review tasks
Review (Mac) ◀── Pipeline window ────── status, tasks, metrics, weaknesses
  blind review, assisted review ──────▶ auto-finalize verified drafts
                                        evaluate (blind gold only) → weaknesses
                                        → plan → catalog vN+1 → iPhone
                                        training gates (experimental only)
```

## Run it

One-time on the Mac:

```sh
cd '/Users/zacharysturman/Workspace/Projects/Chew Sense/ChewSense'
uv sync --locked
tools/loop-service install     # LaunchAgent: starts now and at every login
```

`tools/loop-service start` runs it in the foreground instead; `uninstall`
removes the agent. Logs: `~/Library/Logs/ChewSense/loop.log`. Data lives in
`workspace/loop/` (or `$CHEWSENSE_WORKSPACE/loop`), ignored by Git. The first
start creates a self-signed TLS certificate, an admin token and the seed
experiments. The device API listens on TCP 48765 (LAN, TLS); the admin API on
127.0.0.1:48766 only. Ports are in `pipelines/vision/configs/loop.yaml`; macOS
may ask once to allow incoming connections for Python.

In ChewSense Review choose **Window → Pipeline** (⌘⇧P) and select the
`workspace/loop` folder once. It shows processing, pause/resume/stop, review
tasks, required recordings, performance, weaknesses and training readiness.

Pair the iPhone: **Pipeline → Pair iPhone…** shows a six-digit code (or run
`tools/loop-service pair`). In Collect: **Collect Required Data → Connect to
your Mac**, pick the Mac (Bonjour) or type its IP address, enter the code.
Turn on **Send recordings automatically** if wanted.

Collect's **What happens next** panel shows the exact next action, received /
complete / incomplete counts, verified-data goals and expandable progress for
receive → timing → review → train → test → repeat. A recommended recording links
directly to its preparation and existing timed overlays. Mac review actions are
identified explicitly. Experiment counts mean recorded, not training-ready.
The panel also shows training blockers and separate Vision/AirPods-model readiness. Guided uploads
wait until you review the actual chewing intervals (or explicitly attest that
nothing was eaten/chewed in an eligible food-free experiment). Use **Review
chewing intervals**, finish labelling, then **Send to Mac**. Automatic transfer
starts after that review when enabled. **Share Again** is a separate ZIP export;
it does not amend a previously received service upload.

## Experiment definitions (`guided-experiment/1`)

Seeds: `pipelines/vision/configs/experiments/*.json` (90-Second Baseline, Quiet
Sitting, Walking Without Eating, Crunchy Food, Soft Food, Drinking, Talking,
Head Movement). Schema and validation: `pipelines/vision/chewsense/loop/experiments.py`.
A definition carries identity/version, purpose and request reason, priority,
required/optional, target recordings, preparation, equipment, food/beverage,
AirPods and camera requirements, safety notes, lead-in, ordered activities
(kind, instruction, duration, expected behavior, *intended* label, face/motion
requirements, quality policy `pause|mark|repeat`, minimum valid fraction,
sync-marker flag), completion thresholds, labeling requirements, evaluation
purpose and tags. Validation enforces 2–180 s activities, ≤600 s total, food
for chew/drink, safety notes and no face requirement for walking, sync markers
never intend chewing, and a text safety screen.

Versions are immutable files `loop/experiments/definitions/<id>/vNNNN.json`;
`state.json` records publish/retire history. Manage them without an app release:

```sh
.venv/bin/chewsense-vision loop experiments validate my-experiment.json
.venv/bin/chewsense-vision loop experiments add my-experiment.json --publish
.venv/bin/chewsense-vision loop experiments retire talking --reason "enough data"
```

The catalog (`loop/experiments/catalog.json`) gains a new `catalog_version`
only when its content changes. Collect caches the last valid catalog in
Application Support and falls back to the seed catalog bundled in the app
(`ChewSenseCollect/Resources/SeedExperimentCatalog.json`, regenerated with
`loop experiments export-seed-catalog`; a test keeps it identical to the seeds).
An invalid catalog never replaces a good cache. A run in progress keeps the
definition it started with; each recording stores that exact definition.

## Recording timing semantics

One continuous capture per experiment (video and motion keep recording; manual
Eating/Not Eating capture is unchanged and still splits on motion loss). The
instruction engine (`Guided/GuidedRunEngine.swift`) runs on the same host
clock as the media and logs: start acknowledgement, lead-in, instruction
presented, activity started (only once its required evidence is present),
paused/resumed, restarted, marked uncertain, completed, quality failed/restored
(back-dated to the first bad sample), AirPods lost/restored, capture
interruption/resume, completion or abandonment.

Three separate operations: **pausing the instruction timer** (quality loss with
policy `pause`; recording continues), **pausing media** (never done by guided
mode) and **stopping capture** (only at the end). Policy `mark` keeps counting
and records an uncertain interval (used for walking); `repeat` restarts the
activity (used for nods). A pause longer than 15 s restarts the activity.
Restored quality never retroactively validates the interrupted span.

`guided_session.json` (next to `session.json`) contains the verbatim
definition and its SHA-256, events, per-attempt actual presented/start/end
times, paused and valid seconds, invalid intervals, quality fractions, on-device
validation and `labels_status: intended_only_not_verified`. Scheduled activities
are **intended activity only**: they are never written into coarse labels,
reviews or evaluation truth. The Mac verifies the definition against its
registry and the clock origin against `session.json`.

End-of-run validation checks every activity was completed with enough
observable time, sync nods, face visibility and AirPods coverage, explains each
failure, keeps the recording, and offers **Repeat only the affected segments**
(a short linked recording bracketed by the first and last sync nods, with
`repeat_of_session_id`). For food-free experiments the wearer may confirm
"nothing eaten or chewed"; that is stored as a human coarse decision with
`basis: wearer_no_food_attestation`, distinct from the schedule.

On-device quality checks (`Guided/QualityMonitor.swift`): Vision face
rectangles at ~4 Hz on one frame at a time (frames are skipped while busy),
jaw margin below the face box, sparse luma brightness, phone movement from
CMMotionManager and AirPods sample freshness. Thresholds are heuristics that
need device tuning.

## iPhone ↔ Mac connection

* Discovery: Bonjour `_chewsense._tcp` advertised by `dns-sd`; manual address fallback.
* Transport: TLS 1.2+, self-signed certificate; the iPhone pins its SHA-256.
* Pairing: code shown on the Mac, valid 10 minutes, 5 attempts. The phone sends
  `HMAC(code, "pair|device|fingerprint-it-saw")`; the Mac verifies with its own
  fingerprint (a relay with another certificate fails) and returns a token plus
  its own proof. The token lives in the iPhone Keychain; the Mac stores only its
  SHA-256 and can revoke devices. No endpoint except hello/pair works unpaired.
  The service is for the local network; do not port-forward it.
* Upload: the same hash-verified ZIP as Share. `POST /v1/uploads` announces
  session ID, SHA-256 and size; the Mac returns the offset it holds; `PUT`
  chunks append at that offset (a wrong offset gets 409 with the true offset);
  `complete` verifies size and SHA-256. The same archive never creates a second
  job. The upload queue survives app restarts; transfers run while Collect is open.

AirDrop fallback: `.venv/bin/chewsense-vision loop import <zip>` queues a ZIP
exactly like an upload.

## Automatic processing

Per session, persisted idempotent steps (`loop/processing.py`): import → timing
audit → guided check → role → working copies → Vision → sync-marker assessment
→ review tasks. Import rejects corrupt ZIPs, traversal paths, symlinks,
encrypted members, size bombs, multiple sessions and media hash mismatches. The
ZIP is retained read-only under `loop/archives/`, the session becomes a
read-only source under `loop/sources/`, with a receipt. Identical re-imports are
duplicates; the same session ID with different content is quarantined, never
overwritten.

Roles are assigned once, deterministically from the session ID (50% evaluation
until 6 evaluation sessions exist, then 34%), persisted, and inherited by repeat
recordings. Legacy imports are diagnostic only. Every session gets an assisted
copy (with the guided sidecar); evaluation sessions also get a blind copy with
no predictions and no sidecar.

**Pause** finishes the running step then waits; **Stop** terminates Vision,
discards its partial telemetry, keeps the job queued at that step and waits;
**Resume** continues. After a crash, running jobs return to the queue. Failures
become “needs attention” with the error and a Retry button.

## Review with less manual labeling

Review tasks highlight: instructed chewing with no proposals (possible misses),
proposals during instructed talking/drinking/walking/head movement (possible
false positives), low-confidence proposals, quality-loss spans (candidate
unobservable ranges) and sync nods (clock inspection). The automatic marker
check compares visual head angular-speed peaks with AirPods gyro peaks in each
nod window; it reports `consistent`, `exceeds_budget` or `inconclusive` and never
sets `sync_verified` — the reviewer still confirms the clock in Review.
The checker consumes radians and all three visual pose axes, matching the
three-axis gyro magnitude; omitting roll previously selected the opposite nod
phase. Competing substantial peaks make correspondence inconclusive rather
than proving a clock defect. Assessments carry a version. A dynamic
**30-Second Timing Check** is requested only when evidence is insufficient;
once a complete check arrives, inspect it before asking for another. It is a
diagnostic role, never training/evaluation data. Saving its clock confirmation
refreshes the feedback even without event finalization.
Clicking a span opens the right workspace and seeks there.

Blind evaluation sessions appear first; their assisted review is locked until
the blind review is frozen. Drafts marked verified with a full video pass are
finalized automatically with the existing `finalize_session` (new immutable
revisions; nothing overwritten).

## Dataset eligibility

| Use | Requirement |
| --- | --- |
| Motion training | training role, not legacy, complete registered guided take, assisted review finalized, human-confirmed clock (`sync_verified`) |
| Evaluation truth | evaluation role, complete registered guided take, blind full-video review frozen with only manually added events and an empty proposal artifact |
| Never | instructed schedule, unreviewed predictions/proposals, unknown/unobservable spans, unconfirmed clocks, incomplete takes or timing-only diagnostics |

A session is never both; repeat recordings follow their parent.

## Evaluation methodology

When frozen blind reviews exist, `evaluate_experiment` (one-to-one matching at
±250/100/50 ms on observable spans) runs automatically; reports are immutable
and keyed to the exact input hashes. Aggregates: precision, recall, F1, false
positives/negatives, per-session F1 and count error, timing MAE/p95, results by
instructed activity, food category and sensor gaps, blind-vs-assisted human
agreement, and active correction time vs independent labeling time. These are
Vision-teacher metrics; motion-model metrics come from the existing motion
evaluation once a model exists.

Research-stage acceptance targets (`configs/loop.yaml`, from the October 25
research): chew F1 ≥0.85 at ±250 ms, per-session count error ≤10%, timing MAE
≤67 ms and p95 ≤150 ms, sync median ≤50 ms, labeling time saved ≥50%. They show
“insufficient evidence” until ≥6 evaluation sessions and ≥300 gold events.
Motion promotion keeps the existing gates in `pipelines/motion/pipeline/promotion.py`.
Production requirements are not defined.

## Weaknesses, planning and stopping

Weaknesses are classified as insufficient evaluation data, insufficient training
data, annotation quality (blind/assisted disagreement), synchronization defects,
model limitations (activity-specific false positives, food-specific misses,
training plateau) and recording quality (repeated on-device check failures,
unregistered definitions). Only data-shaped weaknesses produce collection
requests; synchronization and quality problems ask for fixes instead.

The planner attaches requests (reason, priority) to existing experiments, adds
a bounded new version (+3 recordings, ≤5 versions) when an experiment's target
is met but the weakness remains, or templates a validated confound experiment
for an activity with none (≤6 active generated). Auto-publish is on by default
(`planner.auto_publish_generated`). Equivalent requests are not duplicated.
When research targets are met, only evaluation refresh is requested.

## Training (Phase E)

Gates (`loop/training.py`): ≥20 verified positive and ≥20 verified hard-negative
training sessions, all with confirmed clocks, ≥6 independent evaluation
sessions, disjoint partitions, ≥5 new sessions since the last run, ≤2 runs per 7
days, and no plateau (3 runs with <0.01 F1 gain). Only then is an experimental
run requested: a subprocess materializes a new dataset from eligible training
sessions, creates chronological splits with holdout membership retained across
runs, trains, evaluates both locked validation and test splits and registers
into an isolated registry (`loop/training/workspace`). It
never deploys or promotes; the promotion assessment is reported for an explicit
decision. This path has been exercised only on synthetic fixtures; eligible
wearer data does not yet exist. Activity/food-specific locked-test failures
generate focused collection requests. Vision success alone cannot stop the loop
while the motion model fails its gates. Failed training attempts count against
the weekly limit. External experiment logging is disabled for this local loop.
Training runs in a background worker: uploads and processing remain available,
an active-run gate prevents overlapping jobs, and completion triggers fresh
analysis and catalog publication. Service shutdown terminates the training
process group; interrupted attempts are marked failed on restart with outputs
preserved. Failed/missing results never become completed runs. A fixture test
executes upload → process → verified fixture review → actual training → locked
testing → error-derived request → next upload/review → second training run,
and checks unchanged holdout membership and no automatic promotion.

## LM Studio (optional)

If LM Studio is serving at `http://127.0.0.1:1234/v1`, the service asks the
first loaded model, in the background, to explain the deterministic weaknesses
in plain language. Output must match a JSON schema, reference known weakness
IDs, contain no number absent from the evidence and no readiness claims; it is
cached against the exact evidence hash and labeled advisory. Metrics, plans and
publication never depend on it. Disable with `lmstudio.enabled: false`.

## Failure recovery

| Problem | Behavior |
| --- | --- |
| Upload interrupted | resumes from the Mac's offset; checksum mismatch restarts it |
| Same upload again | reported received; no second import |
| Different content, same session ID | quarantined in `loop/quarantine/` |
| Service crash/restart | running step re-queued; outputs are idempotent |
| Vision failure | needs attention; Retry in Pipeline |
| Mac offline | Collect records with the cached catalog; queue sends later |
| Bad catalog | ignored; last good catalog kept |

## Limitations and required physical validation

* Not yet run on a physical iPhone: AirPods dropout/reconnect during continuous
  guided capture (headphone updates are re-registered on reconnection),
  Vision face/jaw/lighting thresholds, haptic/sound cues, Bonjour + local
  network permission, TLS pinning, and large uploads over Wi-Fi.
* Uploads run in the foreground; keep Collect open while sending.
* The sync-marker check and review prioritization are untested on real nods.
* Single wearer: cross-person generalization cannot be assessed.
* No independent accuracy, time savings or synchronization accuracy is claimed.

## Retiring a recording

A recording that can't serve as ground truth (for example, mimed chewing filed as
"not eating") is retired, not deleted:
`chewsense-vision loop retire-recording <id> --category mimed_chewing_no_food --reason "…" --evidence "…"`.
It leaves the review queues, loop evaluation and training, and the eating
experiment, and its files stay byte-identical. `chewsense-vision loop retired`
re-checks them. Restart the service afterwards so it uses the change. The log
of retirements and their effect on the locked split is [SPLIT_CHANGES.md](SPLIT_CHANGES.md).

