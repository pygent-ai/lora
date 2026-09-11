from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from pygent import (
    AIMessage,
    InjectionKind,
    ModelCallLayer,
    Module,
    Reminder,
    ToolAuthorizationDecision,
    ToolAuthorizationRequest,
    ToolCall,
    ToolCallLayer,
    ToolDefinition,
    ToolMessage,
    freeze_json_object,
    thaw_json,
)
from pygent import (
    Context as PygentContext,
)
from pygent import (
    Message as PygentMessage,
)
from pygent import (
    ToolResult as PygentToolResult,
)
from pygent.core import (
    EffectIdempotency,
    EffectRetryPolicy,
    EffectSafety,
    EffectSideEffect,
    EffectSpec,
    ExecutionRequirements,
    RecoverySafety,
    active_infrastructure,
    freeze_json,
)
from pygent.runtime.codec import message_from_dict, message_to_dict
from pygent.tool import ToolSideEffect

from lora.core.io import plain_data, plain_object
from lora.runtime.context import LoraContext
from lora.runtime.context_snapshots import ContextSnapshotStore
from lora.runtime.eternal_conversation import render_memory_access_instruction
from lora.runtime.file_effect_models import DeferredFileEffectJob
from lora.runtime.file_effects import FILE_EFFECT_TOOL_SPEC, DeferredFileEffectBatch
from lora.runtime.reminders import ReminderService
from lora.runtime.reminders.agent_messages import render_agent_message
from lora.runtime.tools import ToolObserver
from lora.schema import RunConfig
from lora.sessions import AgentMessage, AgentMessageState, SessionManager
from lora.tracing import EventStore

from .common import (
    _serialize_tool_payload_for_model,
    _session_dir_for_run,
)
from .prompts import AgentContextManager, PromptRegistry

MAX_EMPTY_TOOL_FOLLOWUP_RETRIES = 5

EFFECT_FREE_RECOVERY = ExecutionRequirements(
    recovery_safety=RecoverySafety.MODULE_BOUNDARY_RETRY,
    effect_safety=EffectSafety.EFFECT_FREE,
)
MANAGED_EFFECT_RECOVERY = ExecutionRequirements(
    recovery_safety=RecoverySafety.MODULE_BOUNDARY_RETRY,
    effect_safety=EffectSafety.MANAGED_EFFECTS,
)


def _context_snapshot_payload(
    context: LoraContext,
    current: PygentMessage,
    *,
    phase: str = "request",
    response: AIMessage | None = None,
) -> dict[str, Any]:
    messages = [
        {**message_to_dict(message), "source": "history", "position": index}
        for index, message in enumerate(context.messages)
    ]
    messages.append(
        {
            **message_to_dict(current),
            "source": "current",
            "position": len(messages),
        }
    )
    if response is not None:
        messages.append(
            {
                **message_to_dict(response),
                "source": "response",
                "position": len(messages),
            }
        )
    tools = [
        {
            "name": definition.name,
            "description": definition.description,
            "parameters": plain_data(definition.parameters),
        }
        for definition in context.tools
    ]
    identity = {
        "case_run_id": context.case_run_id,
        "turn_id": context.turn_id,
        "compression_version": context.compression_count,
        "projection_revision": context.projection_revision,
        "phase": phase,
        "system_prompt": context.system_prompt,
        "messages": messages,
        "tools": tools,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return {
        "schema_version": 2,
        "snapshot_id": f"context-{digest}",
        "session_id": context.session_id,
        "case_run_id": context.case_run_id,
        "turn_id": context.turn_id,
        "compression_version": context.compression_count,
        "projection_revision": context.projection_revision,
        "phase": phase,
        "system_prompt": context.system_prompt,
        "messages": messages,
        "message_count": len(messages),
        "tool_count": len(context.tools),
        "tools": tools,
    }


async def checkpoint_context_snapshot(
    context: LoraContext,
    current: PygentMessage,
    *,
    phase: str = "request",
    response: AIMessage | None = None,
) -> dict[str, Any]:
    payload = _context_snapshot_payload(
        context,
        current,
        phase=phase,
        response=response,
    )
    store = ContextSnapshotStore(_session_dir_for_run(Path(context.run_dir)))

    async def operation():
        saved = await asyncio.to_thread(store.save, payload)
        return freeze_json(saved)

    infrastructure = active_infrastructure()
    if infrastructure is None:
        return await asyncio.to_thread(store.save, payload)
    effect = await infrastructure.execute_effect(
        spec=EffectSpec(
            effect_type="lora.context.snapshot",
            side_effect=EffectSideEffect.WRITE,
            idempotency=EffectIdempotency.INHERENT,
            retry_policy=EffectRetryPolicy.REPLAY_SAFE,
        ),
        request=freeze_json_object(payload),
        operation=operation,
    )
    return plain_object(thaw_json(effect.value))


async def checkpoint_conversation_message(
    config: RunConfig,
    context: LoraContext,
    message: PygentMessage,
    *,
    boundary: str,
) -> None:
    checkpoint_id = f"{context.case_run_id}:{context.turn_id}:{boundary}"
    payload = message_to_dict(message)
    metadata = plain_data(context.metadata)
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
            event_actor = message.role
            if message.kind == "lora.automation.trigger":
                event_type = "conversation.automation_trigger"
                event_actor = "system"
            event_payload = {
                **payload,
                "checkpoint_id": checkpoint_id,
            }
            if message.role == "user":
                message_data = plain_data(message.data)
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
                        "wrapped": message.kind != "lora.automation.trigger",
                        "origin": (
                            "automation"
                            if message.kind == "lora.automation.trigger"
                            else "user"
                        ),
                    }
                )
            store.append(
                event_type,
                actor=event_actor,
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
        request=freeze_json_object(
            {
                "checkpoint_id": checkpoint_id,
                "message": payload,
                "include_in_session": include_in_session,
            }
        ),
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
    parameters = plain_data(definition.parameters)
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return definition
    for name, description in _READ_PARAMETER_DESCRIPTIONS.items():
        parameter = properties.get(name)
        if isinstance(parameter, dict):
            parameter["description"] = description
    return replace(definition, parameters=parameters)


class LoraToolAuthorization(
    Module[ToolAuthorizationRequest, ToolAuthorizationDecision]
):
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
                    "arguments": plain_data(request.call.arguments),
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

    def __init__(
        self, config: RunConfig, prompt_registry: PromptRegistry | None
    ) -> None:
        super().__init__()
        self.config = config
        self.prompt_registry = prompt_registry

    async def forward(
        self, message: PygentMessage, context: LoraContext
    ) -> tuple[PygentMessage, LoraContext]:
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
                    f"{render_memory_access_instruction(_session_dir_for_run(Path(context.run_dir)), plain_object(context.memory_projection))}"
                )
            return freeze_json({"text": text})

        effect = await infrastructure.execute_effect(
            spec=EffectSpec(
                effect_type="lora.prompt.render",
                side_effect=EffectSideEffect.WRITE,
                idempotency=EffectIdempotency.INHERENT,
                retry_policy=EffectRetryPolicy.REPLAY_SAFE,
            ),
            request=freeze_json_object(
                {
                    "message": message_to_dict(message),
                    "history": context.history,
                    "tool_names": tool_names,
                    "memory_projection": plain_data(context.memory_projection),
                }
            ),
            operation=operation,
        )
        result = thaw_json(effect.value)
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise TypeError("replayed dynamic prompt is invalid")
        text = result["text"]
        return message, replace(context, system_prompt=text)


class ForegroundModelModule(Module[PygentMessage, AIMessage]):
    """Foreground provider call with bounded empty tool-followup recovery."""

    execution_requirements = EFFECT_FREE_RECOVERY

    def __init__(
        self,
        *,
        model: ModelCallLayer,
    ) -> None:
        super().__init__()
        self.model = model

    async def forward(
        self, message: PygentMessage, context: PygentContext
    ) -> tuple[AIMessage, PygentContext]:
        current = message
        history = context
        for retry in range(MAX_EMPTY_TOOL_FOLLOWUP_RETRIES + 1):
            answer, model_context = await self.model(current, history)
            if (
                not isinstance(message, ToolMessage)
                or answer.content
                or answer.tool_calls
            ):
                return answer, model_context
            if retry == MAX_EMPTY_TOOL_FOLLOWUP_RETRIES:
                raise RuntimeError(
                    "empty assistant response after tool result "
                    f"after {MAX_EMPTY_TOOL_FOLLOWUP_RETRIES} retries"
                )
        raise AssertionError("unreachable")


class ContextSnapshotModelModule(Module[PygentMessage, AIMessage]):
    execution_requirements = MANAGED_EFFECT_RECOVERY
    trusted_live_resource_attributes = ("inner",)

    def __init__(self, inner: Module[PygentMessage, AIMessage]) -> None:
        super().__init__()
        self.inner = inner

    async def forward(
        self, message: PygentMessage, context: LoraContext
    ) -> tuple[AIMessage, LoraContext]:
        request_snapshot = await checkpoint_context_snapshot(context, message)
        await self.emit(
            kind="lora.context.snapshot",
            data=freeze_json_object(request_snapshot),
        )
        answer, next_context = await self.inner(message, context)
        response_snapshot = await checkpoint_context_snapshot(
            next_context,
            message,
            phase="response",
            response=answer,
        )
        await self.emit(
            kind="lora.context.snapshot",
            data=freeze_json_object(response_snapshot),
        )
        return answer, next_context


class ConversationCheckpointModelModule(Module[PygentMessage, AIMessage]):
    execution_requirements = MANAGED_EFFECT_RECOVERY
    trusted_live_resource_attributes = ("config", "inner")

    def __init__(
        self, config: RunConfig, inner: Module[PygentMessage, AIMessage]
    ) -> None:
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
        self, message: ToolMessage, context: LoraContext
    ) -> tuple[ToolMessage, LoraContext]:
        infrastructure = active_infrastructure()
        if infrastructure is None:  # pragma: no cover - managed graph invariant
            raise RuntimeError("tool audit requires managed execution")
        assistant = context.messages[-1] if context.messages else None
        calls = (
            {call.call_id: call for call in assistant.tool_calls}
            if isinstance(assistant, AIMessage)
            else {}
        )

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
                arguments = plain_data(call.arguments) if call is not None else {}
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
                        plain_data(item) for item in next_context.pending_file_effects
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
            request=freeze_json_object(
                {
                    "message": message_to_dict(message),
                    "assistant": (
                        message_to_dict(assistant)
                        if isinstance(assistant, AIMessage)
                        else None
                    ),
                    "turn_id": context.turn_id,
                    "available_tools": [
                        definition.name for definition in context.tools
                    ],
                }
            ),
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
        next_context = replace(context, pending_file_effects=()).append_file_effects(
            *jobs
        )
        return projected_message, next_context


class RuntimeReminderModule(Module[ToolMessage, ToolMessage]):
    execution_requirements = MANAGED_EFFECT_RECOVERY
    trusted_live_resource_attributes = ("reminders",)

    def __init__(self, reminders: ReminderService) -> None:
        super().__init__()
        self.reminders = reminders
        self.reminder = Reminder()

    async def forward(
        self, message: ToolMessage, context: LoraContext
    ) -> tuple[ToolMessage, LoraContext]:
        infrastructure = active_infrastructure()
        if infrastructure is None:  # pragma: no cover - managed graph invariant
            raise RuntimeError("runtime context requires managed execution")
        execution_id = infrastructure.managed_execution_id
        if execution_id is None:
            raise RuntimeError("runtime context requires an execution id")
        identity = hashlib.sha256(
            json.dumps(
                [context.turn_id, [result.call_id for result in message.results]]
            ).encode()
        ).hexdigest()
        claim_id = f"agent-messages:{execution_id}:{identity}"
        assistant = context.messages[-1] if context.messages else None
        calls = (
            {call.call_id: call for call in assistant.tool_calls}
            if isinstance(assistant, AIMessage)
            else {}
        )

        async def operation():
            mutation_indexes = [
                index
                for index, result in enumerate(message.results)
                if result.status == "succeeded"
                and result.name in {"bash", "write", "edit"}
            ]
            bash_commands: list[str] = []
            for index in mutation_indexes:
                result = message.results[index]
                if result.name != "bash":
                    continue
                call = calls.get(result.call_id)
                arguments = plain_data(call.arguments) if call is not None else {}
                if not isinstance(arguments, dict):
                    arguments = {}
                bash_commands.append(str(arguments.get("command") or ""))
            reminder = await self.reminders.observe_after_tools(
                context.session_id,
                bash_commands=tuple(bash_commands),
                file_mutation=bool(mutation_indexes),
                has_results=bool(message.results),
            )
            agent_messages = await self.reminders.claim_agent_messages(
                context.session_id,
                claim_id=claim_id,
            )
            return freeze_json(
                {
                    "content": reminder,
                    "agent_messages": [
                        item.to_dict(include_content=True) for item in agent_messages
                    ],
                }
            )

        effect = await infrastructure.execute_effect(
            spec=EffectSpec(
                effect_type="lora.tool.reminder",
                side_effect=EffectSideEffect.WRITE,
                idempotency=EffectIdempotency.INHERENT,
                retry_policy=EffectRetryPolicy.REPLAY_SAFE,
            ),
            request=freeze_json_object(
                {
                    "message": message_to_dict(message),
                    "assistant": (
                        message_to_dict(assistant)
                        if isinstance(assistant, AIMessage)
                        else None
                    ),
                    "turn_id": context.turn_id,
                    "agent_message_claim_id": claim_id,
                }
            ),
            operation=operation,
        )
        replayed = thaw_json(effect.value)
        if not isinstance(replayed, dict):
            raise TypeError("replayed runtime context is invalid")
        content = replayed.get("content")
        if content:
            piece, _ = await self.reminder(
                PygentMessage(
                    content=content, kind=InjectionKind.RUNTIME_CONTEXT.value
                ),
                context,
            )
            await self.reminders.deliver_tool_context(
                execution_id,
                input_id=f"runtime-context:{identity}",
                content=piece.content,
            )
        raw_agent_messages = replayed.get("agent_messages", [])
        if not isinstance(raw_agent_messages, list):
            raise TypeError("replayed agent messages are invalid")
        delivered: list[AgentMessage] = []
        for raw in raw_agent_messages:
            if not isinstance(raw, dict):
                raise TypeError("replayed agent message is invalid")
            candidate = AgentMessage.from_dict(raw)
            claimed = await self.reminders.claimed_agent_message(
                candidate.message_id,
                claim_id=claim_id,
            )
            if claimed is not None:
                delivered.append(claimed)
                continue
            current = await self.reminders.agent_message_status(candidate.message_id)
            if (
                current.state is AgentMessageState.DELIVERED
                and current.delivered_execution_id == execution_id
            ):
                delivered.append(candidate)
        if delivered:
            agent_context = "\n".join(render_agent_message(item) for item in delivered)
            message = replace(
                message,
                content=(
                    agent_context
                    if not message.content
                    else f"{message.content}\n{agent_context}"
                ),
            )
            try:
                for item in delivered:
                    current = await self.reminders.agent_message_status(item.message_id)
                    if current.state is AgentMessageState.DELIVERED:
                        continue
                    await self.reminders.acknowledge_agent_message(
                        item.message_id,
                        claim_id=claim_id,
                        execution_id=execution_id,
                    )
            except BaseException:
                for item in delivered:
                    await self.reminders.release_agent_message(
                        item.message_id,
                        claim_id=claim_id,
                    )
                raise
        return message, context


class PersistedDiffModule(Module[ToolMessage, ToolMessage]):
    execution_requirements = EFFECT_FREE_RECOVERY
    trusted_live_resource_attributes = ("workspace_root",)

    def __init__(self, workspace_root: Path, tasks: ToolCallLayer) -> None:
        super().__init__()
        self.workspace_root = workspace_root
        self.tasks = tasks

    async def forward(
        self, message: ToolMessage, context: LoraContext
    ) -> tuple[ToolMessage, LoraContext]:
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


MAX_IDENTICAL_FOREGROUND_TOOL_CALLS = 8


class _RepeatedToolCallState:
    def __init__(self) -> None:
        self.turn_id: str | None = None
        self.signature: tuple[tuple[str, str], ...] | None = None
        self.count = 0


class RepeatedToolCallGuardModule(Module[AIMessage, ToolMessage]):
    execution_requirements = EFFECT_FREE_RECOVERY
    trusted_live_resource_attributes = ("state",)

    def __init__(self, inner: Module[AIMessage, ToolMessage]) -> None:
        super().__init__()
        self.inner = inner
        self.state = _RepeatedToolCallState()

    async def forward(
        self, message: AIMessage, context: LoraContext
    ) -> tuple[ToolMessage, LoraContext]:
        signature = tuple(
            (call.name, repr(call.arguments)) for call in message.tool_calls
        )
        if context.turn_id == self.state.turn_id and signature == self.state.signature:
            self.state.count += 1
        else:
            self.state.turn_id = context.turn_id
            self.state.signature = signature
            self.state.count = 1
        if signature and self.state.count >= MAX_IDENTICAL_FOREGROUND_TOOL_CALLS:
            raise RuntimeError(
                "foreground Agent requested the identical tool arguments "
                f"{self.state.count} consecutive times in turn {context.turn_id or '<unknown>'}; "
                "stop this attempt so the caller can retry from its durable baseline"
            )
        return await self.inner(message, context)


class PreparedToolModule(Module[AIMessage, ToolMessage]):
    execution_requirements = EFFECT_FREE_RECOVERY

    def __init__(
        self,
        *,
        tools: ToolCallLayer,
        audit: ToolAuditModule,
        reminders: RuntimeReminderModule,
        persisted_diff: PersistedDiffModule,
    ) -> None:
        super().__init__()
        self.tools = tools
        self.audit = audit
        self.reminders = reminders
        self.persisted_diff = persisted_diff

    async def forward(
        self, message: AIMessage, context: LoraContext
    ) -> tuple[ToolMessage, LoraContext]:
        tool_message, tool_context = await self.tools(message, context)
        projection_context = tool_context + message
        tool_message, projection_context = await self.audit(
            tool_message, projection_context
        )
        tool_message, projection_context = await self.reminders(
            tool_message, projection_context
        )
        tool_message, projection_context = await self.persisted_diff(
            tool_message, projection_context
        )
        return tool_message, replace(
            tool_context,
            pending_file_effects=projection_context.pending_file_effects,
        )


class ConversationCheckpointToolModule(Module[AIMessage, ToolMessage]):
    execution_requirements = MANAGED_EFFECT_RECOVERY
    trusted_live_resource_attributes = ("config", "inner")

    def __init__(
        self, config: RunConfig, inner: Module[AIMessage, ToolMessage]
    ) -> None:
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
