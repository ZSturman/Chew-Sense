# Shared Capture Contract

`CanonicalCaptureContract.swift` is compiled directly by both Collect and
Live. It owns schema-v2 manifest construction, host-clock synchronization,
gap/interruption fields, media SHA-256 values, and atomic JSON serialization.

Recording controllers, UI, and model inference remain in their respective
apps. Changes to the serialized session contract must be covered by both app
test bundles and the Python golden-session audit.
