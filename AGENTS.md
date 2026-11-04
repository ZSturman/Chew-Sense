# ChewSense active repository

Preserve Collect → Vision → Review → Motion → Live and schema-v2 lineage.
Historical repositories and original recordings are immutable source evidence.

- Apps: `apps/collect-ios`, `apps/review-macos`, `apps/live-ios`; shared capture contracts in `apps/shared-capture`.
- Python: `pipelines/vision/chewsense`, `pipelines/motion/pipeline`.
- Install: `uv sync --locked` (Python 3.12).
- Focused tests: `.venv/bin/python -m pytest pipelines/vision/tests/test_session_bundle.py pipelines/motion/tests/test_temporal_chewnet.py -q`.
- Review build: `xcodebuild -project apps/review-macos/ChewSenseReview.xcodeproj -scheme ChewSenseReview -destination 'platform=macOS' -derivedDataPath build/DerivedData-mac CODE_SIGNING_ALLOWED=NO build`.
- Use stable per-platform DerivedData paths and topic result bundles; scratch logs go in `/tmp`.
- Keep media/models/experiment output in ignored `workspace/` or `CHEWSENSE_WORKSPACE`.
- Predictions never constitute verified truth. Unknown observability, sensor loss and unverified clocks must mask supervision.
- Do not promote or deploy models without explicit approval. Experimental training is allowed through `chewsense-motion eating-experiment`. It writes only to `workspace/experiments/eating/`, uses human coarse labels with provenance tiers and keeps `splits.json` immutable. Use `--dev-only` while developing so the locked test is not consulted. Device fidelity requires physical evidence.
- Coarse-label tiers with unverified clocks (2025 legacy, field tests) are eligible only behind the ±1 s transition guard; they never supervise individual chews.
- Update `docs/PHASE0.md` and `docs/NEXT_STEPS.md` when pilot behavior or blockers change.
