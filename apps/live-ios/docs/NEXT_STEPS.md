# Component next steps

<!-- WORKSPACE-VERIFICATION:START -->
## Component verification continuation

Updated 2026-10-05. Install/start commands and actual screenshots are in [README](../README.md).

## ChewSenseLive / ios

Startup: **opened**. Startup evidence only; candidate flows not run. Production acceptance is not established.

Candidate flow (not run): Connect compatible AirPods, personally grant motion access, inspect configuration and start/stop a short live detection session. Verify model status and motion updates, and report absent sensor input rather than interpreting it as detector success.

Fixture setup: Synthetic data and isolated storage. Use mock/emulator auth, API, mail and paid-provider endpoints. No live automation, deletion of user data, or external submissions. Separate simulator/test store; physical sensors, HealthKit, camera, Pencil and cloud/device behavior need a later human/device check.

Codex: Turn this candidate into a fixture-backed simulated flow with explicit assertions and capture its result.

User: Open ChewSenseLive on your iPhone 16 Pro Max, review its startup screenshot, and choose its intended product role. Use the documented device/permission flows after isolated simulated checks; record any crash or missing data.

## ChewSenseLive / mac

Startup: **build_failed**. Startup evidence only; candidate flows not run. Production acceptance is not established.

Candidate flow (not run): Connect compatible AirPods, personally grant motion access, inspect configuration and start/stop a short live detection session. Verify model status and motion updates, and report absent sensor input rather than interpreting it as detector success.

Fixture setup: Synthetic data and isolated storage. Use mock/emulator auth, API, mail and paid-provider endpoints. No live automation, deletion of user data, or external submissions.

Codex: Resolve the recorded startup blocker and rerun the exact launch command before automating the candidate flow.

User: Review the app purpose and screenshot; decide whether to ship it, retain it as an experiment, consolidate it, or nominate it for archival. Supply required account/device judgment after automated fixtures pass.

Blockers:

- /Users/zacharysturman/Workspace/Projects/Chew Sense/ChewSense/apps/live-ios/ChewSenseLive/Views/CameraPreviewView.swift:10:8: error: Unable to resolve module dependency: 'UIKit' (in target 'ChewSenseLive' from project 'ChewSenseLive')

<!-- WORKSPACE-VERIFICATION:END -->

