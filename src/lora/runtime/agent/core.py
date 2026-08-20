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
    ModelGroupConfig,
    ModelRoute,
    ReActLayer,
    RetryPolicy,
    ToolCallLayer,
    ToolDefinition,
    ToolKit,
    UserMessage,
    freeze_json_object,
    thaw_json,
)
from pygent.llm import (
    DefaultModelInvoker,
    ModelProviderCapabilities,
    OpenAICompatibleAdapter,
    OpenAICompatibleClient,
)
from pygent.tool import StandardTools, ToolSpec
from pygent.core import EffectSafety, ExecutionRequirements, RecoverySafety

from lora.core.io import plain_data
from lora.schema import ResolvedAgentConfig, RunConfig
from lora.sessions import SessionManager
from lora.tracing import DIFF_TOOL_SPEC, EventStore
from lora.runtime.context import LoraContext
from lora.runtime.eternal_conversation import EternalConversationHarness
from lora.runtime.file_effects import FILE_EFFECT_TOOL_SPEC

from .pipeline import (
    ContextCompressionModule,
    ConversationCheckpointModelModule,
    ConversationCheckpointToolModule,
    DynamicPromptModule,
    LoraToolAuthorization,
    PersistedDiffModule,
    PreparedModelModule,
    PreparedToolModule,
    SkillReminderModule,
    ToolAuditModule,
    checkpoint_conversation_message,
    _context_manager,
    _model_tool_definition,
)
from .prompts import PromptRegistry


MODEL_MAX_OUTPUT_TOKENS = 4096
PYGENT_VERIFY_SSL_ENV = "PYGENT_VERIFY_SSL"


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
    ) -> None:
        super().__init__()
        self.config = config
        self.resolved_agent = resolved_agent or config.resolved_agent
        if self.resolved_agent is None:
            raise ValueError("LoraAgent requires a resolved routes-based agent configuration")
        self.prompt_registry = prompt_registry
        self.managed_model = managed_model
        self.interactive_approvals = interactive_approvals
        self.workspace_root = Path(config.workspace_root)
        primary_route = self.resolved_agent.routes[0]
        self.model_name = primary_route.model_name
        self.llm = model_invoker or (
            self._build_model_invoker()
            if any(route.api_key for route in self.resolved_agent.routes)
            else None
        )
        self._standard_tools: StandardTools | None = None
        self._toolkit: ToolKit | None = None
        self._external_tools = external_tools
        self.memory_harness = memory_harness
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

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(definition.name for definition in self.tool_definitions)

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
                attempt_timeout_seconds=retry.attempt_timeout_seconds,
                backoff=ExponentialBackoff(
                    initial=retry.backoff_initial,
                    maximum=retry.backoff_maximum,
                    multiplier=retry.backoff_multiplier,
                ),
            ),
            generation=GenerationConfig(
                temperature=0.1,
                tool_choice="auto",
                max_output_tokens=MODEL_MAX_OUTPUT_TOKENS,
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
            compression = ContextCompressionModule(self.config, compression_model)
            prepared_model = ConversationCheckpointModelModule(
                self.config,
                PreparedModelModule(
                    prompt=self.prompt,
                    compression=compression,
                    model=model,
                ),
            )
            prepared_tools = ConversationCheckpointToolModule(
                self.config,
                PreparedToolModule(
                    tools=self.new_tool_layer(),
                    audit=ToolAuditModule(self.config),
                    reminders=SkillReminderModule(self.config, self.prompt_registry),
                    persisted_diff=PersistedDiffModule(self.workspace_root, diff_tasks),
                ),
            )
            self.react = ReActLayer(
                model=prepared_model,
                tools=prepared_tools,
                max_steps=self.config.max_steps if self.config.max_steps > 0 else 128,
                max_model_calls=self.config.max_steps if self.config.max_steps > 0 else 128,
                max_tool_calls=max(32, (self.config.max_steps if self.config.max_steps > 0 else 128) * 4),
            )

    def render_initial_user_reminder(self, context: LoraContext) -> str | None:
        return _context_manager(self.config, context, self.prompt_registry).render_initial_user_reminder(
            context=context,
        )

    async def forward(
        self,
        message: UserMessage,
        context: LoraContext,
    ) -> tuple[AIMessage, LoraContext]:
        if not isinstance(context, LoraContext):
            raise TypeError("LoraAgent requires LoraContext")
        if context.session_status == "compression_failed":
            raise RuntimeError("context compression failed; session cannot continue requesting the model")
        manager = SessionManager(self.config)
        store = EventStore(context.case_run_ref)
        turn_id = context.turn_id
        message_data = plain_data(thaw_json(message.data))
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
        execution_context = context.append_history(message)
        await self.emit(kind="lora.chat.started", data=context.case_run_ref.to_dict())
        store.append(
            "model.request",
            actor="system",
            payload={
                "agent": type(self).__name__,
                "agent_alias": self.resolved_agent.alias,
                "model_name": self.model_name,
                "model_route": self.resolved_agent.routes[0].id,
                "api_key_source": self.resolved_agent.routes[0].api_key_source,
                "max_steps": self.config.max_steps,
                "history_message_count": len(context.messages) + 1,
                "latest_user_input": raw_content,
            },
            turn_id=turn_id,
        )
        status = "passed"
        error: str | None = None
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
                answer, next_context = await self.react(message, visible)
            current_index = max(
                (
                    index
                    for index, item in enumerate(next_context.messages)
                    if isinstance(item, UserMessage)
                ),
                default=-1,
            )
            output_messages = next_context.messages[current_index + 1 :]
            next_context = next_context.append_history(*output_messages)
            execution_context = next_context
            result = {
                "session_id": context.session_id,
                "case_id": context.case_id,
                "case_run_id": context.case_run_id,
                "turn_id": turn_id,
                "status": status,
                "final_answer": answer.content,
                "error": None,
                "message_count": 1 + len(output_messages),
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
                                "parameters": plain_data(thaw_json(definition.parameters)),
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
                    "model_name": self.model_name,
                    "model_route": self.resolved_agent.routes[0].id,
                    "api_key_source": self.resolved_agent.routes[0].api_key_source,
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
        invoker = self.llm
        close = getattr(invoker, "aclose", None)
        if callable(close):
            await close()

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
