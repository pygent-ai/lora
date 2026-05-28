from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from .schema import AgentSession, CaseDefinition, CaseRunRef, CaseRunResult, RunConfig
from .session import SessionManager
from .trace import EventStore


@dataclass(slots=True)
class RuntimeMessage:
    role: str
    content: str
    type: str | None = None
    payload: dict[str, Any] | None = None


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
    async def stream(self, context: RuntimeContext, max_steps: int = 8) -> AsyncIterator[dict[str, Any]]:
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

            agent = LoraAgent(config)
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
    ) -> dict[str, Any]:
        store = EventStore(case_run_ref)
        context = RuntimeContext(session)
        resolved_turn_id = turn_id or f"turn-{uuid.uuid4().hex[:8]}"
        final_parts: list[str] = []
        message_count = 0
        status = "passed"
        error: str | None = None
        model_request_written = False

        try:
            _start_agent_run(self.agent, case_run_ref, resolved_turn_id)
            context.add_message({"role": "user", "content": user_input})
            store.append(
                "conversation.user_message",
                actor="user",
                payload={"role": "user", "content": user_input},
                turn_id=resolved_turn_id,
            )
            message_count += 1
            store.append(
                "model.request",
                actor="system",
                payload={
                    "agent": type(self.agent).__name__,
                    "max_steps": self.config.max_steps,
                    "history_message_count": len(context.history),
                    "latest_user_input": user_input,
                },
                turn_id=resolved_turn_id,
            )
            model_request_written = True

            async for raw_output in self._stream_agent(context, user_input):
                runtime_message = _normalize_output(raw_output)
                event_type = runtime_message.type or _event_type_for_role(runtime_message.role)
                actor = _actor_for_role(runtime_message.role)
                payload = runtime_message.payload or {"role": runtime_message.role, "content": runtime_message.content}
                context.add_message({"role": runtime_message.role, "content": runtime_message.content, **payload})
                store.append(event_type, actor=actor, payload=payload, turn_id=resolved_turn_id)
                if runtime_message.role == "assistant":
                    final_parts.append(runtime_message.content)
                message_count += 1
        except Exception as exc:  # noqa: BLE001 - preserve partial chat state on agent failures.
            status = "error"
            error = str(exc)
            store.append(
                "runtime.error",
                actor="system",
                payload={"error": error, "error_type": type(exc).__name__},
                turn_id=resolved_turn_id,
            )
        finally:
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
                event_type = runtime_message.type or _event_type_for_role(runtime_message.role)
                actor = _actor_for_role(runtime_message.role)
                payload = runtime_message.payload or {"role": runtime_message.role, "content": runtime_message.content}
                context.add_message({"role": runtime_message.role, "content": runtime_message.content, **payload})
                store.append(event_type, actor=actor, payload=payload, turn_id=turn_id)
                if runtime_message.role == "assistant":
                    final_parts.append(runtime_message.content)
                message_count += 1
        except Exception as exc:  # noqa: BLE001 - runtime boundary must preserve trace on any agent failure.
            status = "error"
            error = str(exc)
            store.append(
                "runtime.error",
                actor="system",
                payload={"error": error, "error_type": type(exc).__name__},
                turn_id=turn_id,
            )
        finally:
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


def _case_carry_context(case: CaseDefinition) -> bool:
    return case.session.get("carry_context") is not False


def _normalize_output(output: Any) -> RuntimeMessage:
    if isinstance(output, RuntimeMessage):
        return output
    if isinstance(output, dict):
        role = str(output.get("role") or output.get("actor") or "assistant")
        content = _stringify(output.get("content") if "content" in output else output.get("result", ""))
        return RuntimeMessage(role=role, content=content, type=output.get("type"), payload=dict(output.get("payload") or output))

    role = _value(getattr(output, "role", None)) or "assistant"
    content = _value(getattr(output, "content", None))
    if content is None:
        content = str(output)
    if role == "tool":
        return RuntimeMessage(role="tool", content=str(content), type="conversation.tool_message")
    return RuntimeMessage(role=str(role), content=str(content), type=_event_type_for_role(str(role)))


def _message_to_history(message: dict[str, Any] | Any) -> dict[str, Any]:
    if isinstance(message, dict):
        role = str(message.get("role", "user"))
        content = _stringify(message.get("content", ""))
        return {**message, "role": role, "content": content}
    role = _value(getattr(message, "role", None)) or "user"
    content = _value(getattr(message, "content", None)) or ""
    return {"role": str(role), "content": str(content)}


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
