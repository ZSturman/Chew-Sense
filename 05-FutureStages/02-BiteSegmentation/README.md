# Historical Bite Segmentation Prototype

This folder is not an active production pipeline.

Canonical ChewSense now distinguishes:

- `bite_event`: the food-entry timestamp;
- `chewing_bout`: the interval following a bite;
- `chew_cycle`: one closed-to-open-to-closed mouth cycle.

Those definitions and their reviewed JSON representation are implemented in
`02b-VideoAssistedLabeling/chewsense/session_bundle.py`.

The code here remains only for migration and fixture-level parity. Do not use
its historical contiguous-run definition to create new labels.
