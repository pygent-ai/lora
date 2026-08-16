from __future__ import annotations

import os
import hashlib
import asyncio
import time
import warnings
from contextvars import ContextVar
from dataclasses import replace
from html import escape
from pathlib import Path
from typing import Any

from pygent import (
    Context as PygentContext,
    FallbackPolicy,
    ModelGroupConfig,
    ModelRoute,
    ToolKit,
    UserMessage,
    freeze_json_object,
    thaw_json,
)
from pygent.llm import ModelResourceRef
from pygent.runtime import (
    CapacityPolicy,
    CapacityScope,
    DurabilityMode,
    DurabilityPolicy,
    DurableToolTaskManager,
    ExecutionCapacityPolicy,
    LocalRuntime,
    SQLiteCapacityCoordinator,
    SQLiteHistoryStore,
    SQLiteModelDeploymentStore,
)
from pygent.tool import (
    AgentToolExecutor,
    ExecutorRegistry,
    LocalToolExecutor,
    SandboxExecutorSupport,
    StandardTools,
    ToolSpec,
)
from pygent.tool.executors import ToolExecutionContext
from pygent.tool.mcp import (
    MCPSseTransport,
    MCPStdioTransport,
    discover_mcp_tools,
    register_mcp_tools,
)

from lora.schema import CaseRunRef, RunConfig
from lora.config import load_run_config
from lora.core.io import read_json, write_json
from lora.sessions import SessionManager
from lora.tracing import DIFF_TOOL_SPEC, DiffTool
from pygent.runtime.codec import invocation_from_dict

from .agent import LoraAgent, _initial_lora_context, _session_dir_for_run, _to_pygent_message
from .context import LORA_CONTEXT_CODEC, LoraContext
from .delegation import (
    DELEGATE_BACKGROUND_TOOL_SPEC,
    DELEGATE_TOOL_SPEC,
    visible_delegation_specs,
)
from .deployment import LoraModelResourceResolver, WorkspaceToolExecutor
from .file_effects import (
    FILE_EFFECT_TOOL_SPEC,
    FileEffectToolExecutor,
)
from .eternal_conversation import EternalConversationHarness, load_projection

_DELEGATION_DEPTH: ContextVar[int] = ContextVar("lora_delegation_depth", default=0)


def _wrap_user_message(message: str, identity: str) -> str:
    return "\n".join(
        (
            "<user-context>",
            f"  <user-identity>{escape(identity or 'default', quote=False)}</user-identity>",
            f"  <user-message>{escape(message, quote=False)}</user-message>",
            "</user-context>",
        )
    )


def _managed_child_budget(config: RunConfig) -> int:
    """Bound the full managed module graph, not just delegated agents."""

    max_steps = config.max_steps if config.max_steps > 0 else 128
    return max(1024, max_steps * 16)


class _DiffExecutor:
    def __init__(self, service: "LoraRuntimeService") -> None:
        self.service = service

    async def execute(self, spec: ToolSpec, call: Any, context: ToolExecutionContext) -> object:
        del spec
        if context.execution_id is None:
            raise RuntimeError("managed diff execution requires an execution id")
        record = await self.service.history.get_execution(context.execution_id)
        if record is None:
            raise RuntimeError("managed diff execution record is unavailable")
        _, execution_context = invocation_from_dict(
            record.input,
            registry=self.service.runtime.context_codec_registry,
        )
        if not isinstance(execution_context, LoraContext):
            raise TypeError("managed diff execution requires LoraContext")
        diff = DiffTool(
            case_run_ref=execution_context.case_run_ref,
            workspace_root=self.service.config.workspace_root,
            turn_id=execution_context.turn_id,
        )
        return await diff.forward(**dict(thaw_json(call.arguments)))


class LoraRuntimeService:
    """Workspace-scoped owner of Pygent execution and deployment resources."""

    def __init__(
        self,
        config: RunConfig,
        *,
        max_live_executions: int = 32,
        max_runnable_executions: int = 4,
        max_queue_size: int = 64,
        model_max_concurrency: int = 4,
        tool_max_concurrency: int = 8,
    ) -> None:
        self.config = config
        history_path = Path(config.runtime_durability.history_path)
        model_path = history_path.with_name("model-deployments-v1.sqlite3")
        self.history = SQLiteHistoryStore(history_path)
        self.model_store = SQLiteModelDeploymentStore(model_path)
        self.capacity = (
            SQLiteCapacityCoordinator(config.runtime_capacity.coordinator_path)
            if config.runtime_capacity.scope == "deployment"
            else None
        )
        self.executor_registry = ExecutorRegistry()
        self.runtime = LocalRuntime(
            history=self.history,
            capacity_coordinator=self.capacity,
            model_deployment_store=self.model_store,
            deployment_namespace=str(Path(config.workspace_root).resolve()),
            context_codecs=(LORA_CONTEXT_CODEC,),
        )
        self.runtime.attach_executor_registry(self.executor_registry)
        self._diff_executor = _DiffExecutor(self)
        standard = StandardTools(workspace_root=config.workspace_root)
        ToolKit(
            standard.bash.bash,
            standard.files.read,
            standard.files.write,
            standard.files.edit,
            standard.files.glob,
            standard.files.grep,
        ).register_into_runtime(
            self.runtime,
            executor_factory=lambda spec, handler: (
                WorkspaceToolExecutor(handler)
                if spec.sandbox_profile == "workspace"
                else LocalToolExecutor(handler)
            ),
        )
        self.runtime.register_tool(FILE_EFFECT_TOOL_SPEC, FileEffectToolExecutor())
        self.runtime.register_tool(DIFF_TOOL_SPEC, self._diff_executor)
        delegation_executor = AgentToolExecutor(invoke=self._execute_delegation)
        setattr(
            delegation_executor,
            "sandbox_support",
            SandboxExecutorSupport(
                profiles=("agent",),
                durable_reconnect=True,
                deployment_fingerprint="lora:delegation:v1",
            ),
        )
        for spec in (DELEGATE_TOOL_SPEC, DELEGATE_BACKGROUND_TOOL_SPEC):
            self.runtime.register_tool(spec, delegation_executor)
        self.task_manager = DurableToolTaskManager(self.history, self.executor_registry)
        self.runtime.attach_tool_task_manager(self.task_manager)
        self.model_resolver = LoraModelResourceResolver()
        self.runtime.register_model_resource_resolver(self.model_resolver)
        scope = (
            CapacityScope.DEPLOYMENT
            if config.runtime_capacity.scope == "deployment"
            else CapacityScope.RUNTIME_INSTANCE
        )
        self.binding = self.runtime.create_binding(
            name="lora-agent",
            execution_capacity=ExecutionCapacityPolicy(
                scope=scope,
                max_live_executions=max_live_executions,
                max_runnable_executions=max_runnable_executions,
                max_queue_size=max_queue_size,
                max_waiters=max_live_executions + max_queue_size,
                max_child_depth=max(8, config.delegation.max_depth + 2),
                max_children_per_execution=_managed_child_budget(config),
                max_external_wait_seconds=config.runtime_approvals.timeout_seconds,
            ),
            model_capacity=CapacityPolicy.limited(
                max_concurrency=model_max_concurrency,
                max_queue_size=max_queue_size,
                capacity_key="lora-api-model",
                scope=scope,
            ),
            tool_capacity=CapacityPolicy.limited(
                max_concurrency=tool_max_concurrency,
                max_queue_size=max_queue_size,
                capacity_key="lora-api-tool",
                scope=scope,
            ),
            durability=DurabilityPolicy(DurabilityMode(config.runtime_durability.mode)),
        )
        self.external_tools: tuple[ToolSpec, ...] = visible_delegation_specs(config)
        self.warnings: list[str] = []
        self._delegation_slots = asyncio.Semaphore(config.delegation.max_parallel)
        self._initialized = False
        self._initializing_task: asyncio.Task[None] | None = None
        self._closed = False
        self.memory_harness = EternalConversationHarness(
            config.eternal_conversation,
            call_agent=self._call_memory_agent,
        )
        template = LoraAgent(config, managed_model=True, memory_harness=self.memory_harness)
        self._agent_definitions: dict[tuple[int, bool], LoraAgent] = {
            (id(config), False): template
        } if config.resolved_agent is not None else {}
        self._model_invokers: dict[str, Any] = {
            config.resolved_agent.alias: template.llm
        } if config.resolved_agent is not None and template.llm is not None else {}

    async def _execute_delegation(
        self,
        spec: ToolSpec,
        call: Any,
        context: ToolExecutionContext,
    ) -> object:
        arguments = thaw_json(call.arguments)
        return await self.run_delegated(
            agent_alias=str(arguments["agent"]),
            task=str(arguments["task"]),
            parent_call_id=call.call_id,
            background=spec.definition.name == "delegate_background",
            emit=context.emit,
        )

    async def initialize(self) -> None:
        if self._closed:
            raise RuntimeError("LoraRuntimeService is closed")
        if self._initialized:
            return
        if self._initializing_task is None:
            self._initializing_task = asyncio.create_task(self._initialize_once())
        try:
            await asyncio.shield(self._initializing_task)
        except BaseException:
            if self._initializing_task.done():
                self._initializing_task = None
            raise

    async def _initialize_once(self) -> None:
        for path in (
            Path(self.config.runtime_durability.history_path),
            Path(self.config.runtime_capacity.coordinator_path),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
        await self.history.open()
        discovered = list(self.external_tools)
        visible_names: set[str] = {
            "bash", "read", "write", "edit", "glob", "grep", "diff",
            *(spec.definition.name for spec in discovered),
        }
        for server in self.config.mcp_servers:
            try:
                transport = self._mcp_transport(server)
                specs = await discover_mcp_tools(
                    transport,
                    namespace=server.name,
                    timeout=server.timeout,
                )
                for spec in specs:
                    name = spec.definition.name
                    if name in visible_names:
                        raise ValueError(f"duplicate MCP model-visible tool name: {name}")
                    visible_names.add(name)
                register_mcp_tools(self.executor_registry, transport, specs)
                discovered.extend(specs)
            except Exception as exc:
                if server.required:
                    raise RuntimeError(
                        f"required MCP server {server.name!r} failed: {exc}"
                    ) from exc
                message = f"optional MCP server {server.name!r} skipped: {exc}"
                self.warnings.append(message)
                warnings.warn(message, RuntimeWarning, stacklevel=2)
        self.external_tools = tuple(discovered)
        self._initialized = True

    def _mcp_transport(self, server: Any) -> Any:
        if server.transport == "stdio":
            env = {name: os.environ[name] for name in server.env_from if name in os.environ}
            return MCPStdioTransport(
                server.command,
                args=server.args,
                env=env,
                cwd=server.cwd,
            )
        headers = {
            header: os.environ[env_name]
            for header, env_name in server.headers_env.items()
            if env_name in os.environ
        }
        return MCPSseTransport(
            server.url,
            headers=headers,
            timeout=server.timeout,
            sse_read_timeout=max(server.timeout, 300.0),
        )

    def new_agent(
        self,
        *,
        interactive_approvals: bool,
        config: RunConfig | None = None,
    ) -> LoraAgent:
        effective_config = config or self.config
        resolved = effective_config.resolved_agent
        if resolved is None:
            raise ValueError("resolved agent configuration is required")
        key = (id(effective_config), interactive_approvals)
        existing = self._agent_definitions.get(key)
        if existing is not None:
            return existing
        invoker = self._model_invokers.get(resolved.alias) if resolved is not None else None
        agent = LoraAgent(
            effective_config,
            resolved_agent=effective_config.resolved_agent,
            external_tools=self.external_tools,
            managed_model=True,
            interactive_approvals=interactive_approvals,
            model_invoker=invoker,
            memory_harness=self.memory_harness,
        )
        if resolved is not None and agent.llm is not None:
            self._model_invokers.setdefault(resolved.alias, agent.llm)
        self._agent_definitions[key] = agent
        return agent

    async def run_delegated(
        self,
        *,
        agent_alias: str,
        task: str,
        parent_call_id: str,
        background: bool,
        emit: Any | None,
    ) -> dict[str, Any]:
        allowed = self.config.delegation.allowed_agents
        if agent_alias not in allowed:
            raise PermissionError(f"delegation to agent {agent_alias!r} is not allowed")
        if background and not self.config.delegation.background_enabled:
            raise PermissionError("background delegation is disabled")
        depth = _DELEGATION_DEPTH.get()
        if depth >= self.config.delegation.max_depth:
            raise RuntimeError("delegation depth limit exceeded")
        token = _DELEGATION_DEPTH.set(depth + 1)
        try:
            async with self._delegation_slots:
                return await self._run_delegated_turn(
                    agent_alias=agent_alias,
                    task=task,
                    parent_call_id=parent_call_id,
                    emit=emit,
                )
        finally:
            _DELEGATION_DEPTH.reset(token)

    async def _run_delegated_turn(
        self,
        *,
        agent_alias: str,
        task: str,
        parent_call_id: str,
        emit: Any | None,
    ) -> dict[str, Any]:
        from pygent.runtime import ExecutionOptions

        child_config = load_run_config(
            workspace_root=self.config.workspace_root,
            agent_alias=agent_alias,
        )
        manager = SessionManager(child_config)
        session = manager.create(case_id="delegation", mode="agent")
        run_ref = manager.start_case_run(session.session_id, "delegation", run_config=child_config)
        turn_id = f"delegate-{parent_call_id}"
        child = self.new_agent(
            interactive_approvals=True,
            config=child_config,
        )
        child_message, child_context = self._prepare_turn(
            agent=child,
            manager=manager,
            run_ref=run_ref,
            message=task,
            config=child_config,
            turn_id=turn_id,
        )
        bound = await self.bind(child, child)
        handle = await bound.start(
            child_message,
            replace(child_context, metadata={"parent_tool_call_id": parent_call_id}),
            execution=ExecutionOptions(
                request_id=run_ref.case_run_id,
                identity=run_ref.session_id,
                idempotency_key=parent_call_id,
                deadline=time.monotonic() + 30 * 60,
            ),
        )
        if emit is not None:
            async with handle.subscribe() as events:
                async for event in events:
                    if event.kind == "lora.approval.requested":
                        await emit(event.kind, dict(thaw_json(event.data)))
        output, _ = await handle.result()
        result = dict(thaw_json(output.data).get("result") or {})
        status = str(result.get("status") or "passed")
        manager.finish_case_run(run_ref, status if status in {"passed", "failed", "error", "skipped"} else "error")
        return {
            "answer": output.content,
            "session_id": run_ref.session_id,
            "case_run_id": run_ref.case_run_id,
            "execution_id": handle.execution_id,
        }

    async def bind(self, module: Any, agent: LoraAgent) -> Any:
        await self.initialize()
        bound = self.binding.bind(module)
        if agent.llm is not None and (module is agent or getattr(module, "agent", None) is agent):
            requirement = ModelGroupConfig.deferred(
                name=f"lora:{agent.resolved_agent.alias}",
                capacity_key="lora-chat-model",
            )
            handle = bound.model_groups.get(requirement)
            routes = agent._resolved_routes()
            revision = hashlib.sha256(
                repr(
                    (
                        agent.resolved_agent.profile,
                        tuple(
                            (route.id, route.provider, route.model_name, route.base_url, route.api_key_env)
                            for route in routes
                        ),
                    )
                ).encode("utf-8")
            ).hexdigest()
            self.model_resolver.register(revision, agent.llm)
            await handle.ensure_profile(
                profile=agent.resolved_agent.profile,
                routes=tuple(
                    ModelRoute(route.id, provider=route.provider, model=route.model_name)
                    for route in routes
                ),
                fallback=FallbackPolicy(
                    agent.resolved_agent.fallback or tuple(route.id for route in routes)
                ),
                invoker=agent.llm,
                resource_ref=ModelResourceRef(
                    resolver_id=self.model_resolver.resolver_id,
                    resource_id=f"lora:{agent.resolved_agent.alias}",
                    revision=revision,
                    capacity_owner_id=f"lora:{Path(self.config.workspace_root).resolve()}",
                    coordinator_domain=str(Path(self.config.workspace_root).resolve()),
                ),
                make_default=True,
                deadline=time.monotonic() + 60,
            )
        return bound

    async def start_turn(
        self,
        *,
        manager: SessionManager,
        message: str,
        run_ref: CaseRunRef,
        turn_id: str,
        interactive_approvals: bool,
        deadline: float | None = None,
    ) -> Any:
        from pygent.runtime import ExecutionOptions

        await self.initialize()
        agent = self.new_agent(interactive_approvals=interactive_approvals)
        turn_message, turn_context = self._prepare_turn(
            agent=agent,
            manager=manager,
            run_ref=run_ref,
            message=message,
            config=self.config,
            turn_id=turn_id,
        )
        bound = await self.bind(agent, agent)
        handle = await bound.start(
            turn_message,
            turn_context,
            execution=ExecutionOptions(
                request_id=run_ref.case_run_id,
                idempotency_key=run_ref.case_run_id,
                identity=run_ref.session_id,
                deadline=deadline if deadline is not None else time.monotonic() + 30 * 60,
            ),
        )
        self._record_execution_id(run_ref, handle.execution_id)
        return handle

    async def execute_case(
        self,
        *,
        manager: SessionManager,
        session: Any,
        case: Any,
        run_ref: CaseRunRef,
    ) -> dict[str, Any]:
        from pygent.runtime import ExecutionOptions

        await self.initialize()
        from lora.tracing import EventStore

        store = EventStore(run_ref)
        store.append("case.started", actor="system", payload={"title": case.title}, turn_id="turn-0001")
        carry_context = case.session.get("carry_context") is not False
        original_history = list(session.history)
        if not carry_context:
            session.history = []
        inputs = case.input.get("messages")
        messages = (
            [str(item.get("content") or "") for item in inputs if isinstance(item, dict) and item.get("role", "user") == "user"]
            if isinstance(inputs, list)
            else [str(case.input.get("content") or "")]
        )
        messages = [item for item in messages if item]
        current_message = messages[-1] if messages else ""
        for prior in messages[:-1]:
            session.history.append({"role": "user", "content": prior})
            store.append("conversation.user_message", actor="user", payload={"role": "user", "content": prior}, turn_id="turn-0001")
        agent = self.new_agent(interactive_approvals=False)
        turn_message, turn_context = self._prepare_turn(
            agent=agent,
            manager=manager,
            run_ref=run_ref,
            message=current_message,
            config=self.config,
            turn_id="turn-0001",
            session=session,
        )
        bound = await self.bind(agent, agent)
        handle = await bound.start(
            turn_message,
            turn_context,
            execution=ExecutionOptions(
                request_id=run_ref.case_run_id,
                idempotency_key=run_ref.case_run_id,
                identity=run_ref.session_id,
                deadline=time.monotonic() + 30 * 60,
            ),
        )
        output, _ = await handle.result()
        result = dict(thaw_json(output.data).get("result") or {})
        if not carry_context:
            session.history = [*original_history, *session.history]
            manager.save(session)
        result["event_count"] = len(store.list_by_run())
        store.append(
            "case.finished",
            actor="system",
            payload={"status": result.get("status"), "error": result.get("error")},
            turn_id="turn-0001",
        )
        result["runtime_execution_id"] = handle.execution_id
        self._record_execution_id(run_ref, handle.execution_id)
        return result

    @staticmethod
    def _prepare_turn(
        *,
        agent: LoraAgent,
        manager: SessionManager,
        run_ref: CaseRunRef,
        message: str,
        config: RunConfig,
        turn_id: str,
        session: Any | None = None,
    ) -> tuple[UserMessage, LoraContext]:
        session = session or manager.load(run_ref.session_id)
        memory_projection = load_projection(session.session_dir)
        covered_through = int(memory_projection.get("covered_through") or 0)
        portable_history = (
            session.history[covered_through:]
            if config.eternal_conversation.enabled
            else session.history
        )
        full_history = tuple(
            converted
            for item in portable_history
            if (converted := _to_pygent_message(item)) is not None
        )
        lora_context = LoraContext(
            session_id=session.session_id,
            session_status=session.status,
            case_id=run_ref.case_id,
            case_run_id=run_ref.case_run_id,
            run_dir=run_ref.run_dir,
            turn_id=turn_id,
            system_prompt=session.system_prompt,
            full_history=full_history,
            eternal_memory_enabled=config.eternal_conversation.enabled,
            memory_covered_through=covered_through,
            memory_projection=freeze_json_object(memory_projection),
            raw_history_location=str(Path(session.session_dir) / "raw-history" / "events.jsonl"),
        )
        wrapped = _wrap_user_message(message, config.user_identity)
        reminder = agent.render_initial_user_reminder(lora_context)
        if reminder:
            wrapped = f"{wrapped}\n\n{reminder}"
        history, _ = _initial_lora_context(
            context=lora_context,
            session_dir=_session_dir_for_run(Path(run_ref.run_dir)),
        )
        return (
            UserMessage(content=wrapped, kind="lora.chat.turn", data={"raw_content": message}),
            replace(
                history,
                metadata={"session_id": run_ref.session_id, "case_run_id": run_ref.case_run_id},
            ),
        )

    @staticmethod
    def _record_execution_id(run_ref: CaseRunRef, execution_id: str) -> None:
        path = Path(run_ref.run_dir) / "run_metadata.json"
        metadata = read_json(path, default={})
        metadata["runtime_execution_id"] = execution_id
        write_json(path, metadata)

    async def deliver_approval(
        self, approval_id: str, *, approved: bool, comment: str = ""
    ) -> bool:
        return await self.runtime.deliver_external(
            kind="tool-approval",
            key=approval_id,
            value={"approved": approved, "comment": comment},
        )

    async def _call_memory_agent(self, alias: str, system_prompt: str, request: str) -> str:
        child_config = load_run_config(
            workspace_root=self.config.workspace_root,
            agent_alias=alias,
        )
        agent = LoraAgent(child_config, managed_model=False)
        try:
            layer = agent.new_model_layer()
            if agent.llm is None:
                raise RuntimeError(f"background memory Agent {alias!r} has no configured model")
            # Background memory work is intentionally independent of a foreground
            # Runtime execution. Invoke the provider operation directly instead of
            # calling a Module child from an unregistered asyncio task.
            execution = agent.llm.execute(
                model_group=layer.model_group,
                retry_policy=layer.retry_policy,
                generation=replace(
                    layer.generation,
                    tool_choice="none",
                    max_output_tokens=8192,
                ),
                message=UserMessage(content=request),
                context=PygentContext(system_prompt=system_prompt),
                tools=(),
            )
            answer = (await execution.result()).message
            if answer.tool_calls:
                raise RuntimeError("background memory Agent attempted a tool call")
            return answer.content
        finally:
            await agent.aclose()

    async def get_task(self, task_id: str) -> Any:
        return await self.runtime.get_tool_task(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        return await self.runtime.cancel_tool_task(task_id)

    async def close(self, *, cancel: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        await self.memory_harness.close()
        await self.runtime.close(cancel=cancel)
        for invoker in {id(value): value for value in self._model_invokers.values()}.values():
            close = getattr(invoker, "aclose", None)
            if callable(close):
                await close()
        if self.capacity is not None:
            await self.capacity.close(release_leases=True)
        await self.history.close()


__all__ = ["LoraRuntimeService"]
