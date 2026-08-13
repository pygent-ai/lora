# Runtime

Owns model execution, tool execution, context management, and the high-level runtime service.

- `agent/`: agent orchestration split by concern.
  - `core.py`: `LoraAgent` construction and run lifecycle.
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
- `tools.py`: tool observation and file-effect discovery.
- `file_effect_models.py`: dependency-light file-effect contracts.
- `file_effects.py`: deferred file-effect persistence and execution.
- `deployment.py`: model-resource and workspace executor adapters.
- `delegation.py`: delegation tool definitions and visibility policy.
- `service.py`: session-oriented runtime facade used by API and CLI adapters.
- `runner.py`: compatibility facade for the workflow-owned case runner.

Compatibility imports from `lora.runtime.agent` and `lora.runtime` are preserved by package exports.

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

`LoraContext` schema version 2 is the first schema with these run facts. Completed
records remain readable as history, but an in-flight execution journaled with the
older context codec must be restarted rather than resumed across this upgrade.
