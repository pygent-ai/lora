# Lora Documentation

This directory contains project-level documentation. Package-local README files stay next to their code when they describe only that package.

## Current Docs

- [API](api/README.md): local FastAPI service routes, SSE events, and generated OpenAPI contract.
- [CLI](cli/lora-chat.md): command-line chat usage.
- [Guides](guides/development-guide.md): development workflow and operational guides.
- [Architecture](architecture/repository-layout.md): active repository layout and runtime boundaries.
- [Design](design/agent/agent-context-architecture-zh.md): design notes for agent context, prompt composition, runtime behavior, and self-optimization.
- [Product](product/README.md): product notes for the Electron + React transition.
- [Planning](planning/superpowers/): historical specs and implementation plans.
- [Backlog](backlog/unimplemented/README.md): known unimplemented or deferred areas.

## Directory Map

```text
docs/
  api/                  Local FastAPI service documentation and API contracts
  architecture/         Current repository and system architecture
  cli/                  CLI user documentation
  design/               Design docs grouped by subsystem
    agent/
    agent-self-optimization/
    runtime/
  guides/               Development and operations guides
  product/              Product and desktop-shell notes
  planning/             Historical specs and implementation plans
  backlog/              Deferred work and unimplemented notes
```
