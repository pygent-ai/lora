from __future__ import annotations

import inspect
import json
import shutil
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Callable

from .io import plain_data
from .schema import AgentSession, CaseDefinition, CaseRunRef, CaseRunResult, RunConfig
from .session import SessionManager
from .trace import EventStore


@dataclass(slots=True)
class RuntimeMessage:
    role: str
    content: str
    type: str | None = None
    payload: dict[str, Any] | None = None
    is_delta: bool = False


class RuntimeContext:
    def __init__(self, session: AgentSession):
        self.session = session
        self.session_id = session.session_id
        self.system_prompt = session.system_prompt
        self.history = session.history

    def add_message(self, message: dict[str, Any] | Any) -> None:
        self.history.append(_message_to_history(message))

    @property
    def last_message(self) -> dict[str, Any] | None:
        return self.history[-1] if self.history else None


class EchoAgent:
    async def stream(self, context: RuntimeContext, max_steps: int = -1) -> AsyncIterator[dict[str, Any]]:
        user_messages = [message for message in context.history if message.get("role") == "user"]
        content = user_messages[-1]["content"] if user_messages else ""
        yield {"role": "assistant", "content": f"Echo: {content}", "type": "conversation.assistant_message"}


class AgentRuntimeAdapter:
    def __init__(
        self,
        *,
        agent: Any | None = None,
        config: RunConfig,
        session_manager: SessionManager,
    ):
        if agent is None:
            from .agent import LoraAgent

            agent = LoraAgent(config, resolved_agent=config.resolved_agent)
        self.agent = agent
        self.config = config
        self.session_manager = session_manager

    async def run_turn(
        self,
        *,
        session: AgentSession,
        user_input: str,
        case_run_ref: CaseRunRef,
        turn_id: str | None = None,
        on_assistant_delta: Callable[[str], Any] | None = None,
        on_runtime_message: Callable[[RuntimeMessage], Any] | None = None,
    ) -> dict[str, Any]:
        store = EventStore(case_run_ref)
        context = RuntimeContext(session)
        resolved_turn_id = turn_id or f"turn-{uuid.uuid4().hex[:8]}"
        final_parts: list[str] = []
        message_count = 0
        status = "passed"
        error: str | None = None
        model_request_written = False
        assistant_delta_parts: list[str] = []

        try:
            _start_agent_run(self.agent, case_run_ref, resolved_turn_id)
            wrapped_user_input = wrap_user_message(user_input, self.config.user_identity)
            initial_reminder = _initial_user_system_reminder(session, self.config)
            if initial_reminder:
                wrapped_user_input = f"{wrapped_user_input}\n\n{initial_reminder}"
            context.add_message({"role": "user", "content": wrapped_user_input})
            store.append(
                "conversation.user_message",
                actor="user",
                payload={
                    "role": "user",
                    "content": wrapped_user_input,
                    "raw_content": user_input,
                    "user_identity": self.config.user_identity,
                    "wrapped": True,
                },
                turn_id=resolved_turn_id,
            )
            message_count += 1
            store.append(
                "model.request",
                actor="system",
                payload={
                    "agent": type(self.agent).__name__,
                    **_agent_event_metadata(self.config),
                    "max_steps": self.config.max_steps,
                    "history_message_count": len(context.history),
                    "latest_user_input": user_input,
                },
                turn_id=resolved_turn_id,
            )
            model_request_written = True

            async for raw_output in self._stream_agent(context, user_input):
                runtime_message = _normalize_output(raw_output)
                if runtime_message.is_delta:
                    if runtime_message.role == "assistant" and runtime_message.content:
                        assistant_delta_parts.append(runtime_message.content)
                        await _emit_assistant_delta(on_assistant_delta, runtime_message.content)
                    continue

                if assistant_delta_parts and runtime_message.role != "assistant":
                    message_count += _flush_assistant_delta(
                        assistant_delta_parts,
                        context=context,
                        store=store,
                        turn_id=resolved_turn_id,
                        final_parts=final_parts,
                    )
                if runtime_message.role == "assistant" and assistant_delta_parts:
                    buffered = "".join(assistant_delta_parts)
                    if runtime_message.content == buffered:
                        assistant_delta_parts.clear()
                    elif not runtime_message.content:
                        assistant_delta_parts.clear()
                        runtime_message.content = buffered
                        runtime_message.payload = {"role": "assistant", "content": buffered}
                    else:
                        message_count += _flush_assistant_delta(
                            assistant_delta_parts,
                            context=context,
                            store=store,
                            turn_id=resolved_turn_id,
                            final_parts=final_parts,
                        )
                message_count += _append_runtime_message(
                    runtime_message,
                    context=context,
                    store=store,
                    turn_id=resolved_turn_id,
                    final_parts=final_parts,
                )
                await _emit_runtime_message(on_runtime_message, runtime_message)
        except Exception as exc:  # noqa: BLE001 - preserve partial chat state on agent failures.
            message_count += _flush_assistant_delta(
                assistant_delta_parts,
                context=context,
                store=store,
                turn_id=resolved_turn_id,
                final_parts=final_parts,
            )
            status = "error"
            error = str(exc)
            store.append(
                "runtime.error",
                actor="system",
                payload={"error": error, "error_type": type(exc).__name__},
                turn_id=resolved_turn_id,
            )
        finally:
            message_count += _flush_assistant_delta(
                assistant_delta_parts,
                context=context,
                store=store,
                turn_id=resolved_turn_id,
                final_parts=final_parts,
            )
            session.history = context.history
            session.metadata.update(
                {
                    "active_case_id": case_run_ref.case_id,
                    "last_case_run_id": case_run_ref.case_run_id,
                    "last_case_run_status": status,
                }
            )
            self.session_manager.save(session)
            if model_request_written or error is not None:
                store.append(
                    "model.response",
                    actor="system",
                    payload={
                        "agent": type(self.agent).__name__,
                        **_agent_event_metadata(self.config),
                        "status": status,
                        "error": error,
                        "assistant_text_length": len("".join(final_parts)),
                        "message_count": message_count,
                    },
                    turn_id=resolved_turn_id,
                )
            store.append(
                "context.checkpoint",
                actor="system",
                payload={
                    "status": status,
                    "message_count": message_count,
                    "history_message_count": len(session.history),
                    "case_run_id": case_run_ref.case_run_id,
                },
                turn_id=resolved_turn_id,
            )

        return {
            "session_id": case_run_ref.session_id,
            "case_id": case_run_ref.case_id,
            "case_run_id": case_run_ref.case_run_id,
            "turn_id": resolved_turn_id,
            "status": status,
            "final_answer": "".join(final_parts),
            "error": error,
            "message_count": message_count,
        }

    async def run_case(
        self,
        session: AgentSession,
        case: CaseDefinition,
        case_run_ref: CaseRunRef,
    ) -> CaseRunResult:
        store = EventStore(case_run_ref)
        carry_context = _case_carry_context(case)
        original_history = list(session.history)
        if not carry_context:
            session.history = []
        context = RuntimeContext(session)
        turn_id = "turn-0001"
        final_parts: list[str] = []
        message_count = 0
        status = "passed"
        error: str | None = None
        model_request_written = False
        assistant_delta_parts: list[str] = []

        store.append("case.started", actor="system", payload={"title": case.title}, turn_id=turn_id)
        try:
            _start_agent_run(self.agent, case_run_ref, turn_id)
            input_messages = _case_messages(case)
            for message in input_messages:
                context.add_message(message)
                store.append(
                    "conversation.user_message",
                    actor="user",
                    payload={"role": "user", "content": message["content"]},
                    turn_id=turn_id,
                )
                message_count += 1
            store.append(
                "model.request",
                actor="system",
                payload={
                    "agent": type(self.agent).__name__,
                    **_agent_event_metadata(self.config),
                    "max_steps": self.config.max_steps,
                    "history_message_count": len(context.history),
                    "input_message_count": len(input_messages),
                    "carry_context": carry_context,
                    "latest_user_input": _latest_user_content(case),
                },
                turn_id=turn_id,
            )
            model_request_written = True

            async for raw_output in self._stream_agent(context, _latest_user_content(case)):
                runtime_message = _normalize_output(raw_output)
                if runtime_message.is_delta:
                    if runtime_message.role == "assistant" and runtime_message.content:
                        assistant_delta_parts.append(runtime_message.content)
                    continue

                if assistant_delta_parts and runtime_message.role != "assistant":
                    message_count += _flush_assistant_delta(
                        assistant_delta_parts,
                        context=context,
                        store=store,
                        turn_id=turn_id,
                        final_parts=final_parts,
                    )
                if runtime_message.role == "assistant" and assistant_delta_parts:
                    buffered = "".join(assistant_delta_parts)
                    if runtime_message.content == buffered:
                        assistant_delta_parts.clear()
                    elif not runtime_message.content:
                        assistant_delta_parts.clear()
                        runtime_message.content = buffered
                        runtime_message.payload = {"role": "assistant", "content": buffered}
                    else:
                        message_count += _flush_assistant_delta(
                            assistant_delta_parts,
                            context=context,
                            store=store,
                            turn_id=turn_id,
                            final_parts=final_parts,
                        )
                message_count += _append_runtime_message(
                    runtime_message,
                    context=context,
                    store=store,
                    turn_id=turn_id,
                    final_parts=final_parts,
                )
        except Exception as exc:  # noqa: BLE001 - runtime boundary must preserve trace on any agent failure.
            message_count += _flush_assistant_delta(
                assistant_delta_parts,
                context=context,
                store=store,
                turn_id=turn_id,
                final_parts=final_parts,
            )
            status = "error"
            error = str(exc)
            store.append(
                "runtime.error",
                actor="system",
                payload={"error": error, "error_type": type(exc).__name__},
                turn_id=turn_id,
            )
        finally:
            message_count += _flush_assistant_delta(
                assistant_delta_parts,
                context=context,
                store=store,
                turn_id=turn_id,
                final_parts=final_parts,
            )
            session.history = context.history if carry_context else [*original_history, *context.history]
            session.metadata.update(
                {
                    "active_case_id": case.id,
                    "last_case_run_id": case_run_ref.case_run_id,
                    "last_case_run_status": status,
                }
            )
            self.session_manager.save(session)
            if model_request_written or error is not None:
                store.append(
                    "model.response",
                    actor="system",
                    payload={
                        "agent": type(self.agent).__name__,
                        **_agent_event_metadata(self.config),
                        "status": status,
                        "error": error,
                        "assistant_text_length": len("".join(final_parts)),
                        "message_count": message_count,
                    },
                    turn_id=turn_id,
                )
            store.append(
                "case.finished",
                actor="system",
                payload={"status": status, "error": error},
                turn_id=turn_id,
            )
            store.append(
                "context.checkpoint",
                actor="system",
                payload={
                    "status": status,
                    "message_count": message_count,
                    "history_message_count": len(session.history),
                    "case_run_id": case_run_ref.case_run_id,
                },
                turn_id=turn_id,
            )

        events = store.list_by_run()
        return CaseRunResult(
            session_id=case_run_ref.session_id,
            case_id=case_run_ref.case_id,
            case_run_id=case_run_ref.case_run_id,
            status=status,  # type: ignore[arg-type]
            final_answer="".join(final_parts),
            error=error,
            event_count=len(events),
            message_count=message_count,
        )

    async def _stream_agent(self, context: RuntimeContext, latest_user_input: str) -> AsyncIterator[Any]:
        stream = getattr(self.agent, "stream", None)
        if stream is None:
            run = getattr(self.agent, "run", None)
            if run is None:
                raise TypeError("agent must expose stream(...) or run(...)")
            result = run(latest_user_input, max_steps=self.config.max_steps)
            if inspect.isawaitable(result):
                result = await result
            yield {"role": "assistant", "content": str(result), "type": "conversation.assistant_message"}
            return

        try:
            output = stream(context, max_steps=self.config.max_steps)
        except TypeError:
            output = stream(latest_user_input, max_steps=self.config.max_steps)

        if hasattr(output, "__aiter__"):
            async for item in output:
                yield item
            return
        for item in output:
            yield item


def _case_messages(case: CaseDefinition) -> list[dict[str, str]]:
    messages = case.input.get("messages")
    if isinstance(messages, list):
        return [
            {"role": "user", "content": str(message.get("content", ""))}
            for message in messages
            if isinstance(message, dict) and message.get("role", "user") == "user"
        ]
    content = case.input.get("content") or case.input.get("prompt") or ""
    return [{"role": "user", "content": str(content)}] if content else []


def _latest_user_content(case: CaseDefinition) -> str:
    messages = _case_messages(case)
    return messages[-1]["content"] if messages else ""


def wrap_user_message(user_message: str, user_identity: str = "default") -> str:
    identity = escape(user_identity or "default", quote=False)
    message = escape(user_message, quote=False)
    return "\n".join(
        [
            "<user-context>",
            f"  <user-identity>{identity}</user-identity>",
            f"  <user-message>{message}</user-message>",
            "</user-context>",
        ]
    )


def _initial_user_system_reminder(session: AgentSession, config: RunConfig) -> str | None:
    state_path = Path(session.session_dir) / "state" / "cli_context.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
        if state.get("initial_available_cli_injected"):
            return None
    if not config.cli_bash_presets:
        return None
    lines = [
        "<system-reminder>",
        "<time>",
        f"  当前系统时间为：{__import__('datetime').datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "</time>",
        "",
        "<cli-context>",
        "  <available-bash-cli>",
    ]
    for preset in config.cli_bash_presets:
        status = "installed" if shutil.which(preset.name) else "not installed"
        lines.extend(
            [
                f"    <{preset.name}>",
                f"      {escape(preset.description, quote=False)}",
                f"      Status: {status}.",
                f"    </{preset.name}>",
            ]
        )
    lines.extend(["  </available-bash-cli>", "</cli-context>", "</system-reminder>"])
    return "\n".join(lines)


def _case_carry_context(case: CaseDefinition) -> bool:
    return case.session.get("carry_context") is not False


def _agent_event_metadata(config: RunConfig) -> dict[str, Any]:
    return {
        "agent_alias": config.agent_alias,
        "model_name": config.model_name,
        "api_key_source": config.api_key_source,
        "base_url": config.base_url,
    }


def _normalize_output(output: Any) -> RuntimeMessage:
    if isinstance(output, RuntimeMessage):
        return output
    if isinstance(output, dict):
        role = str(output.get("role") or output.get("actor") or "assistant")
        content = _stringify(output.get("content") if "content" in output else output.get("result", ""))
        event_type = output.get("type")
        payload = dict(output.get("payload") or output)
        return RuntimeMessage(
            role=role,
            content=content,
            type=event_type,
            payload=payload,
            is_delta=_is_delta_output(output, payload, event_type),
        )

    payload = _message_payload(output)
    role = (payload or {}).get("role") or _value(getattr(output, "role", None)) or "assistant"
    content = (payload or {}).get("content") if payload and "content" in payload else _value(getattr(output, "content", None))
    if content is None:
        content = str(output)
    if role == "tool":
        return RuntimeMessage(role="tool", content=str(content), type="conversation.tool_message", payload=payload)
    event_type = _event_type_for_role(str(role))
    return RuntimeMessage(
        role=str(role),
        content=str(content),
        type=event_type,
        payload=payload,
        is_delta=_is_delta_output(output, None, event_type),
    )


def _append_runtime_message(
    runtime_message: RuntimeMessage,
    *,
    context: RuntimeContext,
    store: EventStore,
    turn_id: str | None,
    final_parts: list[str],
) -> int:
    event_type = runtime_message.type or _event_type_for_role(runtime_message.role)
    actor = _actor_for_role(runtime_message.role)
    payload = runtime_message.payload or {"role": runtime_message.role, "content": runtime_message.content}
    if runtime_message.role == "tool":
        content = _canonical_tool_content(runtime_message.content)
        payload = {**payload, "content": content}
        runtime_message.content = content
    context.add_message({**payload, "role": runtime_message.role, "content": runtime_message.content})
    store.append(event_type, actor=actor, payload=payload, turn_id=turn_id)
    if runtime_message.role == "assistant" and not _has_tool_calls(payload):
        final_parts.clear()
        final_parts.append(runtime_message.content)
    return 1


def _flush_assistant_delta(
    delta_parts: list[str],
    *,
    context: RuntimeContext,
    store: EventStore,
    turn_id: str | None,
    final_parts: list[str],
) -> int:
    if not delta_parts:
        return 0
    content = "".join(delta_parts)
    delta_parts.clear()
    if not content:
        return 0
    return _append_runtime_message(
        RuntimeMessage(role="assistant", content=content, type="conversation.assistant_message"),
        context=context,
        store=store,
        turn_id=turn_id,
        final_parts=final_parts,
    )


async def _emit_assistant_delta(callback: Callable[[str], Any] | None, content: str) -> None:
    if callback is None:
        return
    result = callback(content)
    if inspect.isawaitable(result):
        await result


async def _emit_runtime_message(callback: Callable[[RuntimeMessage], Any] | None, message: RuntimeMessage) -> None:
    if callback is None:
        return
    result = callback(message)
    if inspect.isawaitable(result):
        await result


def _is_delta_output(output: Any, payload: dict[str, Any] | None, event_type: Any) -> bool:
    if event_type in {"conversation.assistant_delta", "conversation.assistant_message_delta", "model.response.chunk"}:
        return True
    if isinstance(output, dict):
        return bool(output.get("delta") or output.get("is_delta") or (payload or {}).get("delta"))
    return isinstance(output, str) or type(output).__name__.endswith("Chunk")


def _message_to_history(message: dict[str, Any] | Any) -> dict[str, Any]:
    if isinstance(message, dict):
        role = str(message.get("role", "user"))
        content = _stringify(message.get("content", ""))
        return {**message, "role": role, "content": content}
    role = _value(getattr(message, "role", None)) or "user"
    content = _value(getattr(message, "content", None)) or ""
    return {"role": str(role), "content": str(content)}


def _has_tool_calls(payload: dict[str, Any]) -> bool:
    tool_calls = payload.get("tool_calls")
    return isinstance(tool_calls, list) and bool(tool_calls)


def _canonical_tool_content(content: str) -> str:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content
    if not isinstance(payload, dict) or not {"status", "result", "error", "tool_call_id"}.issubset(payload):
        return content
    ordered = {
        "status": payload.get("status"),
        "result": payload.get("result"),
        "error": payload.get("error"),
        "tool_call_id": payload.get("tool_call_id"),
    }
    for key, value in payload.items():
        if key not in ordered:
            ordered[key] = value
    return json.dumps(ordered, ensure_ascii=False)


def _message_payload(message: Any) -> dict[str, Any] | None:
    if not hasattr(message, "to_dict"):
        return None
    payload = plain_data(message.to_dict())
    return payload if isinstance(payload, dict) else None


def _event_type_for_role(role: str) -> str:
    return {
        "assistant": "conversation.assistant_message",
        "tool": "conversation.tool_message",
        "user": "conversation.user_message",
    }.get(role, "conversation.assistant_message")


def _actor_for_role(role: str) -> str:
    if role in {"user", "assistant", "tool"}:
        return role
    return "system"


def _value(value: Any) -> Any:
    return getattr(value, "data", value)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _start_agent_run(agent: Any, case_run_ref: CaseRunRef, turn_id: str | None) -> None:
    start_run = getattr(agent, "start_run", None)
    if start_run is not None:
        start_run(case_run_ref, turn_id)
