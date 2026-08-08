# Pygent 0.2.3 integration feedback

This note records issues observed while moving Lora API, CLI, case execution,
approvals, delegation and file-effect persistence onto one `LocalRuntime`.

## High priority

1. **Provide a workspace service facade.** A supported owner that opens and closes
   history, model deployments, task manager and capacity coordinator together
   would prevent every application from recreating the same lifecycle code.
   `LocalRuntime.close()` currently does not close an injected history store or
   capacity coordinator, which is correct ownership-wise but easy to misuse.

2. **Make concurrent SQLite admission a first-class benchmark.** A single
   `SQLiteHistoryStore` writer shows noticeable contention when several root
   executions are admitted while running executions emit their first journal
   events. Publish expected throughput/latency and provide an admission batching
   or dedicated-writer API if serialization is intentional.

3. **Support dynamic Agent-backed tools.** `AgentToolExecutor` is pleasant for a
   statically declared child Module, but routing `delegate(agent, task)` to an
   alias selected at call time still requires application glue. A runtime-owned
   `AgentRegistry`/router should enforce lineage, depth, capacity and detached Job
   recovery without constructing another root execution manually.

4. **Offer a reconstructable OpenAI-compatible model resolver.** Deferred model
   groups plus SQLite durability require `ModelResourceRef` and a resolver. A
   standard resolver that persists only provider/base URL/model/API-key-env names
   (never secret values) would remove substantial boilerplate and make hot profile
   publication safer.

## Medium priority

5. Expose public serializers for `ToolTask`, `JobSnapshot`, stored executions and
   journal events so HTTP APIs do not depend on dataclass fields and enum details.

6. Add a standard approval-request event helper around `wait_external()`, including
   timeout, duplicate delivery, shutdown and rejected `ToolResult` projection.

7. Let MCP discovery retain both the model-visible name and remote tool name as
   explicit metadata, and provide a registry helper that detects visible-name
   conflicts across local and multiple MCP namespaces.

8. Add a durable internal-tool helper for the common pattern “submit a hidden,
   detached, idempotent ToolCall as an independent Job” without exposing its
   definition to the model.

9. Document sequence/index equivalence for `ExecutionEvent.sequence` and
   `SQLiteHistoryStore.events_after(after=...)`; this is the contract used by SSE
   resume cursors.

10. Expose admission, model, tool and external-wait queue metrics from both
    runtime-instance and deployment-scoped coordinators with the same schema.
