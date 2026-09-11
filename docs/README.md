# Lora Documentation

This directory contains project-level documentation. Package-local README files stay next to their code when they describe only that package.

## Current Docs

- [Principles](principles/README.md): normative first principles that constrain product, architecture, memory, compression, and evaluation decisions.
- [API](api/README.md): local FastAPI service routes, SSE events, and generated OpenAPI contract.
- [CLI](cli/lora-session.md): session, chat, and Agent-collaboration commands.
- [Guides](guides/development-guide.md): development workflow and operational guides.
- [Architecture](architecture/repository-layout.md): active repository layout and runtime boundaries.
- [Design](design/agent/agent-context-architecture-zh.md): design notes for agent context, prompt composition, runtime behavior, self-optimization, and pygent runtime requirements.
- [Lora Foreground ReAct Agent Capability Baseline](design/agent/foreground-react-agent-capability-and-pygent-requirements-zh.md): proposed foreground Agent behavior, prompt/input placement, runtime steering semantics, and the minimal Pygent framework capabilities required to support it.
- [Product](product/README.md): product notes for the Electron + React transition, including message display guidelines.
- [Planning](planning/superpowers/): historical specs and implementation plans.
- [Backlog](backlog/unimplemented/README.md): known unimplemented or deferred areas.

## Directory Map

```text
docs/
  principles/           Normative first principles and non-negotiable invariants
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
