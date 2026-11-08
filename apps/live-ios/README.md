# ChewSense Live

Run short on-device checks to see what the chewing models detect from AirPods
motion. Use Collect for guided training and evaluation recordings.

1. Open **Connect AirPods**. Wear your AirPods, choose them as the audio output,
   and wait for **Motion is ready**. Use **Play test sound** to check the route
   and **Retry motion connection** if samples have stopped.
2. Open **New model check**, choose a model and tap **Start model check**.
   ChewNet drives the display by default and the experimental model logs its
   results alongside it. Reference video is optional and starts off.
3. Tap **Stop and save**. The result shows sample and prediction counts and an
   **Export check** action. Predictions stay unreviewed until compared with
   independently checked labels. Camera-free checks have unknown labels.

AirPods must already be paired with the iPhone to appear in the in-app picker.
See the [device validation](../../docs/DEVICE_PIPELINE_2026-11-06.md) for the
physical export and replay results.

From the repository root, `.venv/bin/chewsense-motion eating-parity --live-check
/path/to/FieldTest-folder` checks a saved export against the Mac reference and
returns a nonzero exit status if it finds discrepancies.

<!-- WORKSPACE-VERIFICATION:START -->
## Historical startup audit

Updated 2026-10-05. Component of **Chew Sense**. ChewSenseLive is the motion-only field-test app. It consumes the promoted CoreML model bundled with this app version, records the active model ID, and exports field-test bundles for later Vision and Review processing.

### Startup state

| App | Platform | Result |
|---|---|---|
| ChewSenseLive | ios | opened |
| ChewSenseLive | mac | build_failed |

These are startup observations; simulated flows and production acceptance remain unverified.

### Install and start

#### ChewSenseLive — ios

Purpose: ChewSenseLive is the motion-only field-test app. It consumes the promoted CoreML model bundled with this app version, records the active model ID, and exports field-test bundles for later Vision and Review processing.

Requirements: Xcode and the deployment version declared in the Xcode target. Physical Apple devices need Developer Mode, trust/pairing, a signing team and the required capabilities.

Open the project:

```sh
open ChewSenseLive.xcodeproj
```

Select scheme **ChewSenseLive**, choose the connected iPhone/iPad, and choose Product → Run. Xcode builds, installs and opens the app; a successful device installation remains available for later testing.

#### ChewSenseLive — mac

Purpose: ChewSenseLive is the motion-only field-test app. It consumes the promoted CoreML model bundled with this app version, records the active model ID, and exports field-test bundles for later Vision and Review processing.

Requirements: Xcode and the deployment version declared in the Xcode target. Physical Apple devices need Developer Mode, trust/pairing, a signing team and the required capabilities.

Open the project:

```sh
open ChewSenseLive.xcodeproj
```

Select scheme **ChewSenseLive**, choose My Mac, and choose Product → Run. Xcode builds, installs and opens the app; a successful device installation remains available for later testing.

### Next steps

See [the parent project README](<../../../README.md>) for canonical Better Workspace task IDs and the matched Notion row.

- **ChewSenseLive / ios:** Turn this candidate into a fixture-backed simulated flow with explicit assertions and capture its result.
- **ChewSenseLive / mac:** Resolve the recorded startup blocker and rerun the exact launch command before automating the candidate flow.

Detailed continuation: [next steps](<docs/NEXT_STEPS.md>). No archive or release operation was performed.
<!-- WORKSPACE-VERIFICATION:END -->

Deploy a passing registry model with:

```bash
chewsense-motion deploy
```

See [`../../docs/RUNBOOK.md`](../../docs/RUNBOOK.md).

## Where we are (Mac connection)

Live's first section, **Where we are**, shows the same project status as Collect
and Review: the current step, the next steps and which app each one happens
in. Tap **Connect to your Mac**, open Review → Pipeline → Pair iPhone on the
Mac, choose the Mac and enter the six-digit code. Live pairs as its own device,
uses the same pinned-certificate proof as Collect and only calls
`GET /v1/status`. It never uploads recordings. The first discovery asks for
Local Network access (`Config/Live-Info.plist` declares `_chewsense._tcp`).
The last received message stays visible offline and is marked as such.
