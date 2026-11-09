# Chew Sense apps: what exists, where it runs, how to move between them

Audited November 8, 2026 from the project files, the Mac (`/Applications`,
Xcode build folders, Launch Services, LaunchAgents) and the paired iPhone 16
Pro Max (`xcrun devicectl device info apps`). Nothing here is inferred from
documentation alone.

## Active apps

| App | Platform | Purpose | Installed at | Version | Status |
| --- | --- | --- | --- | --- | --- |
| **Chew Sense Collect** | iOS | Guided recordings (video + AirPods motion), Mac pairing, upload, "What happens next" | iPhone (development build of `125b0df`) | 1.2 (8) | Active. TestFlight still 1.2 (7); 1.2 (8) archived in `build/StoreRefresh/chew-ios-1.2-8/`, upload pending an Xcode account sign-in |
| **Chew Sense Live** | iOS | AirPods-only model checks (ChewNet + experimental eating detector in shadow mode), "Where we are" | iPhone (development build, `b079810`) | 1.0 (1) | Active, experimental model |
| **Chew Sense Review** | macOS | Blind and assisted labelling, chewing spans, Pipeline and Training & Evaluation windows | `/Applications/Chew Sense Review.app` (Release, Apple Development signed, `b079810`) | 1.0 (1) | Active |
| **Chew Sense loop service** | macOS background (LaunchAgent `com.chewsense.loop`) | Receives uploads, runs Vision (offline sandbox) and proposals, review tasks, catalog, shared status, phone API on port 48765 (Bonjour `_chewsense._tcp`) | Runs from the repository `.venv` (`chewsense-vision loop serve`) | repo `HEAD` | Active; restarted Nov 8 |

Command-line tools (not apps): `chewsense-motion` (training, evaluation,
export, parity, status) and `chewsense-vision` (Vision, loop control,
`loop project-status`), from `ChewSense/.venv`.

| Identity | Source | Bundle ID |
| --- | --- | --- |
| Collect | `apps/collect-ios/ChewSenseCollect.xcodeproj`, scheme `ChewSenseCollect` | `zachary-sturman.ChewSense-DataCollectionAndLabeling` |
| Live | `apps/live-ios/ChewSenseLive.xcodeproj`, scheme `ChewSenseLive` | `zachary-sturman.ChewSenseDebuggingModel` |
| Review | `apps/review-macos/ChewSenseReview.xcodeproj`, scheme `ChewSenseReview` | `com.chewsense.cyclereviewer` |
| Shared Swift | `apps/shared-capture/` (capture contract, AirPods panel, pinned trust, `ProjectStatus` + `ChewSenseLink`) | compiled into Collect and Live; `ProjectStatus.swift` also into Review |

Bundle IDs keep their historical names on purpose: changing them would create
new apps with empty data containers, break TestFlight continuity (Collect),
lose Live's pairing and Keychain token, and reset Review's preferences. Only
display names were unified.

## How they work together

```
 iPhone                                   Mac
 ┌──────────────────────┐   upload     ┌──────────────────────────┐
 │ Chew Sense Collect   │ ───────────▶ │ loop service             │
 │  guided recording    │ ◀─────────── │  import → Vision (offline)│
 └──────────▲───────────┘  requests,   │  → proposals → tasks     │
            │ link        status       │  → project_status.json   │
 ┌──────────┴───────────┐ ◀─────────── └────────────┬─────────────┘
 │ Chew Sense Live      │   status only             │ files
 │  AirPods model check │                ┌──────────▼─────────────┐
 └──────────────────────┘                │ Chew Sense Review      │
                                         │  blind / assisted label │
                                         │  Pipeline, Training     │
                                         └──────────┬─────────────┘
                                                    │ verified labels
                                         chewsense-motion: train → evaluate
                                         (locked test) → export → Live
```

Every app shows the same "Where we are" card from the loop service
(`pipelines/vision/chewsense/loop/project_status.py`): the current stage
(Collect → Label → Train → Test), numbered next steps tagged with the app
they happen in, and plain answers on model testing, visual detection and data.

## Moving between apps

Links (only navigate; none starts a recording, sensor or model check):

| Link | Opens |
| --- | --- |
| `chewsense-collect://experiment/<id>` | Collect, the experiment's preparation sheet (close returns where you were) |
| `chewsense-collect://status` | Collect, What happens next |
| `chewsense-live://status` | Live, Where we are (refreshes from the Mac) |
| `chewsense-review://session/<id>?workspace=blind\|assisted` | Review, that recording in the right workspace |

- **Collect:** a Record step has **Prepare in this app**; the Live step has
  **Open Chew Sense Live** (shown only when Live is installed). iOS shows
  "◀ Chew Sense Collect" in the status bar to return.
- **Live:** Record steps have **Open in Chew Sense Collect**, landing on that
  experiment.
- **Review:** steps with a recording have **Open blind review** / **Open
  recording**. The new **Go** menu has Assisted Review (⌥⌘1), Blind Review
  (⌥⌘2), Pipeline and Next Steps (⇧⌘P), Training and Evaluation (⇧⌘T). No
  folder picker is needed after the workspace is chosen once.
- Phone ↔ Mac cannot deep-link; the shared card names the Mac step and Review
  opens it in one click.

## Other installations found

| Item | Where | Assessment |
| --- | --- | --- |
| Chew Sense Collect and Label 1.1 (6), `com.chewsense.archive.ChewSense-DataCollectionAndLabeling` | iPhone | Installed by the Oct 5 automated audit; same display name as the real Collect; empty Documents. **Removed Nov 8** (with your approval). |
| ChewSense 1.0, `com.chewsense.archive.ChewSense` | iPhone | Hello-world placeholder from the audit; no data. **Removed Nov 8** (with your approval). |
| ChewSense Model 1.0, `com.chewsense.archive.ChewSense-Model` | iPhone | Pre-Live debugging app from the audit; 3 KB idle log, backed up to `workspace/backups/iphone-legacy-apps-20261108/`. **Removed Nov 8** (with your approval). |
| HowsMyEating_DataCollection, `com.audit.howsmyeating.20261006…` | iPhone | 2024 prototype installed by the audit; empty. **Removed Nov 8** (with your approval). |
| ChewSenseCollectUITests-Runner | iPhone | Xcode UI-test runner; reinstalled by every device UI test. Harmless; remove after testing if unwanted. |
| Review Debug / Release builds | `ChewSense/build/DerivedData-mac/Build/Products/` | Development builds; unregistered from Launch Services so links and Spotlight open `/Applications`. Regenerable. |
| Collect 1.1 (6) build, Oct 26 | `~/Library/Developer/Xcode/DerivedData/ChewSenseCollect-…` | Deleted Nov 8 (stale, regenerable). |
| Collect 1.2 (7) and 1.2 (8) archives | `ChewSense/build/StoreRefresh/chew-ios*/` | Release records; **kept**. |
| `ChewSense copy/` (7.1 GB) | workspace root | Older snapshot of the pre-monorepo tree (CycleReviewer, RealTime, Demo projects). Not installed anywhere. Keep until you decide; not part of the active system. |
| `ChewSense Collection/` | workspace root | Archived legacy projects (ChewTracker, HowsMyEating, VideoIMURecorder, sandboxes) with `com.chewsense.archive.*` IDs. Archive only. |
| `~/Library/Preferences/com.zsdynamics.ChewTracker.plist` | Mac | 2023 ChewTracker settings; app not installed. Left alone. |
