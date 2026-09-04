from __future__ import annotations

import os
import hashlib
import asyncio
import json
import sqlite3
import time
import warnings
from contextvars import ContextVar
from dataclasses import replace
from html import escape
from pathlib import Path
from typing import Any

from pygent import (
    AIMessage,
    Context as PygentContext,
    FallbackPolicy,
    IdempotencyPolicy,
    ModelGroupConfig,
    ModelCallLayer,
    ModelRoute,
    Module,
    PygentAgent,
    ToolAuthorizationDecision,
    ToolAuthorizationRequest,
    ToolKit,
    ToolSideEffect,
    ToolMessage,
    UserMessage,
    freeze_json_object,
    tool,
)
from pygent.agent import (
    REACT_PROJECTION_OPERATION_KIND,
    ReplaceMessageProjection,
    encode_react_projection_operation,
)
from pygent.llm import ModelResourceRef
from pygent.runtime import (
    CapacityPolicy,
    CapacityScope,
    DurabilityMode,
    DurabilityPolicy,
    DurableToolTaskManager,
    ExecutionAdmissionError,
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
    ToolExecutionError,
    ToolSpec,
)
from pygent.tool.executors import ToolExecutionContext
from pygent.tool.mcp import (
    MCPStdioTransport,
    discover_mcp_tools,
    register_mcp_tools,
)

from lora.schema import AgentSession, CaseRunRef, RunConfig
from lora.config import load_run_config
from lora.core.io import aclose_if_supported, plain_object, read_json, write_json
from lora.sessions import SessionManager
from lora.tracing import DIFF_TOOL_SPEC, DiffTool, EventStore
from lora.runtime.reminders import ReminderService
from pygent.runtime.codec import invocation_from_dict, message_to_dict

from .agent import (
    LORA_PROJECTION_READY_KIND,
    LoraAgent,
    _initial_lora_context,
    _to_pygent_message,
)
from .agent.common import DEFAULT_REACT_MAX_STEPS
from .agent.pipeline import checkpoint_conversation_message
from .context import LORA_CONTEXT_CODEC, LoraContext
from .delegation import (
    DELEGATE_BACKGROUND_TOOL_SPEC,
    DELEGATE_TOOL_SPEC,
    visible_delegation_specs,
)
from .deployment import (
    LoraModelResourceResolver,
    WorkspaceToolExecutor,
    migrate_legacy_model_deployments,
)
from .eternal_conversation import (
    EternalConversationHarness,
    MemoryAgentTool,
    load_projection,
    render_memory_snapshot,
)
from .file_effects import FILE_EFFECT_TOOL_SPEC, FileEffectToolExecutor


PYGENT_HISTORY_SCHEMA_VERSION = 7


def resolve_runtime_history_path(path: str | Path, durability_mode: str) -> Path:
    """Choose a fresh journal for incompatible Pygent schemas without deleting history."""

    configured = Path(path)
    version = _sqlite_schema_version(configured)
    if (
        version is None
        or version == PYGENT_HISTORY_SCHEMA_VERSION
        or durability_mode == "required"
    ):
        return configured

    suffix = configured.suffix or ".sqlite3"
    stem = (
        configured.name[: -len(configured.suffix)]
        if configured.suffix
        else configured.name
    )
    base = configured.with_name(
        f"{stem}-schema-v{PYGENT_HISTORY_SCHEMA_VERSION}{suffix}"
    )
    candidate = base
    index = 2
    while _sqlite_schema_version(candidate) not in {
        None,
        PYGENT_HISTORY_SCHEMA_VERSION,
    }:
        candidate = base.with_name(f"{base.stem}-{index}{base.suffix}")
        index += 1
    warnings.warn(
        f"Pygent history at {configured} uses incompatible schema v{version}; "
        f"preserving it and starting with {candidate}",
        RuntimeWarning,
        stacklevel=2,
    )
    return candidate


def _sqlite_schema_version(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro", uri=True
        )
        try:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if not tables:
                return None
            row = connection.execute("PRAGMA user_version").fetchone()
            return 0 if row is None else int(row[0])
        finally:
            connection.close()
    except sqlite3.Error:
        return None


_DELEGATION_DEPTH: ContextVar[int] = ContextVar("lora_delegation_depth", default=0)
RECOVERY_CLAIM_WAIT_SECONDS = 31.0
PROJECTION_OPERATION_METADATA_KEY = "projection_replacement_operation"
PROJECTION_INPUT_ID_METADATA_KEY = "projection_replacement_input_id"
PROJECTION_READY_INPUT_ID_METADATA_KEY = "projection_replacement_ready_input_id"
MAX_IDENTICAL_MEMORY_TOOL_REJECTIONS = 8


class _MemoryFeedbackState:
    def __init__(self) -> None:
        self.signature: tuple[tuple[str, str], ...] | None = None
        self.count = 0


class _MemoryToolFeedbackLayer(Module[AIMessage, ToolMessage]):
    trusted_live_resource_attributes = ("_feedback_state",)

    def __init__(self, tools: Module[AIMessage, ToolMessage]) -> None:
        super().__init__()
        self.tools = tools
        self._feedback_state = _MemoryFeedbackState()

    async def forward(
        self, message: AIMessage, context: Any
    ) -> tuple[ToolMessage, Any]:
        result, next_context = await self.tools(message, context)
        feedback = [
            item.error
            for item in result.results
            if item.error_kind == "validation_error" and item.error
        ]
        if feedback:
            signature = tuple(
                (call.name, repr(call.arguments)) for call in message.tool_calls
            )
            if signature == self._feedback_state.signature:
                self._feedback_state.count += 1
            else:
                self._feedback_state.signature = signature
                self._feedback_state.count = 1
            same_rejection_count = self._feedback_state.count
            if same_rejection_count >= MAX_IDENTICAL_MEMORY_TOOL_REJECTIONS:
                raise RuntimeError(
                    "publish_pending received the identical invalid payload "
                    f"{same_rejection_count} consecutive times; restart only this background "
                    "memory job with the validation failure in previous_publish_failure"
                )
            repeated = (
                f" IDENTICAL INVALID PAYLOAD REJECTION #{same_rejection_count}: "
                "you resent the same arguments. Change the rejected field before the next tool call."
                if same_rejection_count > 1
                else ""
            )
            instruction = (
                "STOP: publish_pending rejected the previous call before any write; "
                "nothing was published. NEVER resend identical arguments. Preserve all "
                "other facts. Snapshot items must be at most 500 characters; move excess "
                "detail into focused UTs. UT content must be at most 700 characters; split "
                "a larger UT across multiple focused UTs. Keep the whole batch within 8 UT "
                "changes, and call "
                "publish_pending again now."
                + repeated
                + " Validation error: "
                + " | ".join(feedback)
            )
            enhanced_results = tuple(
                replace(
                    item,
                    error=f"{instruction}\nOriginal validation error: {item.error}",
                )
                if item.error_kind == "validation_error" and item.error
                else item
                for item in result.results
            )
            result = replace(
                result,
                content="\n".join(filter(None, (result.content, instruction))),
                results=enhanced_results,
            )
        return result, next_context


def _recent_conversation_messages(history: list[dict[str, Any]]) -> tuple[Any, ...]:
    selected = []
    for item in reversed(history):
        role = item.get("role")
        if (
            role == "assistant"
            and not selected
            and item.get("content")
            and not item.get("tool_calls")
        ):
            selected.append(_to_pygent_message(item))
        elif role == "user":
            selected.append(_to_pygent_message(item))
            break
    return tuple(item for item in reversed(selected) if item is not None)


class _DiffExecutor:
    def __init__(self, service: "LoraRuntimeService") -> None:
        self.service = service

    async def execute(
        self, spec: ToolSpec, call: Any, context: ToolExecutionContext
    ) -> object:
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
        return await diff.forward(**plain_object(call.arguments))


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
        reminders: ReminderService | None = None,
        own_reminders: bool | None = None,
    ) -> None:
        self.config = config
        self.reminders = reminders or ReminderService(config)
        self._owns_reminders = (
            reminders is None if own_reminders is None else own_reminders
        )
        history_path = resolve_runtime_history_path(
            config.runtime_durability.history_path,
            config.runtime_durability.mode,
        )
        self.history_path = history_path
        model_path = history_path.with_name("model-deployments-v1.sqlite3")
        self.model_path = model_path
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
        standard = StandardTools(workspace_root=config.workspace_root, restrict_to_workspace=False)
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
                max_children_per_execution=max(
                    1024, (config.max_steps if config.max_steps > 0 else 128) * 16
                ),
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
            run_agent=self._run_memory_agent,
        )
        template = LoraAgent(
            config,
            managed_model=True,
            memory_harness=self.memory_harness,
            reminders=self.reminders,
        )
        self._agent_definitions: dict[tuple[int, bool], LoraAgent] = (
            {(id(config), False): template} if config.resolved_agent is not None else {}
        )
        self._model_invokers: dict[str, Any] = (
            {config.resolved_agent.alias: template.llm}
            if config.resolved_agent is not None and template.llm is not None
            else {}
        )

    async def _execute_delegation(
        self,
        spec: ToolSpec,
        call: Any,
        context: ToolExecutionContext,
    ) -> object:
        arguments = plain_object(call.arguments)
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
            self.history_path,
            Path(self.config.runtime_capacity.coordinator_path),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(migrate_legacy_model_deployments, self.model_path)
        await self.history.open()
        discovered = list(self.external_tools)
        visible_names: set[str] = {
            "bash",
            "read",
            "write",
            "edit",
            "glob",
            "grep",
            "diff",
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
                        raise ValueError(
                            f"duplicate MCP model-visible tool name: {name}"
                        )
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
        if server.transport != "stdio":
            raise ValueError(
                f"MCP transport {server.transport!r} is unsupported by the installed Pygent; "
                "use stdio"
            )
        env = {name: os.environ[name] for name in server.env_from if name in os.environ}
        return MCPStdioTransport(
            server.command,
            args=server.args,
            env=env,
            cwd=server.cwd,
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
        invoker = (
            self._model_invokers.get(resolved.alias) if resolved is not None else None
        )
        agent = LoraAgent(
            effective_config,
            resolved_agent=effective_config.resolved_agent,
            external_tools=self.external_tools,
            managed_model=True,
            interactive_approvals=interactive_approvals,
            model_invoker=invoker,
            memory_harness=self.memory_harness,
            reminders=self.reminders,
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
        run_ref = manager.start_case_run(
            session.session_id, "delegation", run_config=child_config
        )
        turn_id = f"delegate-{parent_call_id}"
        child = self.new_agent(
            interactive_approvals=True,
            config=child_config,
        )
        child_message, child_context = await self._prepare_turn(
            manager=manager,
            run_ref=run_ref,
            message=task,
            config=child_config,
            turn_id=turn_id,
        )
        bound = await self.bind(child, child)
        delegated_context = replace(
            child_context,
            metadata={
                **plain_object(child_context.metadata),
                "parent_tool_call_id": parent_call_id,
            },
        )
        try:
            handle = await self._start_agent_execution(
                bound,
                child_message,
                delegated_context,
                execution=ExecutionOptions(
                    request_id=run_ref.case_run_id,
                    identity=run_ref.session_id,
                    idempotency_key=parent_call_id,
                    deadline=time.monotonic() + 30 * 60,
                ),
            )
        except BaseException:
            await self.reminders.release_initial(run_ref.session_id, turn_id)
            raise
        await self.reminders.acknowledge_initial(
            run_ref.session_id, turn_id, handle.execution_id
        )
        if emit is not None:
            async with handle.subscribe() as events:
                async for event in events:
                    if event.kind == "lora.approval.requested":
                        await emit(event.kind, plain_object(event.data))
        output, _ = await handle.result()
        result = plain_object(plain_object(output.data).get("result"))
        status = str(result.get("status") or "passed")
        manager.finish_case_run(
            run_ref,
            status if status in {"passed", "failed", "error", "skipped"} else "error",
        )
        return {
            "answer": output.content,
            "session_id": run_ref.session_id,
            "case_run_id": run_ref.case_run_id,
            "execution_id": handle.execution_id,
        }

    async def bind(self, module: Any, agent: LoraAgent) -> Any:
        await self.initialize()
        bound = self.binding.bind(module)
        if agent.llm is not None and (
            module is agent or getattr(module, "agent", None) is agent
        ):
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
                            (
                                route.id,
                                route.provider,
                                route.model_name,
                                route.base_url,
                                route.api_key_env,
                            )
                            for route in routes
                        ),
                    )
                ).encode("utf-8")
            ).hexdigest()
            self.model_resolver.register(revision, agent.llm)
            await handle.ensure_profile(
                profile=agent.resolved_agent.profile,
                routes=tuple(
                    ModelRoute(
                        route.id, provider=route.provider, model=route.model_name
                    )
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

    @staticmethod
    async def _deliver_projection_replacement(
        handle: Any, context: LoraContext
    ) -> None:
        operation = context.metadata.get(PROJECTION_OPERATION_METADATA_KEY)
        if operation is None:
            return
        for key, kind, value in (
            (
                PROJECTION_INPUT_ID_METADATA_KEY,
                REACT_PROJECTION_OPERATION_KIND,
                operation,
            ),
            (PROJECTION_READY_INPUT_ID_METADATA_KEY, LORA_PROJECTION_READY_KIND, True),
        ):
            result = await handle.send_input(
                input_id=str(context.metadata[key]), kind=kind, value=value
            )
            if result.status not in {"accepted", "duplicate"}:
                raise RuntimeError(
                    f"native projection input was not delivered: {result.status}"
                )

    async def _start_agent_execution(
        self,
        bound: Any,
        message: UserMessage,
        context: LoraContext,
        *,
        execution: Any,
    ) -> Any:
        handle = await bound.start(message, context, execution=execution)
        await self._deliver_projection_replacement(handle, context)
        return handle

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
        turn_message, turn_context = await self._prepare_turn(
            manager=manager,
            run_ref=run_ref,
            message=message,
            config=self.config,
            turn_id=turn_id,
        )
        bound = await self.bind(agent, agent)
        try:
            handle = await self._start_agent_execution(
                bound,
                turn_message,
                turn_context,
                execution=ExecutionOptions(
                    request_id=run_ref.case_run_id,
                    idempotency_key=run_ref.case_run_id,
                    identity=run_ref.session_id,
                    deadline=deadline
                    if deadline is not None
                    else time.monotonic() + 30 * 60,
                ),
            )
        except BaseException:
            await self.reminders.release_initial(run_ref.session_id, turn_id)
            raise
        await self.reminders.acknowledge_initial(
            run_ref.session_id, turn_id, handle.execution_id
        )
        self._record_execution_id(run_ref, handle.execution_id)
        return handle

    async def recover_turn(
        self,
        execution_id: str,
        *,
        deadline: float,
    ) -> Any:
        """Claim and resume one non-terminal durable Pygent execution."""

        await self.initialize()
        agent = self.new_agent(interactive_approvals=True)
        bound = await self.bind(agent, agent)
        await self.runtime.recover_tool_jobs(bound)
        lease_wait_deadline = min(
            deadline,
            time.monotonic() + RECOVERY_CLAIM_WAIT_SECONDS,
        )
        while True:
            try:
                handle = await self.runtime.recover(
                    bound,
                    execution_id,
                    deadline=deadline,
                )
                stored = await self.history.get_execution(execution_id)
                if stored is None:
                    raise KeyError(f"unknown durable execution {execution_id!r}")
                _, context = invocation_from_dict(
                    stored.input,
                    registry=self.runtime.context_codec_registry,
                )
                if not isinstance(context, LoraContext):
                    raise TypeError(
                        "durable Lora execution has an incompatible context"
                    )
                await self._deliver_projection_replacement(handle, context)
                return handle
            except ExecutionAdmissionError as exc:
                if (
                    "owned by another recovery attempt" not in str(exc)
                    or time.monotonic() >= lease_wait_deadline
                ):
                    raise
                await asyncio.sleep(0.1)

    async def recovery_case_run(self, execution_id: str) -> CaseRunRef:
        """Recover the application run identity stored with a Pygent invocation."""

        await self.initialize()
        stored = await self.history.get_execution(execution_id)
        if stored is None:
            raise KeyError(f"unknown durable execution {execution_id!r}")
        _, context = invocation_from_dict(
            stored.input,
            registry=self.runtime.context_codec_registry,
        )
        if not isinstance(context, LoraContext):
            raise TypeError("durable Lora execution has an incompatible context")
        return context.case_run_ref

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

        store = EventStore(run_ref)
        store.append(
            "case.started",
            actor="system",
            payload={"title": case.title},
            turn_id="turn-0001",
        )
        carry_context = case.session.get("carry_context") is not False
        if not carry_context:
            session.history = []
        inputs = case.input.get("messages")
        messages = (
            [
                str(item.get("content") or "")
                for item in inputs
                if isinstance(item, dict) and item.get("role", "user") == "user"
            ]
            if isinstance(inputs, list)
            else [str(case.input.get("content") or "")]
        )
        messages = [item for item in messages if item]
        current_message = messages[-1] if messages else ""
        checkpoint_context = LoraContext(
            session_id=run_ref.session_id,
            case_id=run_ref.case_id,
            case_run_id=run_ref.case_run_id,
            run_dir=run_ref.run_dir,
            turn_id="turn-0001",
            system_prompt=session.system_prompt,
            metadata={"persist_conversation_history": carry_context},
        )
        for index, prior in enumerate(messages[:-1]):
            prior_message = UserMessage(content=prior)
            await checkpoint_conversation_message(
                self.config,
                checkpoint_context,
                prior_message,
                boundary=f"case-input-{index}",
            )
            if not carry_context:
                session.history.append(message_to_dict(prior_message))
        if carry_context and messages[:-1]:
            session = manager.load(run_ref.session_id)
        agent = self.new_agent(interactive_approvals=False)
        turn_message, turn_context = await self._prepare_turn(
            manager=manager,
            run_ref=run_ref,
            message=current_message,
            config=self.config,
            turn_id="turn-0001",
            session=session,
            carry_context=carry_context,
        )
        bound = await self.bind(agent, agent)
        try:
            handle = await self._start_agent_execution(
                bound,
                turn_message,
                turn_context,
                execution=ExecutionOptions(
                    request_id=run_ref.case_run_id,
                    idempotency_key=run_ref.case_run_id,
                    identity=run_ref.session_id,
                    deadline=time.monotonic() + 30 * 60,
                ),
            )
        except BaseException:
            await self.reminders.release_initial(run_ref.session_id, "turn-0001")
            raise
        await self.reminders.acknowledge_initial(
            run_ref.session_id,
            "turn-0001",
            handle.execution_id,
        )
        output, _ = await handle.result()
        result = plain_object(plain_object(output.data).get("result"))
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

    async def _prepare_turn(
        self,
        *,
        manager: SessionManager,
        run_ref: CaseRunRef,
        message: str,
        config: RunConfig,
        turn_id: str,
        session: AgentSession | None = None,
        carry_context: bool = True,
    ) -> tuple[UserMessage, LoraContext]:
        if session is None:
            session = manager.load(run_ref.session_id)
        memory_projection = (
            load_projection(session.session_dir) if carry_context else {}
        )
        covered_through = int(memory_projection.get("covered_through") or 0)
        lora_context = LoraContext(
            session_id=session.session_id,
            case_id=run_ref.case_id,
            case_run_id=run_ref.case_run_id,
            run_dir=run_ref.run_dir,
            turn_id=turn_id,
            system_prompt=session.system_prompt,
            eternal_memory_enabled=config.eternal_conversation.enabled,
            memory_covered_through=covered_through,
            memory_projection=freeze_json_object(memory_projection),
            raw_history_location=str(
                Path(session.session_dir) / "raw-history" / "events.jsonl"
            ),
        )
        wrapped = "\n".join(
            (
                "<user-context>",
                f"  <user-identity>{escape(config.user_identity or 'default', quote=False)}</user-identity>",
                f"  <user-message>{escape(message, quote=False)}</user-message>",
                "</user-context>",
            )
        )
        reminder = await self.reminders.claim_initial(session.session_id, turn_id)
        dynamic_reminder = await self.reminders.collect_pending(session.session_id)
        if dynamic_reminder:
            reminder = (
                f"{reminder}\n\n{dynamic_reminder}" if reminder else dynamic_reminder
            )
        if reminder:
            wrapped = f"{wrapped}\n\n{reminder}"
        history, _ = _initial_lora_context(
            context=lora_context,
            history=session.history,
            checkpoint=session.metadata.get("agent_context"),
        )
        current = UserMessage(
            content=wrapped,
            kind="lora.chat.turn",
            data={"raw_content": message},
        )
        metadata: dict[str, Any] = {
            "session_id": run_ref.session_id,
            "case_run_id": run_ref.case_run_id,
            "persist_conversation_history": carry_context,
        }
        if (
            config.eternal_conversation.enabled
            and carry_context
            and covered_through > 0
        ):
            snapshot = UserMessage(
                content=render_memory_snapshot(memory_projection),
                kind="lora.memory.snapshot",
                slot="lora.memory.snapshot",
            )
            replacement = ReplaceMessageProjection(
                messages=(
                    snapshot,
                    *_recent_conversation_messages(session.history),
                    current,
                ),
                expected_revision=history.projection_revision + 1,
            )
            identity = (
                f"{run_ref.case_run_id}:memory-projection:"
                f"{memory_projection.get('snapshot_revision', 0)}:{covered_through}"
            )
            metadata.update(
                {
                    "projection_replacement_pending": True,
                    "projection_replacement_message_count": len(replacement.messages),
                    PROJECTION_OPERATION_METADATA_KEY: encode_react_projection_operation(
                        replacement
                    ),
                    PROJECTION_INPUT_ID_METADATA_KEY: identity,
                    PROJECTION_READY_INPUT_ID_METADATA_KEY: f"{identity}:ready",
                }
            )
        execution_context = replace(
            history,
            metadata=metadata,
        )
        return (
            current,
            execution_context,
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

    async def _run_memory_agent(
        self,
        alias: str,
        system_prompt: str,
        request: dict[str, Any],
        memory_tool: MemoryAgentTool,
    ) -> str:
        child_config = load_run_config(
            workspace_root=self.config.workspace_root,
            agent_alias=alias,
        )
        agent = LoraAgent(child_config, managed_model=False)
        try:
            layer = agent.new_model_layer()
            if agent.llm is None:
                raise RuntimeError(
                    f"background memory Agent {alias!r} has no configured model"
                )

            @tool(
                tool_id=f"lora.memory.{memory_tool.name}",
                version="1.0.0",
                side_effect=ToolSideEffect.WRITE,
                idempotency=IdempotencyPolicy.INHERENT,
                name=memory_tool.name,
                description=memory_tool.description,
                timeout=300,
                resource_key="lora-memory",
                required_permissions=("memory:write",),
            )
            async def memory_operation(payload: dict[str, Any]) -> dict[str, Any]:
                try:
                    return await memory_tool.handler(payload)
                except ValueError as exc:
                    # Extractor validation happens before publish-pending writes.
                    # Tell Pygent the failed call is safe to correct and retry;
                    # unclassified CLI failures remain conservatively unknown.
                    raise ToolExecutionError(
                        str(exc),
                        kind="validation_error",
                        code=type(exc).__name__,
                        retryable=True,
                        side_effect_committed=False,
                    ) from exc

            toolkit = ToolKit(memory_operation)

            def authorize_memory_tool(
                request: ToolAuthorizationRequest,
                context: PygentContext,
            ) -> ToolAuthorizationDecision:
                del context
                return ToolAuthorizationDecision(
                    call_id=request.call.call_id,
                    allowed=True,
                    reason_code="lora-background-memory-job",
                )

            model = ModelCallLayer(
                model_group=layer.model_group,
                retry_policy=layer.retry_policy,
                generation=replace(
                    layer.generation,
                    tool_choice="auto",
                ),
                policy=layer.policy,
                tools=toolkit.definitions,
                invoker=agent.llm,
            )
            # This is a native Pygent ReAct Agent. The background job remains
            # independent of the foreground durable execution, while validation
            # failures return through ToolResult and stay in this Agent's context.
            react_agent = PygentAgent(
                system_prompt=system_prompt,
                compression_prompt="Compress the memory job transcript without losing tool errors.",
                model=model,
                compressor=model,
                tools=_MemoryToolFeedbackLayer(
                    toolkit.local_layer(authorization_adapter=authorize_memory_tool)
                ),
                context_window_tokens=1 << 60,
                compression_context_window_tokens=1 << 60,
                max_compressions=1,
                max_steps=DEFAULT_REACT_MAX_STEPS,
                max_model_calls=DEFAULT_REACT_MAX_STEPS,
                max_tool_calls=DEFAULT_REACT_MAX_STEPS,
            )
            answer, _ = await react_agent.invoke(
                UserMessage(content=json.dumps(request, ensure_ascii=False)),
                react_agent.new_context(
                    tools=toolkit.definitions,
                    metadata={"memory_agent_alias": alias},
                ),
            )
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
        for invoker in {
            id(value): value for value in self._model_invokers.values()
        }.values():
            await aclose_if_supported(invoker)
        if self.capacity is not None:
            await self.capacity.close(release_leases=True)
        await self.history.close()
        if self._owns_reminders:
            await self.reminders.close()


__all__ = ["LoraRuntimeService"]
