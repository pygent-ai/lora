# Pygent 0.3.15 upgrade

## Approved scope

Upgrade the pinned official package from 0.3.12 to 0.3.15, based on the release assessment approved with “开始升级”. Reuse Pygent's managed Bash tasks and existing Lora approvals, audit, file-effect and runtime API paths. Call timeout is in seconds and controls foreground observation, not command termination. Preserve unrelated working-tree changes.

## Implementation

- [x] Pin and sync 0.3.15; add real Bash integration tests for finite waiting, output, cancellation, same-version restart queries and task-control capacity.
- [x] Register Bash task controls in both visible and deployment toolkits; authorize Bash detach through the existing policy; close owned standard-tool assemblies.
- [x] Preserve detached task references and output in audit/model projection. Finalize background file effects and terminal audit through the managed execution owner, without restarting commands or blocking foreground observation.
- [x] Extend existing runtime task API with output and final result while retaining existing response fields. Distinguish cancellation request from confirmed task state.
- [x] Run focused tests, Python suite and applicable type checks; review source changes and document cross-version execution-plan incompatibility in README.

## Verification boundaries

Test with real local Bash and synthetic model responses; no external model credentials are required. Do not claim crash-time process adoption or cross-version recovery. Parent completion must not cancel a detached Bash command; runtime shutdown must finish task cleanup before closing history. Failed/cancelled Bash may have committed file writes and requires final observation too.

## Execution decisions

Work on `codex/pygent-0.3.15` in the shared checkout so existing uncommitted prompt/schema changes stay present and intact. The implementation uses the existing task API and model tools; a new desktop task dashboard is outside this dependency-upgrade delivery.

The desktop's existing result view now preserves running/detached state and task ID through live events and replay, and accepts later terminal results for the same call. Completion notifications or a new polling dashboard are not added.

## Review and validation

- Independent review found missing cancelled-task output and unbounded background previews. Both were reproduced with regression tests and fixed; scoped re-review found no blocking issue.
- A repeated cancellation test exposed a SQLite transaction race caused by polling Pygent task results during cancellation. The observer now uses Pygent's native waiting result query; the reproducer passed 20 consecutive runs after the change.
- `pytest -q`: 462 passed, 13 subtests passed. An additional positive finite-wait parameter was added and both wait cases passed.
- Focused upgrade/recovery/file-effect suite: 38 passed.
- Scenario suite: 25 passed excluding the separately verified collaboration scenario (1 passed), for 26 scenario cases total.
- Desktop `npm test`: 144 passed. Vite production build passed with dependency directive warnings.
- Focused Pyright: no errors or warnings. `uv lock --check` and `git diff --check` passed.
- The collaboration scenario initially waited for real background-memory calls inherited from local user settings; its assertions subsequently passed (58 seconds). The reviewer reproduced this fixture dependence on 0.3.14 as well. No unrelated runtime or user configuration was changed.

Project Maintainer: expanded maintenance; `.doc_project_maintainer/` absent, task slice unavailable. Existing flow/symbol records are not applicable; README/API documentation updated. No integrity artifacts or signing keys created. Global repository coverage is not assessed.
