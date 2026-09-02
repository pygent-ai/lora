from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from pygent import (
    Agent,
    AIMessage,
    ExponentialBackoff,
    FallbackPolicy,
    GenerationConfig,
    ModelCallLayer,
    ModelErrorKind,
    ModelGroupConfig,
    ModelRoute,
    PygentAgent,
    RetryPolicy,
    ToolCallLayer,
    ToolDefinition,
    ToolKit,
    UserMessage,
    freeze_json_object,
)
from pygent.llm import (
    DefaultModelInvoker,
    ModelProviderCapabilities,
    OpenAICompatibleAdapter,
    OpenAICompatibleClient,
)
from pygent.tool import StandardTools, ToolSpec
from pygent.core import EffectSafety, ExecutionRequirements, RecoverySafety
from pygent.runtime.codec import context_to_dict

from lora.core.io import aclose_if_supported, plain_data
from lora.schema import ModelRouteConfig, ResolvedAgentConfig, RunConfig
from lora.sessions import SessionManager
from lora.tracing import DIFF_TOOL_SPEC, EventStore
from lora.runtime.context import LORA_CONTEXT_CODECS, LoraContext
from lora.runtime.context_compression import COMPRESSION_REQUEST_PROMPT
from lora.runtime.eternal_conversation import EternalConversationHarness
from lora.runtime.file_effects import FILE_EFFECT_TOOL_SPEC
from lora.runtime.reminders import ReminderService
from .common import DEFAULT_REACT_MAX_STEPS

from .compressor import LoraCompressorModule
from .pipeline import (
    ConversationCheckpointModelModule,
    ContextSnapshotModelModule,
    ConversationCheckpointToolModule,
    DynamicPromptModule,
    LoraToolAuthorization,
    PersistedDiffModule,
    ForegroundModelModule,
    PreparedToolModule,
    RepeatedToolCallGuardModule,
    SystemReminderModule,
    ToolAuditModule,
    checkpoint_conversation_message,
    _model_tool_definition,
)
from .prompts import PromptRegistry


DISABLED_COMPRESSION_WINDOW_TOKENS = 1 << 60
MAX_CONTEXT_COMPRESSIONS = 128
MODEL_RETRYABLE_ERROR_KINDS = (
    ModelErrorKind.TIMEOUT,
    ModelErrorKind.RATE_LIMIT,
    ModelErrorKind.UNAVAILABLE,
    ModelErrorKind.INVALID_RESPONSE,
)
PYGENT_VERIFY_SSL_ENV = "PYGENT_VERIFY_SSL"
LORA_PROJECTION_READY_KIND = "lora.react.projection.ready.v1"


def _verify_ssl_from_env() -> bool | None:
    raw_value = os.environ.get(PYGENT_VERIFY_SSL_ENV)
    if raw_value is None or not raw_value.strip():
        return None
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{PYGENT_VERIFY_SSL_ENV} must be one of: 1, 0, true, false, yes, no, on, off"
    )


class _DeepSeekAdapter(OpenAICompatibleAdapter):
    """Use DeepSeek's documented non-thinking mode for bounded agent operations."""

    def build_request(self, request: Any) -> Any:
        body = super().build_request(request).to_dict()
        body["thinking"] = {"type": "disabled"}
        return freeze_json_object(body)


def _route_supports_streaming(route: Any) -> bool:
    """DeepSeek can emit malformed JSON fragments for long streamed tool arguments."""
    return "api.deepseek.com" not in route.base_url.lower()


def _preferred_model_route(config: ResolvedAgentConfig) -> ModelRouteConfig:
    routes_by_id = {route.id: route for route in config.routes}
    return routes_by_id[config.fallback[0]]


def _model_route_trace_payload(
    route: ModelRouteConfig | None,
) -> dict[str, str | None]:
    if route is None:
        return {
            "model_name": None,
            "model_route": None,
            "model_base_url": None,
            "api_key_source": None,
        }
    return {
        "model_name": route.model_name,
        "model_route": route.id,
        "model_base_url": route.base_url,
        "api_key_source": route.api_key_source,
    }


def _actual_model_route(
    config: ResolvedAgentConfig,
    answer: AIMessage | None,
) -> ModelRouteConfig | None:
    if answer is None:
        return None
    route_id = dict(answer.metadata).get("route_id")
    if not isinstance(route_id, str):
        return None
    return next((route for route in config.routes if route.id == route_id), None)


class LoraForegroundAgent(PygentAgent):
    """Pygent foreground Agent with Lora's verified recovery declaration."""

    execution_requirements = ExecutionRequirements(
        recovery_safety=RecoverySafety.MODULE_BOUNDARY_RETRY,
        effect_safety=EffectSafety.MANAGED_EFFECTS,
    )


class LoraAgent(Agent[UserMessage, AIMessage]):
    execution_requirements = ExecutionRequirements(
        recovery_safety=RecoverySafety.MODULE_BOUNDARY_RETRY,
        effect_safety=EffectSafety.MANAGED_EFFECTS,
    )
    trusted_live_resource_attributes = (
        "config",
        "resolved_agent",
        "prompt_registry",
        "workspace_root",
        "llm",
        "_standard_tools",
        "_toolkit",
        "_external_tools",
        "memory_harness",
        "reminders",
    )

    def __init__(
        self,
        config: RunConfig,
        resolved_agent: ResolvedAgentConfig | None = None,
        prompt_registry: PromptRegistry | None = None,
        external_tools: tuple[ToolSpec, ...] = (),
        managed_model: bool = False,
        interactive_approvals: bool = False,
        model_invoker: Any | None = None,
        memory_harness: EternalConversationHarness | None = None,
        reminders: ReminderService | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        resolved = resolved_agent or config.resolved_agent
        if resolved is None:
            raise ValueError("LoraAgent requires a resolved routes-based agent configuration")
        self.resolved_agent: ResolvedAgentConfig = resolved
        self.prompt_registry = prompt_registry
        self.managed_model = managed_model
        self.interactive_approvals = interactive_approvals
        self.workspace_root = Path(config.workspace_root)
        self.llm = model_invoker or (
            self._build_model_invoker()
            if any(route.api_key for route in self.resolved_agent.routes)
            else None
        )
        self._standard_tools: StandardTools | None = None
        self._toolkit: ToolKit | None = None
        self._external_tools = external_tools
        self.memory_harness = memory_harness
        self.reminders = reminders or ReminderService(config)
        self._owns_reminders = reminders is None
        self._register_default_tools()
        self._assemble_definition()

    @property
    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        local = self._toolkit.definitions if self._toolkit is not None else ()
        return (
            *(_model_tool_definition(definition) for definition in local),
            DIFF_TOOL_SPEC.definition,
            *(spec.definition for spec in self._external_tools),
        )

    def _build_model_invoker(self) -> DefaultModelInvoker:
        routes = self._resolved_routes()
        verify_ssl = _verify_ssl_from_env()
        providers = {route.provider for route in routes}
        adapters = {}
        for provider in providers:
            provider_routes = [route for route in routes if route.provider == provider]
            adapters[provider] = (
                _DeepSeekAdapter()
                if provider_routes
                and all("api.deepseek.com" in route.base_url.lower() for route in provider_routes)
                else OpenAICompatibleAdapter()
            )
        return DefaultModelInvoker(
            adapters=adapters,
            clients={
                route.id: OpenAICompatibleClient(
                    base_url=route.base_url,
                    api_key=route.api_key or "",
                    verify_ssl=verify_ssl,
                )
                for route in routes
            },
            capabilities={
                route.id: ModelProviderCapabilities(streaming=_route_supports_streaming(route)) for route in routes
            },
        )

    def _resolved_routes(self) -> tuple[Any, ...]:
        return self.resolved_agent.routes

    def new_model_layer(self) -> ModelCallLayer:
        if self.llm is None:
            raise RuntimeError("model invoker is not configured")
        routes = self._resolved_routes()
        group = (
            ModelGroupConfig.deferred(
                name=f"lora:{self.resolved_agent.alias}",
                capacity_key="lora-chat-model",
            )
            if self.managed_model
            else ModelGroupConfig(
                name=f"lora:{self.resolved_agent.alias}",
                routes=tuple(
                    ModelRoute(route.id, provider=route.provider, model=route.model_name)
                    for route in routes
                ),
                fallback=FallbackPolicy(
                    self.resolved_agent.fallback or tuple(route.id for route in routes)
                ),
                max_concurrency=None,
                capacity_key="lora-chat-model",
            )
        )
        retry = self.resolved_agent.retry
        return ModelCallLayer(
            model_group=group,
            retry_policy=RetryPolicy(
                max_attempts_per_route=retry.max_attempts_per_route,
                retry_on=MODEL_RETRYABLE_ERROR_KINDS,
                attempt_idle_timeout_seconds=retry.attempt_idle_timeout_seconds,
                backoff=ExponentialBackoff(
                    initial=retry.backoff_initial,
                    maximum=retry.backoff_maximum,
                    multiplier=retry.backoff_multiplier,
                ),
            ),
            generation=GenerationConfig(
                tool_choice="auto",
            ),
            tools=self.tool_definitions,
            invoker=None if self.managed_model else self.llm,
        )

    def new_tool_layer(self, *, max_concurrency: int = 8) -> ToolCallLayer:
        if self._toolkit is None:
            raise RuntimeError("LoraAgent run graph has not been assembled")
        return ToolCallLayer(
            tools=(*self._toolkit.specs, DIFF_TOOL_SPEC, *self._external_tools),
            authorization=LoraToolAuthorization(
                enabled=self.config.runtime_approvals.enabled,
                timeout_seconds=self.config.runtime_approvals.timeout_seconds,
                preauthorized_tools=self.config.runtime_approvals.preauthorized_tools,
                interactive=self.interactive_approvals,
                scope_key="lora",
                detached_tools=("delegate_background",),
            ),
            max_concurrency=max_concurrency,
        )

    def _assemble_definition(self) -> None:
        self.prompt = DynamicPromptModule(self.config, self.prompt_registry)
        if self.llm is not None:
            diff_tasks = ToolCallLayer(
                tools=(FILE_EFFECT_TOOL_SPEC,),
                authorization=LoraToolAuthorization(
                    enabled=False,
                    timeout_seconds=self.config.runtime_approvals.timeout_seconds,
                    preauthorized_tools=(),
                    interactive=False,
                    scope_key="lora-file-effects",
                ),
            )
            model = self.new_model_layer()
            compression_model = self.new_model_layer()
            prepared_model = ConversationCheckpointModelModule(
                self.config,
                ContextSnapshotModelModule(ForegroundModelModule(model=model)),
            )
            prepared_tools = ConversationCheckpointToolModule(
                self.config,
                RepeatedToolCallGuardModule(
                    PreparedToolModule(
                        tools=self.new_tool_layer(),
                        audit=ToolAuditModule(self.config),
                        reminders=SystemReminderModule(self.reminders),
                        persisted_diff=PersistedDiffModule(self.workspace_root, diff_tasks),
                    ),
                ),
            )
            context_window = self.config.context_window
            compression_enabled = (
                self.config.context_compression_enabled
                and not self.config.eternal_conversation.enabled
                and isinstance(context_window, int)
            )
            context_window_tokens = context_window if isinstance(context_window, int) and compression_enabled else DISABLED_COMPRESSION_WINDOW_TOKENS
            max_steps = (
                self.config.max_steps
                if self.config.max_steps > 0
                else DEFAULT_REACT_MAX_STEPS
            )
            self.foreground = LoraForegroundAgent(
                system_prompt="Lora supplies the execution system prompt in LoraContext.",
                compression_prompt=COMPRESSION_REQUEST_PROMPT,
                model=prepared_model,
                compressor=LoraCompressorModule(self.config, compression_model),
                tools=prepared_tools,
                context_window_tokens=context_window_tokens,
                compression_trigger_ratio=min(self.config.context_compression_trigger_ratio, 0.999_999),
                compression_context_window_tokens=context_window_tokens,
                max_compressions=MAX_CONTEXT_COMPRESSIONS,
                max_steps=max_steps,
                max_model_calls=max_steps,
                max_tool_calls=max(32, max_steps * 4),
            )
            self.foreground.react.model.execution_requirements = LoraForegroundAgent.execution_requirements

    async def forward(
        self,
        message: UserMessage,
        context: LoraContext,
    ) -> tuple[AIMessage, LoraContext]:
        if not isinstance(context, LoraContext):
            raise TypeError("LoraAgent requires LoraContext")
        manager = SessionManager(self.config)
        store = EventStore(context.case_run_ref)
        turn_id = context.turn_id
        message_data = plain_data(message.data)
        raw_content = str(
            (message_data.get("raw_content") if isinstance(message_data, dict) else None)
            or message.content
        )
        await checkpoint_conversation_message(
            self.config,
            context,
            message,
            boundary="user-input",
        )
        projection_pending = context.metadata.get("projection_replacement_pending") is True
        if projection_pending:
            while not await self.receive_execution_inputs(
                kinds=(LORA_PROJECTION_READY_KIND,),
                limit=1,
                seal_if_empty=False,
            ):
                await asyncio.sleep(0)
        execution_context = context
        await self.emit(kind="lora.chat.started", data=context.case_run_ref.to_dict())
        replacement_count = context.metadata.get(
            "projection_replacement_message_count",
            len(context.messages) + 1,
        )
        if not isinstance(replacement_count, int):
            replacement_count = len(context.messages) + 1
        preferred_route = _preferred_model_route(self.resolved_agent)
        store.append(
            "model.request",
            actor="system",
            payload={
                "agent": type(self).__name__,
                "agent_alias": self.resolved_agent.alias,
                **_model_route_trace_payload(preferred_route),
                "model_fallback": list(self.resolved_agent.fallback),
                "max_steps": self.config.max_steps,
                "history_message_count": replacement_count,
                "latest_user_input": raw_content,
            },
            turn_id=turn_id,
        )
        status = "passed"
        error: str | None = None
        answer: AIMessage | None = None
        try:
            visible = replace(execution_context, tools=self.tool_definitions)
            if self.llm is None:
                message, visible = await self.prompt(message, visible)
                answer = AIMessage(
                    content=(
                        "Lora agent is wired into chat, but API key is not configured "
                        f"for agent alias {self.resolved_agent.alias!r}."
                    )
                )
                next_context = visible + message + answer
                await checkpoint_conversation_message(
                    self.config,
                    next_context,
                    answer,
                    boundary="local-answer",
                )
            else:
                message, visible = await self.prompt(message, visible)
                answer, next_context = await self.foreground(message, visible)
            assert answer is not None
            committed_messages = next_context.committed_messages
            execution_context = next_context
            result = {
                "session_id": context.session_id,
                "case_id": context.case_id,
                "case_run_id": context.case_run_id,
                "turn_id": turn_id,
                "status": status,
                "final_answer": answer.content,
                "error": None,
                "message_count": len(committed_messages),
            }
            return replace(answer, kind="lora.chat.result", data={"result": result}), next_context
        except asyncio.CancelledError:
            status, error = "skipped", "cancelled"
            store.append("runtime.cancelled", actor="system", payload={"status": status, "reason": error}, turn_id=turn_id)
            raise
        except Exception as exc:
            status, error = "error", str(exc)
            store.append("runtime.error", actor="system", payload={"error": error, "error_type": type(exc).__name__}, turn_id=turn_id)
            raise
        finally:
            session = manager.load(context.session_id)
            # Stable user/model/tool boundaries are already persisted as
            # idempotent conversation checkpoints. Loading the session merges
            # any raw checkpoint written before the latest session snapshot.
            session.system_prompt = execution_context.system_prompt
            session.metadata.update(
                {
                    "active_case_id": context.case_id,
                    "last_case_run_id": context.case_run_id,
                    "last_case_run_status": status,
                }
            )
            if status == "passed":
                session.metadata["agent_context"] = context_to_dict(
                    execution_context,
                    registry=LORA_CONTEXT_CODECS,
                )
            manager.save(session)
            if self.memory_harness is not None and status == "passed":
                await self.memory_harness.record_and_trigger(
                    session,
                    model_envelope={
                        "system_prompt": execution_context.system_prompt,
                        "tools": [
                            {
                                "name": definition.name,
                                "description": definition.description,
                                "parameters": plain_data(definition.parameters),
                            }
                            for definition in execution_context.tools
                        ],
                    },
                )
            store.append(
                "model.response",
                actor="system",
                payload={
                    "agent": type(self).__name__,
                    "agent_alias": self.resolved_agent.alias,
                    **_model_route_trace_payload(
                        _actual_model_route(self.resolved_agent, answer)
                    ),
                    "status": status,
                    "error": error,
                },
                turn_id=turn_id,
            )
            store.append(
                "context.checkpoint",
                actor="system",
                payload={
                    "status": status,
                    "history_message_count": len(session.history),
                    "case_run_id": context.case_run_id,
                },
                turn_id=turn_id,
            )

    async def aclose(self) -> None:
        if self._owns_reminders:
            await self.reminders.close()
        await aclose_if_supported(self.llm)

    def _register_default_tools(self) -> None:
        self._standard_tools = StandardTools(workspace_root=self.workspace_root)
        self._toolkit = ToolKit(
            self._standard_tools.bash.bash,
            self._standard_tools.files.read,
            self._standard_tools.files.write,
            self._standard_tools.files.edit,
            self._standard_tools.files.glob,
            self._standard_tools.files.grep,
        )
        visible = {definition.name for definition in (*self._toolkit.definitions, DIFF_TOOL_SPEC.definition)}
        for spec in self._external_tools:
            if spec.definition.name in visible:
                raise ValueError(f"duplicate model-visible tool name: {spec.definition.name}")
            visible.add(spec.definition.name)
