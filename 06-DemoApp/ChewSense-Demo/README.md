# ChewSense Demo App

A small iOS app that streams motion data from your AirPods, runs the **ChewNet** CoreML model on-device, and tells you when you're eating — using only motion. **No camera, no microphone**. Designed as a portfolio demo of how the final ChewSense product would feel end-to-end.

## What it does

1. **Start it once.** Put on connected motion-capable AirPods and tap **Start monitoring**. ChewSense polls `CMHeadphoneMotionManager` and starts only after real motion samples arrive.
2. **Eat as normal.** After a few sustained chews (~3–4 s of confirmed chewing), iOS shows a notification: *"Looks like you're eating 🍎."*
3. **Open the app.** A live cartoon face shows your jaw motion in real time — mouth opens and closes from raw motion, while the **CHEWING / NOT CHEWING** label is driven by the model. The app stays at **WAITING** until an actual AirPods sample arrives.

The split is deliberate:
- The **mouth animation** is a signal-processing visualization (band-passed accelerometer envelope) so you can see motion always.
- The **label** comes from the model's binary classifier with hysteresis + a head-shake guard — so chewing gum, talking, or laughing don't trigger false alarms.

## Setup

Requires Xcode 15+ and an iOS 17+ device with AirPods Pro / Max (motion is not available in the simulator).

```bash
# 1. Generate the Xcode project from project.yml
brew install xcodegen          # one-time
cd 06-DemoApp/ChewSense-Demo
xcodegen generate

# 2. Drop the latest model in (and refresh constants)
../scripts/update_model.sh

# 3. Open and run
open ChewSenseDemo.xcodeproj
# Cmd+R on a real device with AirPods connected
```

After XcodeGen runs once, regenerate any time `project.yml` changes. The model files live under `ChewSenseDemo/Resources/` and are picked up automatically.

## Updating the model

Whenever the pipeline produces a new `02-DataPipeline/exports/ChewNet.mlpackage` (typically after `python main.py deploy`), run:

```bash
./06-DemoApp/scripts/update_model.sh
```

That script:
1. Replaces `ChewSenseDemo/Resources/ChewNet.mlpackage` with the new export.
2. Copies the matching `chewnet_norm.json`.
3. Copies the exported `NormalizationConstants.swift`.
4. Synchronizes and verifies the same three artifacts in `ChewSense-Showcase`.
5. Prints model, weights, and normalization SHA-256 values for verification.

Then rebuild the app — `ModelInfoView` (the "ⓘ" button in the top right) will show the new IDs and hashes so you can confirm at a glance which model the binary is running.

The Swift code never references the model by class name; it loads `Bundle.main.url(forResource: "ChewNet", withExtension: "mlmodelc")` and reads input/output names from the model description, so a future model swap requires zero source changes.

## Architecture

```
LiveMotionSource (AirPods → MotionSample @100Hz)
        │
        ▼
MonitoringCoordinator
   ├── MouthOpennessEstimator ─→ mouthOpenness (per sample, animates avatar)
   └── 0.25s tick:
        FeatureExtractor → NormalizationConstants → ChewModel
                       └── ChewDetector (hysteresis + shake guard)
                                  │
                                  ├── eatingState  ─→ CHEWING / NOT CHEWING pill
                                  └── startedEpisode ─→ ChewEpisodeNotifier
                                                            │ (3s grace)
                                                            ▼
                                                    UNNotificationRequest
```

### Reused from `ChewSense-Showcase`
The following files are copied **verbatim** so the inference contract stays in lock-step with the macOS Showcase build:

- `Inference/MotionSample.swift`
- `Inference/FeatureExtractor.swift`, `TimeFeatures.swift`, `FrequencyFeatures.swift`
- `Inference/NormalizationConstants.swift` *(auto-generated; see "Updating the model")*
- `Inference/ChewDetector.swift`
- `Inference/LiveMotionSource.swift`

Demo-specific:
- `App/MonitoringCoordinator.swift` — wires the pipeline end-to-end.
- `App/ChewEpisodeNotifier.swift` — schedules the local notification with a 3 s grace period.
- `App/MouthOpennessEstimator.swift` — heuristic for the mouth animation.
- `Inference/ChewModel.swift` — iOS-target variant that loads from `Bundle.main` (Showcase uses `Bundle.module`).
- `Views/FaceAvatarView.swift`, `RootView.swift`, `ModelInfoView.swift`.

## Tests

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcodebuild -project ChewSenseDemo.xcodeproj \
  -scheme ChewSenseDemo \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
```

The unit suite covers connection transitions, first-sample confirmation,
timeouts, stale-stream reconnection, detector hysteresis, and the bundled
CoreML contract. UI tests use debug-only motion replay scenarios to verify the
avatar, waiting guidance, and `CHEWING` presentation without AirPods.

## Permissions

On first launch the app requests:
- **Motion & Fitness** (AirPods motion)
- **Notifications** (the "you're eating" alert)

It does **not** request camera, microphone, or audio playback access. Monitoring uses Core Motion only.

## Limitations

- AirPods motion only works on physical hardware (Pro / Max). The simulator can't replay it.
- iOS does not provide an API for this app to force-connect AirPods. They must already be connected before `CMHeadphoneMotionManager` reports motion availability.
- Without an audio background mode, iOS may suspend motion delivery after the app backgrounds or the phone locks.
- Current bundled model is binary (eating / not-eating). When the two-head phase model ships (see `ROADMAP.md`), `MouthOpennessEstimator` can be swapped for `0.5 + 0.5 * sin(chew_phase_sin)` and the mouth becomes a direct readout of model phase.
