# Component next steps

<!-- WORKSPACE-VERIFICATION:START -->
## Component verification continuation

Updated 2026-10-05. Install/start commands and actual screenshots are in [README](../README.md).

## Chew Sense Collect and Label / ios

Startup: **opened**. Startup evidence only; candidate flows not run. Production acceptance is not established.

Candidate flow (not run): Read the disclaimer and personally choose camera/motion/local-network permissions. With compatible AirPods, record a short disposable clip, stop, reopen Recordings/detail, inspect the motion/video output and export a test recording. Report unavailable AirPods data separately from recording failures.

Fixture setup: Synthetic data and isolated storage. Use mock/emulator auth, API, mail and paid-provider endpoints. No live automation, deletion of user data, or external submissions. Separate simulator/test store; physical sensors, HealthKit, camera, Pencil and cloud/device behavior need a later human/device check.

Codex: Turn this candidate into a fixture-backed simulated flow with explicit assertions and capture its result.

User: Open Chew Sense Collect and Label on your iPhone 16 Pro Max, review its startup screenshot, and choose its intended product role. Use the documented device/permission flows after isolated simulated checks; record any crash or missing data.

<!-- WORKSPACE-VERIFICATION:END -->

