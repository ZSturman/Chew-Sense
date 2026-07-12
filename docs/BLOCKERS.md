# Blocking Work

Ordered by pipeline impact.

| Priority | Blocker | Owner | Validation | Complete When |
| --- | --- | --- | --- | --- |
| P0 | Finish parent Git history merge and stage the role-based layout | Repository | `git ls-files -s` has no `160000`; `git log -- apps/collect-ios` and `apps/live-ios` show imported history | Two history-preserving merge commits and a clean reorganization commit exist |
| P0 | Re-run all builds and tests after path and scheme renames | Repository | Python suites pass; all three `xcodebuild` commands pass | Verification results are recorded in `STATUS.md` |
| P0 | Add a tracked canonical video+motion fixture | Data/CV | End-to-end test runs without participant data | Fixture covers collect bundle, vision, review, finalize, and both manifests |
| P0 | Validate recording synchronization on physical hardware | Collect | Offset, drift, dropped rows, missing frames, and interrupted recordings are measured | Shared-clock sessions pass acceptance fixtures and device tests |
| P1 | Finish Review clip context and completion UX validation | Review | Five frames before/after each chew, wider bite clips, keyboard flow, split/merge/reset, and completion gate tested | Reviewer can finish a session without editing JSON or CSV manually |
| P1 | Confirm reviewed revisions survive every field-test import path | Motion | Imported bundle retains canonical review JSON and revision/model metadata | Import round trip preserves checksums and training eligibility |
| P1 | Add chew-event metrics to locked evaluation | ML | F1@250 ms and count error appear in registry metrics | Promotion gate evaluates all required metrics |
| P2 | Remove remaining old path text from active source comments and helper docs | Repository | Active-path `rg` check returns no numbered-layout references | Only archived material contains old path names |
| P2 | Validate external `CHEWSENSE_WORKSPACE` with Review and both CLIs | Repository | Default and external-workspace smoke tests pass | No command depends on repository-local data |

Do not promote a replacement Live model while any P0 model/data blocker is
open.
