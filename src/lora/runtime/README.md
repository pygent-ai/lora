# Runtime

Owns model execution, tool execution, context management, and the high-level runtime service.

- `agent/`: agent orchestration split by concern.
  - `core.py`: `LoraAgent` construction and run lifecycle.
  - `model_invoker.py`: native model execution with per-call reasoning display
    metadata; output resets discard failed-attempt reasoning before persistence.
  - `pipeline.py`: model/tool middleware and authorization.
  - `prompt_models.py`: prompt contracts and render context.
  - `prompts.py`: prompt registry, composition, injection policy, and cache.
  - `prompt_sources.py`: built-in prompt renderers and reminder state.
  - `skill_catalog.py`: project/user Skill discovery and shadowing rules.
  - `common.py`: Pygent message conversion and small persistence helpers.
- `context.py`: portable `LoraContext`, a Pygent `Context` subclass and the single
  source of execution-scoped facts. It carries session/case/run/turn identity,
  model projection, complete persisted history, and deferred file-effect jobs.
- `context_compression.py`: model-context compaction.
- `reminders/`: session bootstrap, Git/CLI/Skill observations, and the persistent
  Agent-message inbox. Pygent 0.3.10
  `Reminder` / `format_context` render native runtime context. Tool updates use
  v2 `AppendToolResultContent` operations with stable input IDs; raw ToolResults
  remain unchanged. Agent messages require nested XML, so the reminder module
  appends their escaped `<runtime-context><agent-message>` envelope directly to
  `ToolMessage.content`; the outer conversation checkpoint persists that exact
  projection. There is no legacy system-reminder renderer or v1 adapter.
- `tools.py`: tool observation and file-effect discovery.
- `file_effect_models.py`: dependency-light file-effect contracts.
- `file_effects.py`: deferred file-effect persistence and execution.
- `deployment.py`: model-resource and workspace executor adapters.
- `agent_collaboration.py`: model-visible session collaboration tool definitions
  and visibility policy.
- `service.py`: session-oriented runtime facade used by API and CLI adapters.

Runtime callers import concrete owning modules; package-level compatibility
re-exports are intentionally not provided.

## Runtime-state boundary

`LoraAgent` is a reusable, run-independent Pygent module graph. It must not retain
the current session, case run, turn, observer, or a mutable side-effect queue.
Each managed execution receives those portable facts through `LoraContext`.

Modules may retain only definition-scoped collaborators such as configuration,
prompt registries, model invokers, and workspace adapters. Run-bound services
(`EventStore`, `SessionManager`, prompt context views, and `DiffTool`) are rebuilt
from `LoraContext` at the point of use. This keeps concurrent executions isolated
without creating a new Agent graph for every turn.

Deferred file effects flow explicitly through `LoraContext.pending_file_effects`:
tool observation appends jobs, and the persisted-diff module drains them into the
managed Pygent tool task. There is no hidden observer-owned queue.

Checkpoint restoration delegates directly to Pygent's current context codec
registry without Lora-owned version detection or migration branches.

## Eternal conversation projection

Before the asynchronous extractor publishes its first snapshot, each turn restores
SessionManager history. Once a snapshot is available, native replacement delivers
that snapshot plus every uncovered message. The suffix starts at a user-turn
boundary to retain matching tool calls and results; a fully covered history keeps
its latest exchange for follow-up references. Stored history is not removed.
