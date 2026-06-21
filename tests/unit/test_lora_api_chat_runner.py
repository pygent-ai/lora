from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from lora.core.io import read_json
from lora.runtime import RuntimeMessage
from lora.tracing import EventStore
from lora_api.dependencies import ApiContext
from lora_api.models.requests import ChatTurnRequest
from lora_api.services import chat_runner
from lora_api.services.chat_runner import ChatRunRegistry, stream_chat_turn


def test_stream_chat_turn_resumes_running_chat_after_disconnect(tmp_path: Path, monkeypatch: Any) -> None:
    class ResumableAdapter:
        def __init__(self, **_: Any) -> None:
            pass

        async def run_turn(self, **kwargs: Any) -> dict[str, Any]:
            await kwargs["on_assistant_delta"]("one")
            await asyncio.sleep(0.05)
            await kwargs["on_assistant_delta"]("two")
            return {
                "session_id": kwargs["case_run_ref"].session_id,
                "case_id": kwargs["case_run_ref"].case_id,
                "case_run_id": kwargs["case_run_ref"].case_run_id,
                "status": "passed",
                "final_answer": "done",
            }

    monkeypatch.setattr(chat_runner, "AgentRuntimeAdapter", ResumableAdapter)

    async def scenario() -> None:
        context = ApiContext(workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json"))
        registry = ChatRunRegistry(disconnect_grace_seconds=1.0, completed_resume_ttl_seconds=1.0)

        stream = stream_chat_turn(context, ChatTurnRequest(message="hello"), registry=registry)
        started = _decode_sse(await stream.__anext__())
        first_delta = _decode_sse(await stream.__anext__())
        await stream.aclose()

        await asyncio.sleep(0.1)
        resumed = []
        resume_request = ChatTurnRequest(
            message="hello",
            session_id=started["session_id"],
            resume_case_run_id=started["case_run_id"],
            resume_from_event=first_delta["sequence"],
        )
        async for chunk in stream_chat_turn(context, resume_request, registry=registry):
            if chunk.startswith(":"):
                continue
            resumed.append(_decode_sse(chunk))

        assert [event["type"] for event in resumed] == ["assistant.delta", "chat.completed"]
        assert resumed[0]["payload"]["delta"] == "two"
        assert resumed[-1]["payload"]["status"] == "passed"
        assert read_json(Path(started["payload"]["run_dir"]) / "run_metadata.json")["status"] == "passed"

    asyncio.run(scenario())


def test_stream_chat_turn_cancels_abandoned_chat_after_grace(tmp_path: Path, monkeypatch: Any) -> None:
    class WaitingAdapter:
        def __init__(self, **_: Any) -> None:
            pass

        async def run_turn(self, **_: Any) -> dict[str, Any]:
            await asyncio.sleep(10)
            return {"status": "passed", "final_answer": "too late"}

    monkeypatch.setattr(chat_runner, "AgentRuntimeAdapter", WaitingAdapter)

    async def scenario() -> None:
        context = ApiContext(workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json"))
        registry = ChatRunRegistry(disconnect_grace_seconds=0.01, completed_resume_ttl_seconds=1.0)

        stream = stream_chat_turn(context, ChatTurnRequest(message="hello"), registry=registry)
        started = _decode_sse(await stream.__anext__())
        await stream.aclose()

        metadata_path = Path(started["payload"]["run_dir"]) / "run_metadata.json"
        for _ in range(50):
            if read_json(metadata_path)["status"] == "skipped":
                break
            await asyncio.sleep(0.02)
        assert read_json(metadata_path)["status"] == "skipped"

    asyncio.run(scenario())


def test_stream_chat_turn_emits_lightweight_tool_result_event(tmp_path: Path, monkeypatch: Any) -> None:
    large_result = "x" * 2_000

    class ToolResultAdapter:
        def __init__(self, **_: Any) -> None:
            pass

        async def run_turn(self, **kwargs: Any) -> dict[str, Any]:
            store = EventStore(kwargs["case_run_ref"])
            call_id = store.append(
                "tool.call",
                actor="assistant",
                payload={"tool_name": "glob", "args": {"pattern": "**/*.py"}},
                turn_id=kwargs.get("turn_id"),
            )
            await kwargs["on_runtime_message"](
                RuntimeMessage(
                    role="tool",
                    content=json.dumps(
                        {
                            "status": "success",
                            "result": large_result,
                            "error": None,
                            "tool_call_id": call_id,
                        }
                    ),
                    type="conversation.tool_message",
                    payload={"role": "tool", "tool_call_id": call_id},
                )
            )
            return {"status": "passed", "final_answer": "done"}

    monkeypatch.setattr(chat_runner, "AgentRuntimeAdapter", ToolResultAdapter)

    async def scenario() -> None:
        context = ApiContext(workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json"))
        events = []
        async for chunk in stream_chat_turn(context, ChatTurnRequest(message="hello")):
            if chunk.startswith(":"):
                continue
            events.append(_decode_sse(chunk))

        tool_event = next(event for event in events if event["type"] == "tool.result")
        assert tool_event["payload"]["tool_name"] == "glob"
        assert tool_event["payload"]["status"] == "success"
        assert tool_event["payload"]["result_size"] == 2_000
        assert tool_event["payload"]["truncated"] is True
        assert tool_event["payload"]["result_ref"].startswith("/tool-results/")
        assert "result" not in tool_event["payload"]
        assert large_result not in json.dumps(tool_event, ensure_ascii=False)
        assert all(
            not (event["type"] == "runtime.message" and event.get("payload", {}).get("role") == "tool")
            for event in events
        )

    asyncio.run(scenario())


def test_stream_chat_turn_emits_assistant_reasoning_delta(tmp_path: Path, monkeypatch: Any) -> None:
    class ReasoningAdapter:
        def __init__(self, **_: Any) -> None:
            pass

        async def run_turn(self, **kwargs: Any) -> dict[str, Any]:
            await kwargs["on_assistant_reasoning_delta"]("checking files")
            await kwargs["on_assistant_delta"]("done")
            return {
                "session_id": kwargs["case_run_ref"].session_id,
                "case_id": kwargs["case_run_ref"].case_id,
                "case_run_id": kwargs["case_run_ref"].case_run_id,
                "status": "passed",
                "final_answer": "done",
            }

    monkeypatch.setattr(chat_runner, "AgentRuntimeAdapter", ReasoningAdapter)

    async def scenario() -> None:
        context = ApiContext(workspace_root=str(tmp_path), state_path=str(tmp_path / "state.json"))
        events = []
        async for chunk in stream_chat_turn(context, ChatTurnRequest(message="hello")):
            if chunk.startswith(":"):
                continue
            events.append(_decode_sse(chunk))

        assert [event["type"] for event in events] == [
            "chat.started",
            "assistant.reasoning_delta",
            "assistant.delta",
            "chat.completed",
        ]
        assert events[1]["payload"]["delta"] == "checking files"

    asyncio.run(scenario())


def _decode_sse(chunk: str) -> dict[str, Any]:
    data = "\n".join(line.removeprefix("data: ") for line in chunk.splitlines() if line.startswith("data: "))
    return json.loads(data)
