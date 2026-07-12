# Local Workspace

This directory contains local recordings, annotations, derived artifacts,
models, manifests, and reports. Real participant data is intentionally ignored
by Git.

Set `CHEWSENSE_WORKSPACE` to an absolute directory to keep these files
elsewhere. The expected structure is:

```text
sessions/{raw,field-tests,curated,rejected}
derived/{vision,motion}
models/{registry,exports}
manifests
reports
archive-assets
```

The local reorganization checksum inventory is
`manifests/reorg-2026-06-15.json`.
