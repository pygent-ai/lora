# Persisted Diff Tool Design

## Goal

Add a first-class `diff` tool that lets the model inspect file changes recorded by Lora, using data persisted under the current session directory.

The tool must answer questions like:

- Which files changed during this turn, run, or session?
- What unified patch was produced by a recorded write/edit/delete?
- Can the same diff be recovered after restarting the chat session?

The important constraint is that `diff` must use Lora's persisted evidence chain, not transient model context and not only live `git diff` output. A restarted session should still be able to inspect file changes that were recorded earlier.

## Existing Architecture Fit

The current code already records most of the facts this feature needs:

- `ToolInterceptor.call_tool()` wraps every model-triggered tool call.
- `FileEffectTracker` snapshots the workspace before and after tool execution.
- `file.read`, `file.write`, `file.edit`, and `file.delete` events are written to each run's `file_events.jsonl`.
- `EventStore._append_session_log()` also projects file events to `.lora/sessions/{session_id}/logs/file_events.jsonl`.
- `LoraAgent._register_default_tools()` registers the model-visible tool set through `ToolManager`.

The new design should extend this path rather than adding a separate diff mechanism. `file_events.jsonl` remains the durable fact log; the new diff artifacts are derived evidence that make file changes easier to inspect and replay.

## Recommended Approach

Use persisted before/after snapshots plus generated patch artifacts.

For every observed write, edit, or delete, Lora should store enough content under the run directory to reconstruct the textual diff later. The `diff` tool reads those persisted artifacts and can return either a summary, structured JSON, or a unified patch.

This is preferred over:

- A summary-only tool that reads `file_events.jsonl`, because hashes alone cannot reconstruct patch text.
- A command wrapper around `git diff`, because live repository state can change after the original session or run.

## Persisted Layout

Each run gets a `diffs` directory:

```text
.lora/sessions/{session_id}/cases/{case_id}/runs/{case_run_id}/
  file_events.jsonl
  diffs/
    snapshots/
      {tool_call_id}/
        before/{relative_path}
        after/{relative_path}
    patches/
      {diff_id}.patch
    diff_events.jsonl
```

Each session also gets a cross-run index:

```text
.lora/sessions/{session_id}/logs/diff_events.jsonl
```

The run-local files are the source of recoverable patch content. The session-level file is an append-only index that lets later turns discover historical diffs without scanning every run directory first.

## Event Types

Add `diff.created` to `LORA_EVENT_TYPES`.

`diff.created` is emitted after a write/edit/delete file event has enough data to create a patch artifact. It is written to:

- run `events.jsonl`
- run `diffs/diff_events.jsonl`
- session `logs/diff_events.jsonl`

Recommended projection row:

```json
{
  "event_id": "evt_...",
  "diff_id": "diff_...",
  "session_id": "chat-chat-...",
  "case_id": "chat",
  "case_run_id": "run-...",
  "turn_id": "turn-0001",
  "tool_call_id": "evt_tool",
  "tool_name": "edit",
  "path": "E:\\Projects\\lora\\src\\lora\\agent.py",
  "relative_path": "src/lora/agent.py",
  "change_type": "edit",
  "before_exists": true,
  "after_exists": true,
  "before_hash": "...",
  "after_hash": "...",
  "snapshot_before_path": "...\\diffs\\snapshots\\evt_tool\\before\\src\\lora\\agent.py",
  "snapshot_after_path": "...\\diffs\\snapshots\\evt_tool\\after\\src\\lora\\agent.py",
  "patch_path": "...\\diffs\\patches\\diff_....patch",
  "patch_char_count": 1234,
  "patch_line_count": 42,
  "created_at": "2026-06-01T..."
}
```

## Snapshot Rules

`FileEffectTracker` currently stores hashes in `FileSnapshot`. Extend the write/edit/delete path so it persists relevant file content:

- `file.write`: store only `after` content.
- `file.edit`: store both `before` and `after` content.
- `file.delete`: store only `before` content.

No patch is created for `file.read`.

For missing sides, use a metadata value such as `before_exists=false` or `after_exists=false` rather than creating placeholder files. The patch generator can render missing sides as `/dev/null`.

Only workspace-relative paths should be mirrored under `diffs/snapshots`. Absolute input paths must be normalized and validated under `workspace_root` before writing snapshots. This prevents path traversal inside the artifact directory.

## Diff Generation

Use Python's standard library `difflib.unified_diff` instead of shelling out to `diff`.

Benefits:

- Works consistently on Windows.
- Does not depend on Git Bash or GNU diff availability.
- Produces deterministic text from persisted snapshots.

Patch headers should use stable workspace-relative names:

```text
--- a/src/lora/agent.py
+++ b/src/lora/agent.py
```

For new files:

```text
--- /dev/null
+++ b/path
```

For deleted files:

```text
--- a/path
+++ /dev/null
```

## Tool Schema

Register a model-visible tool named `diff`.

```json
{
  "name": "diff",
  "description": "Show persisted file diffs for the current Lora session or run. Uses recorded file effects and stored snapshots, so results survive chat restarts.",
  "parameters": {
    "type": "object",
    "properties": {
      "scope": {
        "type": "string",
        "enum": ["turn", "run", "session"],
        "description": "Which persisted changes to inspect. Defaults to run."
      },
      "path": {
        "type": "string",
        "description": "Optional workspace-relative or absolute file path filter."
      },
      "tool_call_id": {
        "type": "string",
        "description": "Optional tool call id to inspect a single tool's file changes."
      },
      "format": {
        "type": "string",
        "enum": ["summary", "patch", "json"],
        "description": "Output format. summary lists changed files; patch returns unified diff; json returns structured records."
      },
      "limit": {
        "type": "integer",
        "description": "Maximum number of diff records or patch files to return."
      }
    },
    "required": []
  }
}
```

Defaults:

```text
scope = "run"
format = "summary"
limit = 20
```

## Tool Behavior

`diff(format="summary")` returns compact records:

```json
{
  "scope": "run",
  "count": 2,
  "diffs": [
    {
      "diff_id": "diff_...",
      "change_type": "edit",
      "path": "src/lora/agent.py",
      "tool_name": "edit",
      "tool_call_id": "evt_tool",
      "patch_path": "..."
    }
  ]
}
```

`diff(format="patch")` returns patch text when it is small enough. Large patch output should be spooled to `diffs/patches/{diff_id}.patch`, and the tool result should include a preview plus the full patch path.

`diff(format="json")` returns the structured diff event records with paths, hashes, patch paths, and metadata.

Filters:

- `scope="turn"` reads current run diff records matching `turn_id`.
- `scope="run"` reads current run diff records.
- `scope="session"` reads `.lora/sessions/{session_id}/logs/diff_events.jsonl`.
- `path` filters by normalized absolute path or workspace-relative path.
- `tool_call_id` filters by the recorded tool call.

## Registration and Prompting

Add the custom `DiffTool` to the same registration flow as pygent tools:

- Keep `DEFAULT_PYGENT_TOOL_NAMES = ("bash", "read", "write", "edit", "glob", "grep")`.
- Register `DiffTool` after pygent tools in `_register_default_tools()`.
- The rendered prompt should naturally list `diff` in `tool_names`.

Update the available-tools prompt guidance with one sentence:

```text
Use diff to inspect persisted Lora file changes. Use bash git diff only for live repository state.
```

This tells the model that `diff` is for recorded session evidence, while shell diff commands are for current workspace state.

## Error Handling

Use normal `ToolInterceptor` error semantics.

Expected behavior:

- No matching changes: return `status=success` with `count=0`.
- Matching file events but missing snapshot artifacts: return `status=success` with a warning and summary metadata, but no patch text.
- `path` outside workspace: return `status=error`, `error_type="PathOutsideWorkspace"`.
- Invalid `scope` or `format`: schema should reject this before execution where possible; otherwise return `status=error`, `error_type="ToolArgumentError"`.
- Missing `tool_call_id`: return `status=success` with `count=0`.
- Binary or undecodable file content: record the file event and hashes, but mark `patch_available=false` with a reason.

## Size and Retention

Patch artifacts and text snapshots can grow. Initial safeguards:

- Skip snapshot content for files above a configurable size threshold.
- Store hash metadata even when content snapshot is skipped.
- Use patch spooling for large returned patch output.
- Keep artifacts under the run directory so any future retention policy can prune old sessions as a unit.

Suggested initial defaults:

```text
max_snapshot_bytes = 1_000_000
max_inline_patch_chars = 20_000
max_inline_patch_lines = 200
```

## Testing Plan

Add focused unit tests:

1. `diff` is registered and appears in `tools_param()` and rendered `tool_names`.
2. Editing a tracked text file writes before/after snapshots, a patch file, and `diff.created`.
3. `diff(format="summary")` returns the changed file and change type.
4. `diff(format="patch")` returns unified diff text for small patches.
5. Restart-style lookup works by creating a new tool instance over the same session/run directory and reading existing diff artifacts.
6. New file and deleted file patches render `/dev/null` headers.
7. `scope="session"` can see diffs from multiple runs through session logs.
8. Oversized or binary files return summary metadata with `patch_available=false`.
9. A path outside the workspace returns a structured tool error.

Add at least one scenario test that runs through `ToolInterceptor` with file-effect tracking enabled, then calls `diff` as a model-visible tool.

## Non-Goals

- Do not replace `git diff`.
- Do not attempt OS-level file access auditing.
- Do not preserve every intermediate write inside a single tool call. The existing net-effect model remains correct: create-edit-delete in one call can produce no net file effect.
- Do not track files outside the workspace for write/edit/delete snapshots.

