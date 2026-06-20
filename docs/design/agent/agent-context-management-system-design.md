# Agent Context Management System Design

## 1. Goal

Build a context management system for coding agents that records conversation history, tool calls, tool results, project file changes, file read/write activity, and timestamps. The recorded state should support Git-like version operations: commit, checkout, branch, merge, reset, and diff.

The system also provides prompt modularization, request-system prompt assembly, file status tracking, and duplicate file-read detection. When a read tool attempts to read unchanged content that has already been read, the system returns a fixed prompt instead of the full file content.

## 2. Design References From `prompt-injection-map.md`

The reference file shows several useful patterns:

- Prompt modules are split by ownership: global system prompts, compact prompts, memory prompts, and one prompt file per tool.
- Tool prompt files export stable names and descriptions, for example `FILE_READ_TOOL_NAME = 'Read'`, `DESCRIPTION`, and prompt renderer functions.
- Request-scoped prompt content is generated at runtime by functions rather than stored as one static string.
- Request-scoped system prompt sections are appended directly after static cacheable sections; the actual prompt text must not include a synthetic boundary marker.
- The read tool has an unchanged-file stub: if the same file content is already available in the conversation, the tool can return a fixed message instead of re-reading full content.
- Deferred tool loading can expose only tool names first, then load full schemas when needed.

These ideas map well to this system: prompts become composable modules, tool records become versioned events, and file-read deduplication becomes a first-class context policy.

## 3. Core Architecture

```text
Agent Runtime
  |
  v
Context Manager
  |-- Event Store
  |-- Version Store
  |-- File State Tracker
  |-- Read/Write Tracker
  |-- Prompt Registry
  |-- Prompt Composer
  |-- Tool Call Interceptor
  |-- Context Projection Engine
```

### Context Manager

The central service. It accepts user messages, assistant messages, tool calls, tool results, and file events. It exposes query APIs for current context, historical context, branch state, and prompt rendering.

### Event Store

Append-only log of everything that happened. It is the source of truth.

Typical events:

- `conversation.user_message`
- `conversation.assistant_message`
- `tool.call`
- `tool.result`
- `file.read`
- `file.write`
- `file.edit`
- `file.delete`
- `file.snapshot`
- `prompt.rendered`
- `context.commit`
- `context.branch_created`
- `context.checkout`
- `context.reset`

### Version Store

Git-like version layer over the event store. A commit points to:

- parent commit ids
- event range or event ids
- file snapshot tree id
- prompt module versions
- context projection metadata
- author, timestamp, message

### File State Tracker

Maintains the latest known file state per branch:

- path
- existence
- content hash
- size
- mtime if available
- last read event id
- read ranges
- last write/edit event id
- last included context message id

### Prompt Registry

Stores prompt modules as versioned units. A prompt module has:

- id
- namespace
- type: `system`, `user`, `tool`, `memory`, `policy`, `project`, `dynamic`
- content template or render function
- dependencies
- priority/order
- cache scope: `global`, `project`, `session`, `turn`
- version hash

### Prompt Composer

Builds the final prompt for each model call:

1. Load static system modules.
2. Load request-scoped system modules and append them directly.
3. Add user message.
4. Add available tool prompts or tool schemas.
5. Add tool-result reminders where they belong.
6. Emit a rendered prompt record.

### Tool Call Interceptor

Wraps all tools and records:

- tool name
- input args
- start time
- end time
- result
- error
- side effects
- files read/written
- associated model turn

It also enforces policies such as duplicate file-read detection.

## 4. Storage Model

Use three logical stores:

```text
.agent-context/
  refs/
    heads/main
    heads/feature-x
    HEAD
  objects/
    commits/
    trees/
    blobs/
    events/
    prompts/
  index/
    file-state.json
    read-state.json
    prompt-registry.json
  logs/
    event-log.jsonl
```

### Event Object

```ts
type ContextEvent = {
  id: string
  type: string
  branch: string
  timestamp: string
  actor: 'user' | 'assistant' | 'tool' | 'system'
  turnId?: string
  parentEventIds?: string[]
  payload: unknown
  contentHash?: string
}
```

### Tool Call Event

```ts
type ToolCallEvent = {
  id: string
  type: 'tool.call'
  timestamp: string
  turnId: string
  toolName: string
  args: Record<string, unknown>
  argsHash: string
}
```

### Tool Result Event

```ts
type ToolResultEvent = {
  id: string
  type: 'tool.result'
  timestamp: string
  toolCallId: string
  status: 'success' | 'error' | 'blocked' | 'stubbed'
  result: unknown
  resultHash: string
  sideEffects?: FileEffect[]
}
```

### File State

```ts
type FileState = {
  path: string
  exists: boolean
  contentHash?: string
  size?: number
  lastReadEventId?: string
  readRanges: ReadRange[]
  lastWriteEventId?: string
  lastContextMessageId?: string
  lastKnownAt: string
}

type ReadRange = {
  eventId: string
  contentHash: string
  unit: 'line' | 'byte' | 'full'
  start: number
  end: number | 'EOF'
  contextMessageId?: string
  readAt: string
}

type ReadDedupLevel = 'exact' | 'contained' | 'overlap'
```

### Commit Object

```ts
type ContextCommit = {
  id: string
  parents: string[]
  branch: string
  message: string
  timestamp: string
  eventIds: string[]
  treeId: string
  promptSnapshotId: string
  metadata?: {
    model?: string
    tokenUsage?: unknown
    cwd?: string
  }
}
```

## 5. Git-Like Operations

### `commit`

Creates an immutable snapshot of:

- event range since previous commit
- current file state tree
- prompt module versions
- conversation projection state

### `branch`

Creates a new ref pointing to the current commit.

### `checkout`

Switches active branch and restores:

- context event projection
- file state index
- prompt module versions
- read/write state

For physical project files, support two modes:

- `context-only`: switch only agent memory and file-state metadata.
- `workspace`: also restore actual file contents from stored blobs.

### `reset`

Moves branch head to another commit.

Modes:

- `soft`: move HEAD only, keep event index.
- `mixed`: rebuild event index from target commit.
- `hard`: rebuild event index and restore file snapshots.

### `diff`

Can diff:

- conversation events
- tool calls/results
- file state
- prompt module versions
- rendered prompt output
- read/write records

## 6. Prompt Module System

Recommended directory:

```text
prompts/
  system/
    identity.ts
    coding-rules.ts
    safety.ts
    output-style.ts
  tools/
    read.ts
    write.ts
    edit.ts
    grep.ts
  project/
    file-state.ts
    workspace.ts
  user/
    task.ts
  runtime/
    time.ts
    branch.ts
    recent-events.ts
```

Prompt module interface:

```ts
type PromptModule = {
  id: string
  type: 'system' | 'user' | 'tool' | 'project' | 'runtime'
  cacheScope: 'global' | 'project' | 'session' | 'turn'
  order: number
  render(ctx: PromptRenderContext): string | null | Promise<string | null>
}
```

Composition:

```ts
async function composePrompt(ctx: PromptRenderContext) {
  const modules = await registry.resolve(ctx)
  const staticParts = modules.filter(m => m.cacheScope === 'global')
  const requestSystemParts = modules.filter(m => m.phase === 'request_system')

  return [
    ...(await renderAll(staticParts, ctx)),
    ...(await renderAll(requestSystemParts, ctx)),
  ].filter(Boolean).join('\n\n')
}
```

## 7. Request System Prompt Assembly

Each model call should build:

```text
System Prompt
  - static identity
  - coding rules
  - safety rules
  - output style
  - available tools
  - tool result handling reminders
  - context budget reminders

User Prompt
  - latest user message
  - attached files or selected context
  - initial <system-reminder> when CLI/skill context must be shown

Tool Prompt
  - tool result content
  - follow-up <system-reminder> when new CLI/skill resources are detected
```

Rendered prompt output should be recorded as `prompt.rendered` with:

- module ids and versions
- final prompt hash
- request-system module ids
- associated turn id

## 8. File Status Display

Expose an API:

```ts
getFileStatus(branch: string): FileStatus[]
```

Status values:

- `untracked`: file exists but no baseline snapshot.
- `read`: file has been read and is unchanged.
- `modified_after_read`: file changed after last read.
- `written_by_agent`: file was written by an agent tool.
- `edited_by_agent`: file was edited by an agent tool.
- `deleted`: file existed in snapshot but no longer exists.
- `stale`: metadata exists but current disk state is unknown.

CLI-style display:

```text
On branch main
Changes since last context commit:
  read:                src/app.ts
  modified_after_read: src/config.ts
  edited_by_agent:     src/server.ts
  untracked:           docs/notes.md
```

## 9. Duplicate File Read Detection

Policy:

When `Read(file_path, offset?, limit?)` is called:

1. Normalize path.
2. Get current disk content hash.
3. Look up `FileState[path]`.
4. Convert the requested read into a normalized range.
5. Apply the configured read deduplication level.
6. If the configured level is triggered, do not return duplicated full content.
7. Record a `tool.result` with status `stubbed` or `partial`.
8. Return the configured fixed text or the missing range.

The guard must not rely on file hash alone. The file hash proves that the file version is unchanged, but it does not prove that the requested range was already shown to the model. The read-state index therefore stores covered ranges per file version.

Deduplication levels:

| Level | Trigger | Example | Result |
| --- | --- | --- | --- |
| `exact` | Same normalized path, same file hash, same read range. | Previous read `50-80`, new read `50-80`. | Return exact-duplicate stub. |
| `contained` | Same normalized path, same file hash, and a previous range fully contains the new range. | Previous read `1-200`, new read `50-80`. | Return contained-range stub. |
| `overlap` | Same normalized path, same file hash, and any previous range overlaps the new range. | Previous read `1-200`, new read `150-260`. | Return overlap reminder plus only missing content, or block the duplicated portion. |

`contained` includes the `exact` case. `overlap` includes both `contained` and `exact`; it is the most aggressive setting.

Examples by behavior:

- Previous read: full file. New read: lines 20-40. Return stub.
- Previous read: lines 1-200. New read: lines 50-80. Return stub.
- Previous read: lines 1-200. New read: lines 150-260. In `overlap` mode, return content for missing lines 201-260, or return a partial-coverage reminder plus the missing range.
- Previous read: lines 1-200. File hash changed. Return content, because the earlier range belongs to an old file version.

Fixed responses:

```text
File unchanged since last read. The content from the earlier Read tool_result in this conversation is still current. Refer to that instead of re-reading.
```

```text
Requested range is contained in an earlier Read tool_result for the same file version. Refer to the earlier result instead of re-reading.
```

```text
Requested range partially overlaps an earlier Read tool_result for the same file version. Returning only the unread portion.
```

Pseudocode:

```ts
async function readFileWithContext(
  path: string,
  offset?: number,
  limit?: number,
  dedupLevel: ReadDedupLevel = 'contained',
) {
  const normalized = normalizePath(path)
  const content = await fs.readFile(normalized)
  const hash = sha256(content)
  const state = fileState.get(normalized)
  const requestedRange = toReadRange({ offset, limit, content })

  const match = findReadRangeMatch({
    ranges: state?.readRanges ?? [],
    contentHash: hash,
    requestedRange,
    dedupLevel,
  })

  if (match.type === 'exact' || match.type === 'contained') {
    return {
      status: 'stubbed',
      content:
        match.type === 'exact'
          ? FILE_UNCHANGED_STUB
          : FILE_RANGE_CONTAINED_STUB,
      previousReadEventId: match.range.eventId,
      matchedRange: match.range,
    }
  }

  const rangeToRead =
    match.type === 'overlap'
      ? subtractReadRanges(requestedRange, match.overlappingRanges)
      : requestedRange

  const result = formatFileContent(content, rangeToRead)
  const eventId = eventStore.append({
    type: 'file.read',
    payload: {
      path: normalized,
      hash,
      size: content.length,
      requestedRange,
      returnedRange: rangeToRead,
      dedupMatch: match,
    },
  })

  fileState.set(normalized, {
    path: normalized,
    exists: true,
    contentHash: hash,
    size: content.length,
    lastReadEventId: eventId,
    readRanges: mergeReadRanges([
      ...(state?.contentHash === hash ? state.readRanges : []),
      { ...rangeToRead, eventId, contentHash: hash, readAt: new Date().toISOString() },
    ]),
    lastKnownAt: new Date().toISOString(),
  })

  return {
    status: match.type === 'overlap' ? 'partial' : 'success',
    content:
      match.type === 'overlap'
        ? `${FILE_RANGE_OVERLAP_STUB}\n\n${result}`
        : result,
  }
}
```

Range coverage rules:

- Normalize all line-based reads to inclusive 1-based line intervals.
- Represent full-file reads as `{ unit: 'full', start: 1, end: 'EOF' }`, which covers every later line-based or byte-based request for the same file hash.
- The active deduplication level is a policy setting and can be configured globally, per project, per tool, or per agent session.
- For text files, prefer line ranges because tool output is usually line-numbered.
- For binary files and images, use byte ranges or full-file coverage.
- Merge overlapping or adjacent ranges for the same file hash to keep the index compact.
- On file hash change, keep old read ranges for audit, but exclude them from current duplicate-read coverage.

## 10. Read/Write Rules

Recommended policy:

- Write or edit existing files only after a successful read unless user explicitly disables the guard.
- Every write/edit must record before and after content hashes.
- Every write/edit should update file state immediately.
- If a file changed on disk after last read, require re-read or produce a warning.
- File reads may be summarized in context, but the original full content should be available in the event/blob store.

## 11. Context Projection

The model should not always receive the full event log. Build a projection:

```ts
type ContextProjection = {
  messages: Message[]
  recentToolCalls: ToolRecord[]
  relevantFiles: FileState[]
  promptReminders: string[]
  summaries: SummaryBlock[]
}
```

Projection strategies:

- Keep latest user/assistant turns verbatim.
- Keep tool errors and side effects verbatim.
- Replace old successful tool outputs with summaries.
- Keep file content only once while unchanged.
- Keep commit summaries as compact context checkpoints.

## 12. Suggested Public API

```ts
interface ContextManager {
  startSession(input: StartSessionInput): Promise<Session>
  recordUserMessage(input: MessageInput): Promise<EventId>
  recordAssistantMessage(input: MessageInput): Promise<EventId>
  recordToolCall(input: ToolCallInput): Promise<EventId>
  recordToolResult(input: ToolResultInput): Promise<EventId>

  composePrompt(input: ComposePromptInput): Promise<RenderedPrompt>
  getProjection(input: ProjectionInput): Promise<ContextProjection>

  getFileStatus(branch?: string): Promise<FileStatus[]>
  getReadState(path: string): Promise<FileState | null>

  commit(message: string): Promise<ContextCommit>
  branch(name: string, from?: CommitId): Promise<void>
  checkout(nameOrCommit: string, mode?: 'context-only' | 'workspace'): Promise<void>
  reset(commit: CommitId, mode: 'soft' | 'mixed' | 'hard'): Promise<void>
  diff(a: string, b?: string): Promise<ContextDiff>
}
```

## 13. Complete System Capabilities

The final system should manage agent context as a fully versioned state space rather than a plain message list.

Required capabilities:

- Record user messages, assistant messages, tool calls, tool results, system reminders, prompt renders, and runtime metadata.
- Record file reads, writes, edits, deletes, snapshots, content hashes, and file-state transitions.
- Maintain branch-specific file state, read state, write state, and context-reference state.
- Support Git-like commands: `status`, `commit`, `branch`, `checkout`, `reset`, `merge`, `diff`, `log`, and `show`.
- Support both `context-only checkout` and `workspace checkout`.
- Store file blobs so workspace files can be restored from a context commit.
- Version prompt modules and record the exact prompt module versions used in each model call.
- Dynamically compose system prompts, user prompts, tool prompts, runtime prompts, project prompts, and memory prompts.
- Record rendered prompts with module ids, module versions, dynamic inputs, final content hash, and turn id.
- Intercept every tool call and persist tool name, args, timestamps, results, errors, and side effects.
- Detect unchanged duplicate file reads by normalized path, content hash, read-range relation, and configured deduplication level, then return a fixed stub or only the missing content instead of injecting repeated content into context.
- Build compact context projections from the full event log while preserving replayability from stored events.
- Support prompt diff, context diff, tool-result diff, file-state diff, and workspace diff.
- Support audit and replay from any commit.

## 14. End-to-End Runtime Flow

One complete agent turn should follow this flow:

1. Record the user message as a `conversation.user_message` event.
2. Build the current context projection from the active branch and HEAD commit.
3. Resolve prompt modules from the prompt registry.
4. Compose the final system, user, tool, runtime, project, and memory prompts.
5. Record the rendered prompt as a `prompt.rendered` event.
6. Send the rendered context to the model.
7. Intercept each tool call and record `tool.call`.
8. Execute the tool through the policy layer.
9. Record file reads, writes, edits, deletes, and snapshots produced by the tool.
10. Record `tool.result` with result hash and side effects.
11. Update file state, read state, and branch state.
12. Record assistant messages.
13. Optionally create a context commit containing events, file tree, prompt snapshot, and projection metadata.

This flow guarantees that every model-visible input and every tool-visible side effect can be reconstructed later.

## 15. Final Invariants

The system should preserve these invariants:

- Every model call has a corresponding rendered prompt record.
- Every tool result has a matching tool call.
- Every file write/edit/delete has before and after state when the previous file existed.
- Every commit points to a deterministic event set, file tree, and prompt snapshot.
- Branch checkout can rebuild file state, read state, prompt state, and context projection.
- Duplicate read detection is based on normalized path, content hash, read-range relation, and the configured deduplication level, not path alone.
- Context compression never deletes source events; it only changes the projection shown to the model.
- Prompt modules are versioned, so old commits can reconstruct old prompt behavior.
