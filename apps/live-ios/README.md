# ChewSense Live

ChewSenseLive is the motion-only field-test app. It consumes the promoted
CoreML model bundled with this app version, records the active model ID, and
exports field-test bundles for later Vision and Review processing.

Open `ChewSenseLive.xcodeproj` and run the `ChewSenseLive` scheme.

Live does not own labeling or ground-truth creation. Legacy review readers
remain only for migration and are not reachable from active navigation.

Deploy a passing registry model with:

```bash
chewsense-motion deploy
```

See [`../../docs/RUNBOOK.md`](../../docs/RUNBOOK.md).
