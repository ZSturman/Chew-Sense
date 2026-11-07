# Device and pipeline continuation — November 6, 2026

Resumed Claude's clean `3e8fb8c` checkpoint on `claude/phase2-2026-11`.
All Phase 2, permanent MediaPipe sandbox, service restart, catalog v8 and
Natural Meal changes were already present; no merge or project restart was
needed. Source/blind hashes and non-media state were checkpointed before edits
in ignored `workspace/checkpoints/resume-20261106/`.

## Executed evidence

| Surface | Actually executed | Limits |
| --- | --- | --- |
| iPhone 16 Pro Max, iOS 27.2 | Collect 1.2 (7) and Live built, installed in place and launched. Real meal and Live motion-only takes saved | The meal ended intentionally before the final marker. The Live take is unlabelled |
| Physical Live tests | 22 tests passed; Python/Swift synthetic-stream parity, gap handling, session metadata, saved-check status and CPU benchmark | Fixture input on physical hardware |
| Physical Collect tests | 18 guided tests passed after fixing Mac-only fixture paths | Guided timers/quality are fixtures, not physical guided capture |
| Physical → Python contract | Copied the device-generated guided sidecar from app tmp/, validated it in Python | Synthetic sidecar, not a real recording |
| macOS Review | Rebuilt; 25 tests passed, 1 opt-in render skipped; opened actual blind baseline with zero proposals and assisted panel disabled | No human review was completed |
| Python | Initial 83 focused tests passed; affected data/runtime/assist/status/end-to-end group 36 passed, then 20 eating-data tests after split regression; 14 privacy tests; 4 status/physical-sidecar contract tests | Overlapping groups; not a full suite |

The first Collect device run exposed eleven tests reading a Mac source path
for `SeedExperimentCatalog.json`. Both app test suites now load resources from
their bundles. Collect exports the generated contract sidecar into app tmp/;
copy it to `build/contract/` before the Python cross-language check. The catalog
cache test now injects an isolated preferences suite, so applying its fixture
cannot reset the wearer's local collection counts.

### Real recordings processed

An existing, previously unimported phone recording,
`Not-eating-20261026-174428-C27C9380`, was copied over USB to
`workspace/experiments/device-20261106/received/`. The phone original remains.
The received manifest and existing wearer coarse labels were preserved. This
was **prior physical capture**, not a new wearer session or new ground truth.

- Video 25.255 s, 757 frames; 1,249 motion samples, median 50 Hz.
- 98.828% sensor coverage; initial 0.265 s missing, no internal motion gaps.
- All six motion channels and media hashes validated. Shared-clock metadata
  exists; physical marker synchronization is **unverified**.
- Packaged unchanged files and queued through the running service with
  `chewsense-vision loop import`, using its documented local-import path.
  This did not validate phone network upload or resumable transfer anew.
- Actual service completed import → timing audit → role assignment → working
  copies → sandboxed Vision (58.6 s) → coarse proposals → review tasks.
- It was deterministically assigned **evaluation** and has an untouched blind
  copy. Proposals remain automatic artifacts, not verified labels. No assisted
  event boundaries were used to annotate either blind recording.

Evidence: `workspace/loop/reports/sessions/Not-eating-20261026-174428-C27C9380/`,
`workspace/loop/state/sessions/Not-eating-20261026-174428-C27C9380.json` and
`workspace/experiments/device-20261106/received-source-hashes.json`.

The wearer then recorded `Guided-natural-meal-20261106-174058-8029B059` and
intentionally ended it at 210.59 s. The saved bundle contained 10,518 samples
at about 50 Hz, 99.869% motion coverage, one 240 ms gap and about 99% reported
face visibility. The running service took 521.2 s for Vision and produced 159
automatic chew cycles, 15 food-entry proposals and coarse chewing proposals.
The final nod and remaining periods are missing. The take stays diagnostic,
with inconclusive synchronization and zero new human-verified labels. Natural
Meal v1 supplied two marker windows. Catalog v10 now serves v2, which takes
6:15 and requires start, midpoint and final nods. The v1 digest and all other
catalog entries were checked after publication. The phone still needs to sync
the new catalog and complete a v2 take.

### Privacy verified in the running service

The Vision subprocess was PID 7611, parent PID 42269 (the existing loop
service). It logged **two failed Clearcut requests**. System logs for that same
PID at 17:19:12 show `Sandbox does not allow access to com.apple.dnssd.service`,
`Operation not permitted`, and both URLSession failures `NSURLErrorDomain -1003`.
This is the first real in-service post-sandbox processing evidence, rather than
only a standalone reproduction. Filtered local evidence is retained in
`workspace/experiments/device-20261106/network-denials.log`; Vision's log remains
with the recording's report. No sandbox rule, telemetry override or dependency
version was relaxed.

A separate guard defect was reproduced: socket resource exhaustion (`EMFILE`)
previously counted as network isolation. It now fails closed unless at least
one address family reports explicit `EPERM`; only unsupported families are
skipped. Fourteen targeted privacy tests pass, including actual sandbox children.

## Recording integrity correction

The eating-data loader previously accepted a CSV whose declared manifest hash
was wrong, and accepted incomplete guided takes once labels existed. It now:

- validates supplied motion hashes and confines media references to the bundle;
- binds both phone labels and assisted reviews to the actual session ID, rejecting
  wrong/missing identities instead of applying labels from a different take;
- rejects malformed numeric CSV data per recording, preserving other sessions;
- excludes guided takes whose completion is false or unverified, while allowing
  their motion to be replayed diagnostically and proposals to be generated;
- reports missing historical hashes as unverified integrity;
- reuses locked split files without rewriting them when no assignments change.

Wrong-session phone and assisted labels were separately reproduced as accepted
before the identity fix. All existing 32 eligible recordings still load afterward;
33 affected data/assistance/end-to-end tests pass.

The historical Phase 2 run actually included **46 negative development windows**
from incomplete Quiet Sitting, despite the review-task UI excluding that take.
Those historical artifacts and the bundled detector remain intact; they must
not be described as a newly corrected evaluation. The fixed loader excludes
Quiet Sitting even after an assisted review. Its original recording and fixed
historical split assignment are retained.

## Corrected development experiment

Run `eating-20261107-011934` (UTC run ID; November 6 local time) trained all
five candidates on **22 development recordings**. Ten recordings are assigned
to locked evaluation, including the newly received negative. The dataset has
32 eligible recordings from one wearer; 7 source entries were rejected.

| Candidate | Development precision | Recall | F1 | AUC-PR | False-positive rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| Gradient boosting | 0.8664 | 0.8703 | 0.8683 | 0.9444 | 0.0284 |
| Logistic regression | 0.8356 | 0.8539 | 0.8446 | 0.9159 | 0.0356 |
| Temporal CNN | 0.8563 | 0.8274 | 0.8416 | 0.9102 | 0.0294 |
| Motion-energy baseline | 0.2248 | 0.9397 | 0.3628 | 0.1587 | 0.6861 |
| Prior baseline | 0.1747 | 1.0000 | 0.2974 | 0.1412 | 1.0000 |

Gradient boosting was selected on development AUC-PR, with development-chosen
threshold 0.78. Confusion counts on 6,268 development windows: TP 953, FN 142,
FP 147, TN 5,026. Session-bootstrap F1 95% interval: 0.734–0.919.
It detected 17/17 reference bouts but produced 7 false bouts (9.1 per negative
hour); median onset lag 4.08 s, p90 4.79 s. These are coarse-label pilot results,
mostly from legacy recordings with unverified alignment, not real-world
validation or verified individual-chew accuracy.

Development errors: meal-pause negatives 33/451 false positives (7.32%);
unspecified non-eating 114/4,722 (2.41%). Legacy meal labels cannot distinguish
unmarked chewing from at-table/head activity without the missing video.
Talking/drinking/gum/natural-meal/pace data remain insufficient; no reliable
per-activity comparison or behavior improvement is claimed.

No locked-test prediction was populated in the new run, every model's test
metrics are null, and test consultations remain **2**. Every one of the 32
prior split records is byte-for-value unchanged; only the new recording's
fixed evaluation assignment was appended. The original 24 protected source/
blind files and all four USB-received files retain their checkpoint hashes.

The dashboard now selects the newest completed run, including development-only
checks. It no longer keeps showing the old evaluated run and asking for another
check after a successful dev-only check. Historical test results are not mixed
with the new candidate. The export pointer and installed model remain unchanged.

Run outputs: `workspace/experiments/eating/runs/eating-20261107-011934/`.
The run's dirty-tree marker refers to concurrent device-test/UI work; the
training-loader corrections were committed before the run. No model was promoted.

## On-device inference status

Installed detector: `eating-hgb-20261104-015911-7a28dd15`, experimental shadow
mode by default alongside bundled ChewNet. The corrected development candidate
has not replaced it. Video/reference processing remains offline labeling context;
all exported-detector inference features use only AirPods acceleration and
rotation (six channels plus sample validity).

On the physical iPhone, the Debug runtime processed the synthetic parity stream
exactly as Python. The updated build's 234-window benchmark measured **17.668 ms**
median and **19.799 ms** p95 against a 500 ms stride. This measures resampling,
features, trees and decoding. Battery, thermals and end-to-end delay still need
separate measurements.

Three earlier foreground checks saved header-only CSVs. After Live gained the
shared Collect connection panel and stopped requiring video, the wearer selected
the AirPods, played the test sound, and retried. CoreMotion reported an active,
authorized stream and fresh callbacks. No single cause was isolated for the
earlier failures. On November 7 the wearer completed an unlabelled check and
AirDropped the saved folder to the Mac. The export and USB copy matched byte for
byte, and the canonical motion hash matched.

| Live check `FieldTest-20261107-023850-E7978B9B` | Measured |
| --- | --- |
| Input | 32.13 s, 1,590 samples, six axes, 50 Hz median; two gaps, largest 290 ms |
| Reference media | Video and audio off; no camera file, no benchmark candidacy |
| ChewNet | 313 windows, 19 reported chewing |
| Experimental detector | 59 scored shadow windows, all idle |
| Mac replay of exported motion | Same validity and all 59 decisions; max probability difference 4.97e-9 |

The two models disagreed during the take. Its activity was unlabelled, so the
outputs measure runtime and transfer integrity only. Neither model's output is
an accuracy result. The receipt is in
`workspace/experiments/device-20261106/real-live-shadow-validation.json`.

Evidence: `build/Device-Live.xcresult`, `build/Device-Collect.xcresult`,
`workspace/experiments/device-20261106/runtime-benchmark.json`, and the matching
`workspace/experiments/eating/deploy/<model-id>/physical-validation.json`.
Review's training status displays that scoped physical evidence.

## Human gates and next actions

1. **Pairing complete:** the wearer enabled Local Network. Actual Bonjour
   discovery resolved the Mac, authenticated pairing verified fingerprint
   `8da810cf23f6f689…`, and catalog/status sync succeeded. No security/VPN
   workaround was used. Camera and Motion & Fitness were already enabled.
   Natural Meal was staged without recording. Apple then explicitly reported
   that the iPhone camera is unavailable from the Mac while mirrored. The wearer
   recorded the meal on the phone and the bundle was processed after USB transfer.
2. **Independent baseline:** saved blind draft has zero events, zero timer
   seconds, no full pass, no verification and no clock confirmation. Review is
   open on it with predictions hidden; use existing phone sections 24.1–44.2 s
   and 61.5–75.3 s, then check the rest. Codex has read aggregate Phase 2 results,
   so it must not invent independent human gold or insert teacher proposals
   here. There is no evidence of exposure in the saved human draft/UI; a human
   blind review is still available. Finish/freeze it before the baseline's
   assisted workflow. A new negative blind take is also available.
3. **Review and measurement:** retain incomplete Quiet Sitting diagnostically;
   after independent review, use assisted proposals with the existing active-time
   counters. No measured review-time saving or new human-verified labels exists.
4. **Next collection:** sync catalog v10 on the iPhone and complete Natural Meal
   v2 through its final nod. Collect completed hard negatives for talking,
   drinking, gum and head movement, followed by talking while eating. Compare a
   labelled Live shadow check to independent ground truth and measure sustained
   runtime separately.

Native and real-source processing are complete to the gates above. A full new
wearer collection → verified labeling → training → live shadow accuracy cycle
is **not yet demonstrated**. Do not lower the 20 positive/20 hard-negative,
6 independent-take or 300 independent-event research gates.

## Reproduction commands (active repository)

```sh
# Safe read/refresh; no locked-test evaluation.
tools/loop-service status
.venv/bin/chewsense-motion pipeline-status
# Replay one Live export. Expected: pass, 59/59 valid windows, no state differences.
.venv/bin/chewsense-motion eating-parity --live-check \
  workspace/experiments/device-20261106/received/FieldTest-20261107-023850-E7978B9B
# Already executed; repeat only after a meaningful data/code change.
.venv/bin/chewsense-motion eating-experiment --dev-only
# Focused integrity, isolation and dashboard regression checks.
.venv/bin/python -m pytest pipelines/motion/tests/test_eating_detection.py \
  pipelines/motion/tests/test_eating_status.py pipelines/vision/tests/test_offline.py -q
```

The export audit verifies the model ID, the manifest's motion hash, six sensor
channels, window validity and recorded decisions. It takes only the exported
folder and the bundled model spec (use `--spec` for a different model ID).

For native reruns use the existing stable `build/DerivedData-ios` and
`build/DerivedData-mac`, replacing the previous result bundle for the topic.
Device tests use `-destination 'id=00008140-000538120A43801C'` and
`-parallel-testing-enabled NO`, with the documented app project/scheme and
`-only-testing:ChewSenseLiveTests/EatingDetectorParityTests`,
`-only-testing:ChewSenseLiveTests/TemporalRuntimeTests`, or
`-only-testing:ChewSenseCollectTests/GuidedCollectionTests`.

After Collect's physical contract test:

```sh
mkdir -p build/contract
xcrun devicectl device copy from --device 00008140-000538120A43801C \
  --domain-type appDataContainer \
  --domain-identifier zachary-sturman.ChewSense-DataCollectionAndLabeling \
  --source tmp/ChewSenseContract/guided_session.swift.json \
  --destination build/contract/guided_session.swift.json
.venv/bin/python -m pytest \
  pipelines/vision/tests/test_loop.py::test_swift_generated_sidecar_satisfies_mac_contract -q
```

Expected: 18 Collect fixture tests, 22 focused Live tests, and one physical-sidecar
Python contract check pass. These commands are recorded for reproducibility;
Codex has already performed them, so no developer build or command action is
needed to resume.
