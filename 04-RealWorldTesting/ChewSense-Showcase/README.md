# ChewSense Showcase

A polished SwiftUI macOS app that doubles as a portfolio piece **and** a live AirPods demo for the ChewSense pipeline.

It bundles the deployed CoreML model and two recorded sessions, so it always
works without hardware. When AirPods Pro / Max are connected to the Mac, the
**Live Demo** tab uses the same inference path against real-time motion.

## What it shows

| Tab | What it demonstrates |
|---|---|
| **Pipeline Tour** | Five-card narrative of the lifecycle: Collect → Label → Train → Evaluate → Deploy. |
| **Replay Demo** | Pumps a recorded CSV through `FeatureExtractor` + `ChewNet` + `ChewDetector` at 0.5×–4× speed. Animated probability bar, raw vs. smoothed traces, threshold ticks, state pill, accel-magnitude waveform, and a live bite counter. |
| **Live Demo** | Same UI driven by `CMHeadphoneMotionManager`. Pre-flight checks for AirPods, model load, and inference parity. |
| **Meal Report** | Hero stats (bites, total chews, avg chews/bite, chew rate), composite quality grade (A–D), per-bite Swift Charts bar chart, and a pace timeline. |

## Build & run

Requires macOS 14+ and Xcode 15+ command-line tools.

```bash
cd 04-RealWorldTesting/ChewSense-Showcase
swift build
swift run ChewSenseShowcase
```

Or open `Package.swift` in Xcode and Run.

```bash
swift test
```

The executable embeds `ChewSenseShowcase-Info.plist` at link time so macOS can
show the Motion & Fitness privacy prompt when the Live Demo starts. If motion
permission was previously denied, enable ChewSense Showcase in **System
Settings → Privacy & Security → Motion & Fitness** before trying again.

The suite compiles the preserved CoreML package, verifies its 12-feature
contract and finite probabilities, replays both bundled recordings, and
renders every sidebar destination in an `NSHostingView` smoke test.

## How it reuses the rest of the repo

- **Inference**: `FeatureExtractor`, `TimeFeatures`, `FrequencyFeatures`,
  `ChewDetector`, `ChewModel`, `NormalizationConstants` are copied verbatim
  from [ChewSenseDebuggingModel](../ChewSense-RealTime/ChewSenseDebuggingModel/) — the same code that runs in the iOS field-test app.
- **Model**: `ChewNet.mlpackage` from [02-DataPipeline/exports/](../../02-DataPipeline/exports/), bundled with SwiftPM `.copy` so its weights directory remains intact.
- **Sample sessions**: bundled CSVs from [02-DataPipeline/data/sample/](../../02-DataPipeline/data/sample/).

## What it intentionally doesn't do

This is a focused showcase, not the field-test tool. It does **not** record
video, capture feedback, or export session bundles. The full debugging
surface lives in [ChewSenseDebuggingModel](../ChewSense-RealTime/ChewSenseDebuggingModel/).

Per-bite chew counts use a heuristic peak detector while the two-head
chew-phase model is still in training (see [ROADMAP](../../ROADMAP.md) Phase 3).
The Meal Report explicitly calls this out.
