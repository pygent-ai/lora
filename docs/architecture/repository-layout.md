# Repository Layout

This repository is moving toward a local desktop architecture with three main layers:

- `src/lora`: Python core domain logic. It should not import desktop, React, Electron, or FastAPI code.
- `src/lora_api`: local FastAPI service layer. It adapts core Lora capabilities to HTTP and event streams.
- `apps/desktop`: Electron and React desktop shell. It talks to `lora_api` through typed contracts.

## Python feature domains

`src/lora` is organized by capability rather than by technical file type:

- `core` and `schema`: dependency-light primitives and shared contracts.
- `credentials` and `config`: external configuration inputs.
- `sessions` and `tracing`: persisted runtime state and observability.
- `runtime`: agent execution, tools, prompt/context management, and runtime services.
- `workflows`: application use cases that coordinate multiple feature domains.
- `evaluation`: cases, scoring, analysis, regression, and test generation.
- `repair`: orchestration across evaluation and runtime capabilities.
- `cli`: command-line adapter only.

Dependencies should generally point in this direction:

```text
cli / lora_api
        |
workflows / repair
        |
evaluation / runtime
        |
sessions / tracing / config
        |
credentials / schema / core
```

Cross-feature imports should use a package's exported API where practical. A feature may use a narrower submodule import to avoid an import cycle, but should not reach into another feature's private helpers.

## Runtime agent package

The agent is a package because it contains several independently changing concerns:

- `runtime/agent/core.py`: reusable `LoraAgent` definition and graph assembly.
- `runtime/agent/pipeline.py`: model and tool middleware.
- `runtime/agent/prompt_models.py`: prompt contracts and render context.
- `runtime/agent/prompts.py`: prompt composition, injection policy, and cache.
- `runtime/agent/prompt_sources.py`: built-in renderers and reminder state.
- `runtime/agent/skill_catalog.py`: project/user Skill discovery and shadowing.
- `runtime/agent/common.py`: message codecs and small persistence helpers.

`runtime/agent/__init__.py` is the compatibility facade for the former `runtime/agent.py` module.

## Composition and compatibility boundaries

- `workflows/case_run.py` owns the case-run use case. `runtime/runner.py` remains a lazy compatibility facade, so runtime no longer depends on evaluation.
- `runtime/file_effect_models.py` holds dependency-light file-effect data contracts; observation remains in `tools.py` and deferred execution in `file_effects.py`.
- `runtime/deployment.py` and `runtime/delegation.py` isolate Pygent adapter policy from the session-oriented `runtime/service.py` facade.
- `runtime/context.py` is the execution-state boundary: portable run identity,
  turn state, history, and pending side effects travel through `LoraContext`;
  reusable Agent/Module definitions do not retain per-run mutable state.
- `lora_api/container.py` is the API composition root. `dependencies.py` only adapts it to FastAPI, while API services depend on the container instead of FastAPI wiring.
- Package `__init__.py` files expose intentional public symbols. Compatibility modules preserve established import paths without reintroducing dependency cycles.

Project-level documentation is organized under `docs/`:

- `docs/api`: local FastAPI service and generated-contract notes.
- `docs/cli`: command-line usage.
- `docs/guides`: development and operations guides.
- `docs/design`: subsystem design notes.
- `docs/planning`: historical implementation plans and specs.
- `docs/backlog`: deferred or unimplemented work.
