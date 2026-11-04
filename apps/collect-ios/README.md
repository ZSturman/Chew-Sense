# ChewSense Collect

<!-- WORKSPACE-VERIFICATION:START -->
## Current state and getting started

Updated 2026-10-05. Component of **Chew Sense**. Collect records paired front-camera video and AirPods motion for canonical ChewSense session bundles.

### Startup state

| App | Platform | Result |
|---|---|---|
| Chew Sense Collect and Label | ios | opened |

These are startup observations; simulated flows and production acceptance remain unverified.

### Install and start

#### Chew Sense Collect and Label — ios

Purpose: Collect records paired front-camera video and AirPods motion for canonical ChewSense session bundles.

Requirements: Xcode and the deployment version declared in the Xcode target. Physical Apple devices need Developer Mode, trust/pairing, a signing team and the required capabilities.

Open the project:

```sh
open ChewSenseCollect.xcodeproj
```

Select scheme **ChewSenseCollect**, choose the connected iPhone/iPad, and choose Product → Run. Xcode builds, installs and opens the app; a successful device installation remains available for later testing.

### Chew Sense Collect and Label / ios screenshot

![Chew Sense Collect and Label startup](docs/images/startup-ea7617a3d657.png)

### Next steps

See [the parent project README](<../../../README.md>) for canonical Better Workspace task IDs and the matched Notion row.

- **Chew Sense Collect and Label / ios:** Turn this candidate into a fixture-backed simulated flow with explicit assertions and capture its result.

Detailed continuation: [next steps](<docs/NEXT_STEPS.md>). No archive or release operation was performed.
<!-- WORKSPACE-VERIFICATION:END -->

Collect records paired front-camera video and AirPods motion for canonical
ChewSense session bundles.

Each new session contains:

- `session.json` with shared-host-clock synchronization;
- video and motion CSV media references;
- frame rate, sensor rate, duration, app version, wearer, activity, and
  recording quality;
- `annotations/coarse_labels.json` after coarse chewing/not-chewing review.

Both AVFoundation and headphone motion use the same host-clock origin. New
sessions do not infer alignment from the first motion row.

Collect owns capture and coarse intervals only. Automatic mouth telemetry,
individual chew cycles, food-entry candidates, and final verification belong
to Vision and ChewSenseReview.

See [`../../docs/DATA_CONTRACT.md`](../../docs/DATA_CONTRACT.md).


## Phase 0 recording

Follow [the first-recording protocol](../../docs/PHASE0.md): connect AirPods,
record visible beginning/middle/end head-motion markers, capture chewing and
confounds, stop and wait for Saved, then complete coarse review and share the
session ZIP. Export verifies media hashes and includes nested annotations and
optional audio. Keep the received source immutable and review working copies.
Shared-clock metadata describes the intended mapping; actual camera/AirPods
alignment still requires physical marker evidence.

## Guided collection

**AirPods** in the library, manual camera and guided overlay opens the system
output picker, current audio output, fresh-motion status, **Play test sound**
and **Retry motion connection**. Use these to select/reconnect the AirPod
without opening a music app. Initial Bluetooth pairing is managed in system
settings. The motion stream now retries stale subscriptions automatically while
guided video continues; missing samples stay excluded. A first physical
interruption exposed a stalled stream. After the repair, the single-AirPod
removal/reinsertion test resumed automatically; its real gap and monotonic
timestamps were verified and its Mac processing completed. A deliberately
shortened diagnostic remains incomplete, as expected.

**Collect Required Data** (top of the Recordings list) shows experiments from
the Mac (or the bundled seed catalog offline): purpose, why it is needed,
duration, equipment, food, AirPods and camera needs, progress and priority.
Each run is one continuous recording with timed overlays, a countdown, quality
indicators and prompts; the instruction timer pauses when required evidence is
missing while recording continues. A `guided_session.json` sidecar stores the
actual timing (intended activity only, never labels). Pair once under **Mac
Connection**; **Send to Mac** or automatic upload transfers the verified ZIP
resumably after actual coarse review or an eligible explicit no-food attestation.
The completion screen offers **Review chewing intervals**; finish labelling
before sending. **What happens next** shows the next recording/review action,
received/complete/incomplete counts, verified positive/negative/evaluation/event
goals, expandable pipeline stages and exact training blockers. A recommended
recording opens its existing preparation and timed overlays directly. Counts
beside experiments mean recorded, not verified. The wearer confirmed the new
status and goals on the physical iPad. **Share Again** exports a ZIP; it does not update a prior service
upload. Manual Eating/Not Eating capture is unchanged. See
[GUIDED_LOOP.md](../../docs/GUIDED_LOOP.md).
