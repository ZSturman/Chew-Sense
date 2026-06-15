# ChewSense Collect

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

Collect owns capture and coarse intervals only. Individual mouth states,
chews, bites, and final verification belong to 02b and CycleReviewer.

See [`../../docs/SESSION_BUNDLE.md`](../../docs/SESSION_BUNDLE.md).
