# Local Data Recovery

Participant media, generated artifacts, and the retired project history are
intentionally excluded from Git. They remain in two local roots:

- `workspace/`: active sessions, derived artifacts, models, manifests, and reports.
- `archive/`: cold storage for retired source trees and unique field media.

The 2026-07-13 inventory covered 2,051 files and 5,045,338,633 bytes. It found
250 duplicate-content groups, 1,144,852,669 duplicate bytes, and
3,900,485,964 unique bytes. No file was removed by the inventory pass.

Regenerate the ignored, per-file SHA-256 manifest with:

```bash
python3 tools/inventory_local_data.py
```

The result is written atomically to
`workspace/manifests/local-data-inventory-v1.json`. Keep that file with local
backups when pruning generated duplicates. Never use path or filename alone as
proof that media is duplicated; compare the recorded SHA-256 values.

The earlier reorganization receipt remains at
`workspace/manifests/reorg-2026-06-15.json`. It records every source-to-local
move made during the role-based layout change. Imported Collect and Live Git
histories are also parents of the active branch, so their source history is
recoverable without nested repositories or gitlinks.

## Migration State

There are 27 raw session directories. Twenty-six have explicit unverified
schema-v2 manifests and pass the read-only structural audit. The remaining
`Not-eating-20251212-181452` CSV has zero usable rows and is intentionally
quarantined rather than assigned invented timestamps.

Two exact video/motion pairs were re-homed under matching IDs:

- `video-20250919-000138`
- `video-20251021-225301`

Both are `activity: unknown`, require full coarse review, and contribute no
event, phase, or food-entry supervision until fresh review. Migration retained
their original media; the second pair's one short repeated/backward motion-row
glitch was recovered into `motion-v2.csv` while preserving the source CSV.

Candidate generation is still pending because the pinned MediaPipe assets are
not present locally and network access was unavailable. Do not substitute old
derived events as schema-v2 truth.
