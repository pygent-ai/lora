# Deferred File Effect Tracking Design

Date: 2026-06-29

## Context

Lora currently records file effects by running `FileEffectTracker.snapshot_workspace()` inside `ToolInterceptor.call_tool()`. That makes every tracked tool call wait for a full workspace scan before and after tool execution. The bash timeout still applies to the bash process itself, but the wrapper can remain blocked while file-effect tracking scans and hashes the workspace.

The raw snapshot result is not injected into model context. It is internal runtime state used to derive persisted `file.*` events and `diff.created` artifacts. Because of that, file-effect tracking does not need to block the next model request or the next user chat turn.

## Goals

- Do not block tool result delivery on full workspace snapshots.
- Do not block the next agent chat turn on file-effect tracking.
- Preserve persisted file-event and diff artifacts as a best-effort audit trail.
- Keep declared file effects for explicit tools such as `read`, `write`, and `edit`.
- Make pending file-effect work visible to `diff` callers.
- Keep failure isolated: tracking failures must not fail the agent turn after the tool already succeeded.

## Non-Goals

- Do not guarantee exact per-tool attribution for multiple opaque bash commands in one agent turn.
- Do not make workspace snapshots part of model context.
- Do not introduce a file watcher in this change.
- Do not change pygent bash timeout semantics.

## Recommended Architecture

Introduce deferred, session-scoped file-effect tracking.

The synchronous tool path records lightweight metadata only:

1. Append `tool.call`.
2. Compute declared effects from tool arguments.
3. Execute the tool.
4. Append `tool.result`.
5. Store a `DeferredFileEffectJob` in memory for later processing.

After the agent produces its final assistant output for the turn, the agent enqueues pending jobs into a session-level background worker and returns without waiting for the worker to finish.

The background worker processes jobs serially:

1. Load or create a workspace baseline snapshot.
2. Build a current workspace snapshot using `asyncio.to_thread(...)` or a single-thread executor.
3. Compare baseline to current snapshot.
4. Merge declared and observed effects.
5. Append `file.*` events and `diff.created` artifacts.
6. Update the baseline snapshot.
7. Mark the work completed, skipped, or failed.

## Data Flow

```text
LoraAgent.stream()
  creates ToolInterceptor

ToolInterceptor.call_tool()
  append tool.call
  collect declared effects
  execute tool
  append tool.result
  keep DeferredFileEffectJob

LoraAgent.stream() final assistant output path
  drain jobs from interceptor
  enqueue jobs in FileEffectBackgroundWorker
  return to runtime adapter

FileEffectBackgroundWorker
  snapshot baseline/current outside event loop
  append file events and diff artifacts
  update pending state
```

## New Runtime Module

Add `src/lora/runtime/file_effects.py`.

This module should own runtime scheduling and lifecycle concerns:

- `DeferredFileEffectJob`
- `DeferredFileEffectBatch`
- `FileEffectPendingState`
- `FileEffectBaselineStore`
- `FileEffectBackgroundWorker`
- queue registration helpers for session/workspace scoped workers

This code belongs under `runtime`, not `tracing`, because it controls agent execution timing and background scheduling. Existing `tracing` code should continue to own event persistence and diff artifact formatting.

## Existing File Changes

### `src/lora/runtime/tools.py`

Keep:

- `FileSnapshot`
- `FileEffect`
- `FileEffectTracker`
- `declared_effects()`
- `observed_effects()`
- `merge_effects()`
- `append_effects()`

Change `ToolInterceptor.call_tool()`:

- Remove synchronous `snapshot_workspace()` calls from the normal tool path.
- Record declared effects before execution.
- Append `tool.result` before deferred tracking work starts.
- Save a `DeferredFileEffectJob` for tracked calls.
- Add `drain_file_effect_jobs()` so the agent can move jobs to the worker at turn end.

The `ToolInterceptor` should remain responsible for tool call/result trace events and model-facing tool payloads. It should not own background task lifetime.

### `src/lora/runtime/agent.py`

Use the deferred worker from `LoraAgent.stream()`.

Recommended behavior:

- Construct `ToolInterceptor` with file-effect tracking enabled, but in deferred mode.
- After the final assistant message for the turn is emitted, call `interceptor.drain_file_effect_jobs()`.
- Enqueue drained jobs into a session/workspace scoped `FileEffectBackgroundWorker`.
- Do not await job completion.

The agent is the right enqueue point because it has the Lora-specific tool metadata, turn id, session dir, run ref, and workspace root.

### `src/lora/runtime/adapter.py`

No core tracking logic should live here.

If needed, add a small optional hook after stream consumption so generic runtime code can notify agents that a stream finished. The default design can avoid this by placing the enqueue logic inside `LoraAgent.stream()` directly.

### `src/lora/tracing/diffing.py`

Update `DiffTool.forward()` to handle pending deferred work.

Recommended behavior:

- Check pending file-effect state for the requested run/session/turn.
- If pending work exists, wait for a short bounded timeout only if cheap to do.
- If still pending, return a structured result with `pending: true` and a short message.
- Do not block indefinitely.

The existing diff event reading and patch formatting should remain unchanged.

### `src/lora/tracing/events.py`

Existing `file.*` and `diff.created` persistence can be reused.

Optional new event types:

- `file.effects.pending`
- `file.effects.completed`
- `file.effects.skipped`
- `file.effects.failed`

If event schema churn is undesirable, the same state can be written to a runtime state JSON file under the session directory instead.

## Baseline Strategy

The worker should maintain a workspace baseline per session/workspace.

On first use:

- If no baseline exists, create one and treat the first batch as baseline initialization.
- If there are declared effects in the first batch, append those declared effects even if observed effects cannot be derived yet.

On later batches:

- Compare previous baseline to current snapshot.
- Attribute observed changes to the current deferred batch.
- Merge observed effects with declared effects.
- Update the baseline after event persistence succeeds.

This trades exact per-tool attribution for responsiveness. If a single agent turn runs multiple bash commands that edit files, the observed changes may be attributed to the batch rather than precisely to each individual command. Explicit `read`, `write`, and `edit` tools still provide declared per-tool effects.

## Pending State

Pending state should be durable enough for `diff` to report incomplete work after a restart.

Suggested location:

```text
.lora/sessions/<session-id>/state/file_effects_pending.json
```

Suggested fields:

- `session_id`
- `case_run_id`
- `turn_id`
- `batch_id`
- `status`
- `queued_at`
- `started_at`
- `finished_at`
- `tool_call_ids`
- `error`

The worker should write status transitions:

- `queued`
- `running`
- `completed`
- `failed`
- `skipped`

## Failure Handling

Tracking failures must be isolated from agent execution.

If the worker fails:

- Record failure in pending state.
- Optionally append `file.effects.failed`.
- Leave `tool.result` and conversation history untouched.
- Keep the previous baseline if event persistence did not complete.

If snapshot processing exceeds a configured time budget:

- Mark the batch `skipped` or `failed` with reason `snapshot_timeout`.
- Do not retry forever in the foreground path.

## Concurrency

Use one worker per session/workspace pair.

The worker should process batches serially. Snapshot work should run outside the event loop with `asyncio.to_thread(...)` or a single-thread executor. This avoids event-loop stalls and avoids multiple simultaneous full-workspace scans for the same session.

## Testing Plan

Add tests in `tests/unit/test_file_effects.py`:

- Enqueuing a batch does not synchronously call `snapshot_workspace()`.
- Worker processes a batch and writes `file.*` and `diff.created` records.
- Pending state moves through `queued`, `running`, and `completed`.
- Worker failure marks the batch failed without changing `tool.result`.
- Multiple batches process serially.
- `DiffTool.forward()` reports `pending: true` when work is incomplete.

Update `tests/unit/test_tools.py`:

- `ToolInterceptor.call_tool()` appends `tool.result` before deferred tracking.
- `drain_file_effect_jobs()` returns declared effects and call metadata.
- Unsupported arguments and structured tool errors preserve existing behavior.

Update runtime adapter or agent tests as needed:

- A completed `LoraAgent.stream()` enqueues deferred jobs after final assistant output.
- The stream returns without waiting for worker completion.

## Open Design Choices

1. Whether to persist pending state only in `state/file_effects_pending.json` or also append explicit `file.effects.*` events.
2. Whether `diff` should wait for a very short timeout before returning pending status.
3. Whether baseline snapshots should be stored in memory only or persisted across process restarts.

Recommended defaults:

- Persist pending state in `state/file_effects_pending.json`.
- Let `diff` wait up to 500 ms, then return `pending: true`.
- Persist baseline metadata and snapshot hashes under session state so restarts can continue from the latest known baseline.
