# Shared Capture Contract

`CanonicalCaptureContract.swift` is compiled directly by both Collect and
Live. It owns schema-v2 manifest construction, host-clock synchronization,
gap/interruption fields, media SHA-256 values, and atomic JSON serialization.

Recording controllers, UI, and model inference remain in their respective
apps. Changes to the serialized session contract must be covered by both app
test bundles and the Python golden-session audit.

## Link to the Mac

`LoopLink.swift` is the only phone ↔ Mac client (Collect and Live): pairing,
certificate-pinned requests, the Mac's reported routes (LAN, `.local`,
tailnet) and finding it again on another network, Bonjour, and the phone's
network watch. `LoopPairingForm.swift` holds the shared pairing form and the
**Details** view. `LoopServiceTrust.swift` holds pinning and the token's
keychain entry. Rules: [docs/COMMUNICATION.md](../../docs/COMMUNICATION.md).
