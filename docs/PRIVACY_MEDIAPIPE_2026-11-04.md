# MediaPipe usage reports — investigation and fix (November 4, 2026)

Follows the privacy note in [PHASE2_2026-11-03.md](PHASE2_2026-11-03.md) §7.

## Finding

The locked `mediapipe` 0.10.35 wheel loads `mediapipe/tasks/c/libmediapipe.dylib`.
It contains Google's Clearcut uploader: `PortableClearcutUploader` and an
`IonHttpClient` over NSURLSession that posts to `https://play.googleapis.com/log`.
MediaPipe's open-source repository has no such code; the PyPI builds come
from Google's internal release branch.

Google's position (maintainer, [google-ai-edge/mediapipe#6291](https://github.com/google-ai-edge/mediapipe/issues/6291),
July 2026):

- telemetry is being rolled out on all platforms;
- there will be no official API to disable it;
- input data is never sent;
- source builds contain no telemetry, and blocking the host is acceptable.

The reporter saw none in 0.10.21. The [MediaPipe Tasks page](https://developers.google.com/edge/mediapipe/solutions/tasks)
says the APIs send "metrics about the performance and utilization of the APIs".

## What a report can contain

These message schemas were decoded from the descriptors embedded in the dylib
(`logs.proto.mediapipe`). They show what the format can carry, not a captured
upload.

| Message | Fields |
| --- | --- |
| `SystemInfo` | platform, app ID, app version, MediaPipe version, host (Python) version, host environment |
| `SolutionEvent` | solution name (e.g. face or hand landmarker), event (start, invocations, end, error) |
| `SolutionSessionStart` | mode (video), graph name, initialisation latency |
| `SolutionInvocationReport` | mode, average and peak latency, elapsed time, dropped count, invocation count by input type |
| `SolutionError` | error code |

None of these fields can hold an image, landmark, blendshape or file path.
The Clearcut envelope (`LogRequest`/`ClientInfo`/`LogEvent`) also allows event
time, timezone offset, OS version, country and client or logging identifiers.
Which of these the uploader fills in was not observed. The connection itself
exposes the Mac's IP address. Together, a report can reveal that and when face
or hand video was processed, and roughly for how long (elapsed time and frame
counts).

**Earlier runs probably sent reports.** The Nov 2 `vision.log` files contain
"Not valid for uploading until …" (the uploader waiting on a server-set
interval). Other runs logged nothing, which is what a successful upload looks
like. Reports already sent cannot be recalled.

## Fix

`pipelines/vision/chewsense/features/offline.py`:

- **Sandbox:** `/usr/bin/sandbox-exec` runs the process with
  `(allow default)(deny network*)(deny mach-lookup (global-name "com.apple.dnssd.service"))`.
  `network*` alone still allowed Network.framework to resolve
  `play.googleapis.com` through the dnssd Mach service; the second rule
  closes that. The PID is unchanged, so Stop and the parent watchdog work as
  before.
- **Enforcement:** `FaceExtractor` and `HandsExtractor` call
  `require_offline()` before loading a model. A process that can open an
  outbound socket gets `MediaPipeNetworkError`. The probe is a UDP `connect`
  to a documentation address, which sends no packets.
- **Entry points:**
  - the loop's `VisionRunner` launches `run_on_sessions.py` sandboxed, after
    fetching models into the directory it passes on;
  - `run_on_sessions.py`, `chewsense-vision run` and `extract-features`
    re-run themselves sandboxed.

  The re-run in `run_on_sessions.py` also covers the loop service that is
  running now: its Vision step starts that script fresh from disk, so no
  restart is needed.
- **No override:** no environment variable or flag bypasses the check.

The lock was not changed. Pinning `mediapipe<=0.10.21`, or building from
source, would remove the uploader instead. Either would also change the
inference binary that produced every stored Vision output and the Phase 2
calibration, so that choice is left to you.

## Validation

| Check | Result |
| --- | --- |
| `pytest pipelines/vision/tests/test_offline.py pipelines/vision/tests/test_loop.py` | 64 passed (7 new) |
| Real landmarkers, unsandboxed | refused before any model loaded |
| Real landmarkers in the sandbox, synthetic gray frames, 65 s | 4 upload attempts, all `NSURLErrorDomain -1003`; unified log: "Sandbox does not allow access to com.apple.dnssd.service"; 0 endpoints resolved, 0 connections; exit 0 |
| Same with `deny network*` only (superseded) | DNS answered; 64 TCP attempts all failed `Operation not permitted`; none connected |
| `chewsense-vision run` on the 30 s guided timing check (re-runs itself sandboxed) | 28 s; 2 blocked attempts; source SHA-256 unchanged; 919 frames, 35/36 numeric columns bit-identical to the Nov 2 output. The exception, `label_confidence`, and the cycle confidence/evidence fields reflect Phase 2's calibration; cycle times are identical. Scratch output deleted. |
| The running service's (pre-change) Vision command, `python run_on_sessions.py --only <id>` unsandboxed, on a synthetic gray-video bundle | re-ran itself sandboxed; processed, upload attempt blocked. With the re-run bypassed, the guard refused (`MediaPipeNetworkError`). |

**Not validated:** the live loop service has not processed an upload since
the change. Its next Vision step will be the first in-service sandboxed run.
That step's `vision.log` should show the `INTERNAL: ResponseInfo` failures
above, never "Not valid for uploading until".


## In-service confirmation — November 6

The existing loop service processed the real prior iPhone recording
`Not-eating-20261026-174428-C27C9380` through its normal queued import and
Vision runner. The worker (PID 7611, parent service 42269) logged two failed
Clearcut attempts; same-PID system logs at 17:19:12 show DNS service denial,
`EPERM`, and both URLSession requests failing with -1003. Evidence is retained
locally in `workspace/experiments/device-20261106/network-denials.log` and the
recording's `workspace/loop/reports/sessions/.../vision.log`. This closes the
specific outstanding first-upload/in-service validation above. No sandbox or
telemetry exemption was introduced.

The next capture, a newly recorded Natural Meal, independently confirmed the
same behavior. Vision logged 16 failed Clearcut attempts. For that worker
(PID 12906), macOS logged 16 mDNS access denials and 16 HTTP failures with zero
bytes transferred. The filtered evidence is in
`workspace/experiments/device-20261106/meal-network-denials.log`.

The socket guard was also hardened: unexpected socket-creation failures such
as `EMFILE` no longer imply isolation. At least one explicit `EPERM` is required;
unsupported address families alone are inconclusive. Fourteen offline tests
pass, including real sandboxed children. See the
[continuation report](DEVICE_PIPELINE_2026-11-06.md) for scope and limitations.
