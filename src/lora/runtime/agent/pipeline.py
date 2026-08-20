from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from pygent import (
    AIMessage,
    Context as PygentContext,
    Message as PygentMessage,
    ModelCallLayer,
    Module,
    ToolAuthorizationDecision,
    ToolAuthorizationRequest,
    ToolCall,
    ToolCallLayer,
    ToolDefinition,
    ToolMessage,
    ToolResult as PygentToolResult,
    thaw_json,
)
from pygent.core import EffectSafety, ExecutionRequirements, RecoverySafety
from pygent.core import (
    EffectIdempotency,
    EffectRetryPolicy,
    EffectSideEffect,
    EffectSpec,
    active_infrastructure,
    freeze_json,
)
from pygent.runtime.codec import message_from_dict, message_to_dict
from pygent.tool import ToolSideEffect

from lora.core.io import plain_data
from lora.runtime.context import LoraContext
from lora.runtime.context_compression import ContextCompressionModelResult, ContextCompressionRunner
from lora.runtime.eternal_conversation import render_memory_context
from lora.runtime.file_effects import DeferredFileEffectBatch, FILE_EFFECT_TOOL_SPEC
from lora.runtime.file_effect_models import DeferredFileEffectJob
from lora.runtime.tools import ToolObserver
from lora.schema import RunConfig
from lora.sessions import SessionManager
from lora.tracing import EventStore

from .common import (
    _message_content,
    _pygent_context_from_model_messages,
    _pygent_context_messages,
    _serialize_tool_payload_for_model,
    _split_current_message,
    _to_pygent_message,
    _session_dir_for_run,
)
from .prompts import AgentContextManager, PromptRegistry
from .prompt_sources import (
    _detect_new_bash_cli,
    _detect_new_skills_after_file_change,
    _render_tool_system_reminder,
)

MAX_EMPTY_TOOL_FOLLOWUP_RETRIES = 5

EFFECT_FREE_RECOVERY = ExecutionRequirements(
    recovery_safety=RecoverySafety.MODULE_BOUNDARY_RETRY,
    effect_safety=EffectSafety.EFFECT_FREE,
)
MANAGED_EFFECT_RECOVERY = ExecutionRequirements(
    recovery_safety=RecoverySafety.MODULE_BOUNDARY_RETRY,
    effect_safety=EffectSafety.MANAGED_EFFECTS,
)


async def checkpoint_conversation_message(
    config: RunConfig,
    context: LoraContext,
    message: PygentMessage,
    *,
    boundary: str,
) -> None:
    checkpoint_id = f"{context.case_run_id}:{context.turn_id}:{boundary}"
    payload = message_to_dict(message)
    metadata = plain_data(thaw_json(context.metadata))
    include_in_session = not (
        isinstance(metadata, dict)
        and metadata.get("persist_conversation_history") is False
    )
    def persist() -> bool:
        manager = SessionManager(config)
        inserted = manager.append_history_checkpoint(
            context.case_run_ref,
            turn_id=context.turn_id,
            checkpoint_id=checkpoint_id,
            message=payload,
            include_in_session=include_in_session,
        )
        if inserted:
            store = EventStore(context.case_run_ref)
            event_type = {
                "assistant": "conversation.assistant_message",
                "tool": "conversation.tool_message",
            }.get(message.role, "conversation.user_message")
            event_payload = {
                **payload,
                "checkpoint_id": checkpoint_id,
                "message": payload,
            }
            if message.role == "user":
                message_data = plain_data(thaw_json(message.data))
                raw_content = (
                    message_data.get("raw_content")
                    if isinstance(message_data, dict)
                    else None
                )
                event_payload.update(
                    {
                        "raw_content": (
                            raw_content
                            if isinstance(raw_content, str)
                            else message.content
                        ),
                        "user_identity": config.user_identity,
                        "wrapped": True,
                    }
                )
            store.append(
                event_type,
                actor=message.role,
                payload=event_payload,
                turn_id=context.turn_id,
            )
        return inserted

    infrastructure = active_infrastructure()
    if infrastructure is None:
        persist()
        return

    async def operation():
        return freeze_json({"inserted": persist()})

    await infrastructure.execute_effect(
        spec=EffectSpec(
            effect_type="lora.conversation.checkpoint",
            side_effect=EffectSideEffect.WRITE,
            idempotency=EffectIdempotency.INHERENT,
            retry_policy=EffectRetryPolicy.REPLAY_SAFE,
        ),
        request={
            "checkpoint_id": checkpoint_id,
            "message": payload,
            "include_in_session": include_in_session,
        },
        operation=operation,
    )


def _message_boundary(
    context: PygentContext,
    *,
    phase: str,
    current: PygentMessage,
    output: PygentMessage,
) -> str:
    material = {
        "phase": phase,
        "history": [message_to_dict(item) for item in context.messages],
        "current": message_to_dict(current),
        "output": message_to_dict(output),
    }
    digest = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return f"{phase}-{digest}"


_READ_PARAMETER_DESCRIPTIONS = {
    "limit": "Maximum number of text lines to return from the starting offset.",
    "offset": "One-based text line number at which to start reading.",
    "pages": (
        "PDF-only one-based page number or inclusive range such as '3' or '1-5'; "
        "at most 20 pages."
    ),
}


def _model_tool_definition(definition: ToolDefinition) -> ToolDefinition:
    if definition.name != "read":
        return definition
    parameters = plain_data(thaw_json(definition.parameters))
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return definition
    for name, description in _READ_PARAMETER_DESCRIPTIONS.items():
        parameter = properties.get(name)
        if isinstance(parameter, dict):
            parameter["description"] = description
    return replace(definition, parameters=parameters)


class LoraToolAuthorization(Module[ToolAuthorizationRequest, ToolAuthorizationDecision]):
    execution_requirements = ExecutionRequirements(
        recovery_safety=RecoverySafety.MODULE_BOUNDARY_RETRY,
        effect_safety=EffectSafety.MANAGED_EFFECTS,
    )
    def __init__(
        self,
        *,
        enabled: bool,
        timeout_seconds: float,
        preauthorized_tools: tuple[str, ...],
        interactive: bool,
        scope_key: str,
        detached_tools: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.preauthorized_tools = preauthorized_tools
        self.interactive = interactive
        self.scope_key = scope_key
        self.detached_tools = detached_tools

    async def forward(
        self,
        request: ToolAuthorizationRequest,
        context: PygentContext,
    ) -> tuple[ToolAuthorizationDecision, PygentContext]:
        name = request.spec.definition.name
        lifecycle = "detach" if name in self.detached_tools else "sync"
        high_risk = request.spec.side_effect in {
            ToolSideEffect.WRITE,
            ToolSideEffect.EXTERNAL,
        }
        if not self.enabled or not high_risk or name in self.preauthorized_tools:
            return (
                ToolAuthorizationDecision(
                    call_id=request.call.call_id,
                    allowed=True,
                    reason_code="lora_policy_allowed",
                    lifecycle=lifecycle,
                ),
                context,
            )
        scope_key = getattr(context, "case_run_id", None) or self.scope_key
        approval_id = f"{scope_key}:{request.call.call_id}"
        if not self.interactive:
            return (
                ToolAuthorizationDecision(
                    call_id=request.call.call_id,
                    allowed=False,
                    reason_code="approval_required_noninteractive",
                    lifecycle=lifecycle,
                ),
                context,
            )
        decision, _ = await self.gather(
            self.wait_external(
                kind="tool-approval",
                key=approval_id,
                request={"tool_name": name, "call_id": request.call.call_id},
                timeout=self.timeout_seconds,
            ),
            self.emit(
                kind="lora.approval.requested",
                data={
                    "approval_id": approval_id,
                    "call_id": request.call.call_id,
                    "tool_name": name,
                    "tool_id": request.spec.tool_id,
                    "side_effect": request.spec.side_effect.value,
                    "arguments": plain_data(thaw_json(request.call.arguments)),
                },
            ),
        )
        approved = bool(decision.get("approved"))
        return (
            ToolAuthorizationDecision(
                call_id=request.call.call_id,
                allowed=approved,
                reason_code="user_approved" if approved else "user_rejected",
                lifecycle=lifecycle,
            ),
            context,
        )


def _context_manager(
    config: RunConfig,
    context: LoraContext,
    prompt_registry: PromptRegistry | None,
) -> AgentContextManager:
    return AgentContextManager(
        session_dir=_session_dir_for_run(Path(context.run_dir)),
        workspace_root=Path(config.workspace_root),
        store=EventStore(context.case_run_ref),
        prompt_registry=prompt_registry,
        cli_bash_presets=config.cli_bash_presets,
        project_lora_root=Path(config.lora_root),
        user_lora_root=Path(config.user_lora_root or Path.home() / ".lora"),
    )


class DynamicPromptModule(Module[PygentMessage, PygentMessage]):
    execution_requirements = MANAGED_EFFECT_RECOVERY
    trusted_live_resource_attributes = ("config", "prompt_registry")

    def __init__(self, config: RunConfig, prompt_registry: PromptRegistry | None) -> None:
        super().__init__()
        self.config = config
        self.prompt_registry = prompt_registry

    async def forward(
        self, message: PygentMessage, context: LoraContext
    ) -> tuple[PygentMessage, LoraContext]:
        if context.model_context_compacted:
            return message, context
        tool_names = [definition.name for definition in context.tools]
        infrastructure = active_infrastructure()
        if infrastructure is None:  # pragma: no cover - managed graph invariant
            raise RuntimeError("dynamic prompt requires managed execution")

        async def operation():
            prompt = _context_manager(
                self.config,
                context,
                self.prompt_registry,
            ).build_model_request_prompt(
                context=context,
                tool_names=tool_names,
            )
            text = prompt.text
            if context.eternal_memory_enabled:
                text = (
                    f"{text}\n\n"
                    f"{render_memory_context(_session_dir_for_run(Path(context.run_dir)), dict(thaw_json(context.memory_projection)))}"
                )
            return freeze_json({"text": text})

        effect = await infrastructure.execute_effect(
            spec=EffectSpec(
                effect_type="lora.prompt.render",
                side_effect=EffectSideEffect.WRITE,
                idempotency=EffectIdempotency.INHERENT,
                retry_policy=EffectRetryPolicy.REPLAY_SAFE,
            ),
            request={
                "message": message_to_dict(message),
                "history": context.history,
                "tool_names": tool_names,
                "memory_projection": plain_data(thaw_json(context.memory_projection)),
            },
            operation=operation,
        )
        result = thaw_json(effect.value)
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise TypeError("replayed dynamic prompt is invalid")
        text = result["text"]
        return message, replace(context, system_prompt=text)


class ContextCompressionModule(Module[PygentMessage, PygentMessage]):
    execution_requirements = MANAGED_EFFECT_RECOVERY
    trusted_live_resource_attributes = ("config",)

    def __init__(
        self,
        config: RunConfig,
        model: ModelCallLayer,
    ) -> None:
        super().__init__()
        self.config = config
        self.model = model

    async def forward(
        self, message: PygentMessage, context: LoraContext
    ) -> tuple[PygentMessage, LoraContext]:
        if context.model_context_compacted:
            return message, context
        if self.config.eternal_conversation.enabled:
            return message, context
        session = SessionManager(self.config).load(context.session_id)
        compression = await ContextCompressionRunner(
            config=self.config,
            session_dir=_session_dir_for_run(Path(context.run_dir)),
        ).maybe_compact(
            session=session,
            system_prompt=context.system_prompt,
            model_messages=_pygent_context_messages(context + message),
            history_cutoff=len(context.full_history),
            call_model=self._call_model,
        )
        if compression.status == "failed":
            raise RuntimeError(compression.reason or "context compression failed")
        if compression.status != "compacted":
            return message, context
        compacted, current = _split_current_message(
            _pygent_context_from_model_messages(context, compression.messages)
        )
        return current, replace(
            compacted,
            tools=context.tools,
            session_status=session.status,
            model_context_compacted=True,
        )

    async def _call_model(
        self,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str,
        tools_enabled: bool,
    ) -> ContextCompressionModelResult:
        converted = [
            item for value in messages if (item := _to_pygent_message(value)) is not None
        ]
        if not converted:
            raise RuntimeError("context compression requires at least one model message")
        context = PygentContext(
            system_prompt=system_prompt,
            messages=tuple(converted[:-1]),
            tools=self.model.tools if tools_enabled else (),
        )
        answer, _ = await self.model(converted[-1], context)
        return ContextCompressionModelResult(
            text=_message_content(answer),
            has_tool_call=bool(answer.tool_calls),
        )


class PreparedModelModule(Module[PygentMessage, AIMessage]):
    execution_requirements = EFFECT_FREE_RECOVERY
    def __init__(
        self,
        *,
        prompt: DynamicPromptModule,
        compression: ContextCompressionModule,
        model: ModelCallLayer,
    ) -> None:
        super().__init__()
        self.prompt = prompt
        self.compression = compression
        self.model = model

    async def forward(
        self, message: PygentMessage, context: PygentContext
    ) -> tuple[AIMessage, PygentContext]:
        current = message
        history = context
        for retry in range(MAX_EMPTY_TOOL_FOLLOWUP_RETRIES + 1):
            current, history = await self.prompt(current, history)
            current, history = await self.compression(current, history)
            answer, model_context = await self.model(current, history)
            if not isinstance(message, ToolMessage) or answer.content or answer.tool_calls:
                return answer, model_context
            if retry == MAX_EMPTY_TOOL_FOLLOWUP_RETRIES:
                raise RuntimeError(
                    "empty assistant response after tool result "
                    f"after {MAX_EMPTY_TOOL_FOLLOWUP_RETRIES} retries"
                )
        raise AssertionError("unreachable")


class ConversationCheckpointModelModule(Module[PygentMessage, AIMessage]):
    execution_requirements = MANAGED_EFFECT_RECOVERY
    trusted_live_resource_attributes = ("config", "inner")

    def __init__(self, config: RunConfig, inner: Module[PygentMessage, AIMessage]) -> None:
        super().__init__()
        self.config = config
        self.inner = inner

    async def forward(
        self, message: PygentMessage, context: LoraContext
    ) -> tuple[AIMessage, LoraContext]:
        answer, next_context = await self.inner(message, context)
        await checkpoint_conversation_message(
            self.config,
            next_context,
            answer,
            boundary=_message_boundary(
                context,
                phase="model",
                current=message,
                output=answer,
            ),
        )
        return answer, next_context


class ToolAuditModule(Module[ToolMessage, ToolMessage]):
    execution_requirements = MANAGED_EFFECT_RECOVERY
    trusted_live_resource_attributes = ("config",)

    def __init__(self, config: RunConfig) -> None:
        super().__init__()
        self.config = config

    async def forward(
        self, message: ToolMessage, context: PygentContext
    ) -> tuple[ToolMessage, PygentContext]:
        infrastructure = active_infrastructure()
        if infrastructure is None:  # pragma: no cover - managed graph invariant
            raise RuntimeError("tool audit requires managed execution")
        assistant = context.messages[-1] if context.messages else None
        calls = {
            call.call_id: call
            for call in assistant.tool_calls
        } if isinstance(assistant, AIMessage) else {}

        async def operation():
            projected: list[PygentToolResult] = []
            next_context = context
            observer = ToolObserver(
                EventStore(context.case_run_ref),
                workspace_root=self.config.workspace_root,
                track_file_effects=True,
                defer_file_effects=True,
                allow_read_outside_workspace=self.config.allow_read_outside_workspace,
                bash_full_output_allowlist=self.config.bash_full_output_allowlist,
            )
            for result in message.results:
                call = calls.get(result.call_id)
                arguments = (
                    plain_data(thaw_json(call.arguments))
                    if call is not None
                    else {}
                )
                if not isinstance(arguments, dict):
                    arguments = {}
                payload, deferred_job = observer.record_framework_result(
                    result.name,
                    arguments,
                    context.turn_id,
                    result,
                    available_tools=tuple(
                        definition.name for definition in context.tools
                    ),
                )
                if deferred_job is not None:
                    next_context = next_context.append_file_effects(deferred_job)
                await self.emit(
                    kind="lora.runtime.message",
                    data={
                        "role": "tool",
                        "content": _serialize_tool_payload_for_model(payload),
                        "message_type": "conversation.tool_message",
                        "payload": {
                            "role": "tool",
                            "tool_call_id": result.call_id,
                            "name": result.name,
                        },
                        "is_delta": False,
                    },
                )
                projected.append(
                    replace(
                        result,
                        output=_serialize_tool_payload_for_model(payload),
                    )
                )
            return freeze_json(
                {
                    "message": message_to_dict(ToolMessage(results=tuple(projected))),
                    "pending_file_effects": [
                        plain_data(thaw_json(item))
                        for item in next_context.pending_file_effects
                    ],
                }
            )

        effect = await infrastructure.execute_effect(
            spec=EffectSpec(
                effect_type="lora.tool.audit",
                side_effect=EffectSideEffect.WRITE,
                idempotency=EffectIdempotency.INHERENT,
                retry_policy=EffectRetryPolicy.REPLAY_SAFE,
            ),
            request={
                "message": message_to_dict(message),
                "assistant": (
                    message_to_dict(assistant)
                    if isinstance(assistant, AIMessage)
                    else None
                ),
                "turn_id": context.turn_id,
                "available_tools": [definition.name for definition in context.tools],
            },
            operation=operation,
        )
        result = thaw_json(effect.value)
        if not isinstance(result, dict):
            raise TypeError("replayed tool audit is invalid")
        projected_message = message_from_dict(result.get("message"))
        if not isinstance(projected_message, ToolMessage):
            raise TypeError("replayed tool audit did not return a ToolMessage")
        raw_jobs = result.get("pending_file_effects", [])
        if not isinstance(raw_jobs, list):
            raise TypeError("replayed tool audit pending effects are invalid")
        jobs = tuple(DeferredFileEffectJob.from_dict(item) for item in raw_jobs)
        next_context = replace(context, pending_file_effects=()).append_file_effects(*jobs)
        return projected_message, next_context


class SkillReminderModule(Module[ToolMessage, ToolMessage]):
    execution_requirements = MANAGED_EFFECT_RECOVERY
    trusted_live_resource_attributes = ("config", "prompt_registry")

    def __init__(self, config: RunConfig, prompt_registry: PromptRegistry | None) -> None:
        super().__init__()
        self.config = config
        self.prompt_registry = prompt_registry

    async def forward(
        self, message: ToolMessage, context: PygentContext
    ) -> tuple[ToolMessage, PygentContext]:
        infrastructure = active_infrastructure()
        if infrastructure is None:  # pragma: no cover - managed graph invariant
            raise RuntimeError("skill reminder requires managed execution")
        assistant = context.messages[-1] if context.messages else None
        calls = {
            call.call_id: call
            for call in assistant.tool_calls
        } if isinstance(assistant, AIMessage) else {}

        async def operation():
            updated: list[PygentToolResult] = []
            manager = _context_manager(self.config, context, self.prompt_registry)
            for result in message.results:
                call = calls.get(result.call_id)
                arguments = (
                    plain_data(thaw_json(call.arguments))
                    if call is not None
                    else {}
                )
                if not isinstance(arguments, dict):
                    arguments = {}
                new_cli: list[dict[str, Any]] = []
                new_skills: list[dict[str, Any]] = []
                if result.status == "succeeded" and result.name == "bash":
                    new_cli = _detect_new_bash_cli(
                        manager.session_dir,
                        self.config.cli_bash_presets,
                        command=str(arguments.get("command") or ""),
                    )
                    new_skills = _detect_new_skills_after_file_change(
                        manager.session_dir,
                        user_skills_dir=manager.user_skills_dir,
                        project_skills_dir=manager.project_skills_dir,
                    )
                elif result.status == "succeeded" and result.name in {
                    "write",
                    "edit",
                }:
                    new_skills = _detect_new_skills_after_file_change(
                        manager.session_dir,
                        user_skills_dir=manager.user_skills_dir,
                        project_skills_dir=manager.project_skills_dir,
                    )
                reminder = _render_tool_system_reminder(
                    manager,
                    context=context,
                    new_cli_entries=new_cli,
                    new_skill_entries=new_skills,
                )
                output = str(plain_data(thaw_json(result.output)) or "")
                updated.append(
                    replace(
                        result,
                        output=f"{output}\n\n{reminder}" if reminder else output,
                    )
                )
            return freeze_json(
                {"message": message_to_dict(ToolMessage(results=tuple(updated)))}
            )

        effect = await infrastructure.execute_effect(
            spec=EffectSpec(
                effect_type="lora.tool.reminder",
                side_effect=EffectSideEffect.WRITE,
                idempotency=EffectIdempotency.INHERENT,
                retry_policy=EffectRetryPolicy.REPLAY_SAFE,
            ),
            request={
                "message": message_to_dict(message),
                "assistant": (
                    message_to_dict(assistant)
                    if isinstance(assistant, AIMessage)
                    else None
                ),
                "turn_id": context.turn_id,
            },
            operation=operation,
        )
        replayed = thaw_json(effect.value)
        if not isinstance(replayed, dict):
            raise TypeError("replayed skill reminder is invalid")
        updated_message = message_from_dict(replayed.get("message"))
        if not isinstance(updated_message, ToolMessage):
            raise TypeError("replayed skill reminder did not return a ToolMessage")
        return updated_message, context


class PersistedDiffModule(Module[ToolMessage, ToolMessage]):
    execution_requirements = EFFECT_FREE_RECOVERY
    trusted_live_resource_attributes = ("workspace_root",)

    def __init__(self, workspace_root: Path, tasks: ToolCallLayer) -> None:
        super().__init__()
        self.workspace_root = workspace_root
        self.tasks = tasks

    async def forward(
        self, message: ToolMessage, context: PygentContext
    ) -> tuple[ToolMessage, PygentContext]:
        jobs, next_context = context.drain_file_effects()
        if jobs:
            batch = DeferredFileEffectBatch.create(
                case_run_ref=context.case_run_ref,
                workspace_root=self.workspace_root,
                jobs=list(jobs),
            )
            await self.tasks(
                AIMessage(
                    tool_calls=(
                        ToolCall(
                            call_id=batch.batch_id,
                            name=FILE_EFFECT_TOOL_SPEC.definition.name,
                            arguments={"batch": batch.to_dict()},
                            tool_id=FILE_EFFECT_TOOL_SPEC.tool_id,
                            tool_version=FILE_EFFECT_TOOL_SPEC.version,
                            idempotency_key=batch.batch_id,
                        ),
                    )
                ),
                replace(context, tools=(FILE_EFFECT_TOOL_SPEC.definition,)),
            )
        return message, next_context


class PreparedToolModule(Module[AIMessage, ToolMessage]):
    execution_requirements = EFFECT_FREE_RECOVERY
    def __init__(
        self,
        *,
        tools: ToolCallLayer,
        audit: ToolAuditModule,
        reminders: SkillReminderModule,
        persisted_diff: PersistedDiffModule,
    ) -> None:
        super().__init__()
        self.tools = tools
        self.audit = audit
        self.reminders = reminders
        self.persisted_diff = persisted_diff

    async def forward(
        self, message: AIMessage, context: PygentContext
    ) -> tuple[ToolMessage, PygentContext]:
        tool_message, tool_context = await self.tools(message, context)
        projection_context = tool_context + message
        tool_message, projection_context = await self.audit(tool_message, projection_context)
        tool_message, projection_context = await self.reminders(tool_message, projection_context)
        tool_message, projection_context = await self.persisted_diff(tool_message, projection_context)
        return tool_message, replace(
            tool_context,
            pending_file_effects=projection_context.pending_file_effects,
        )


class ConversationCheckpointToolModule(Module[AIMessage, ToolMessage]):
    execution_requirements = MANAGED_EFFECT_RECOVERY
    trusted_live_resource_attributes = ("config", "inner")

    def __init__(self, config: RunConfig, inner: Module[AIMessage, ToolMessage]) -> None:
        super().__init__()
        self.config = config
        self.inner = inner

    async def forward(
        self, message: AIMessage, context: LoraContext
    ) -> tuple[ToolMessage, LoraContext]:
        tool_message, next_context = await self.inner(message, context)
        await checkpoint_conversation_message(
            self.config,
            next_context,
            tool_message,
            boundary=_message_boundary(
                context,
                phase="tool",
                current=message,
                output=tool_message,
            ),
        )
        return tool_message, next_context
