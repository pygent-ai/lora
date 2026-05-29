from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pygent.message import AssistantMessage, AssistantMessageChunk, ToolCall
from pygent.module.tool import BaseTool, ToolCategory

from lora.agent import LoraAgent, _to_pygent_message
from lora.runtime import AgentRuntimeAdapter
from lora.schema import CaseDefinition, RunConfig
from lora.session import SessionManager


class RecordingAgent:
    def __init__(self) -> None:
        self.seen_session_id: str | None = None
        self.seen_history: list[dict[str, Any]] = []

    async def stream(self, context: Any, max_steps: int = 8) -> AsyncIterator[dict[str, Any]]:
        self.seen_session_id = context.session_id
        self.seen_history = list(context.history)
        yield {"role": "assistant", "content": "thinking", "type": "conversation.assistant_message"}
        yield {
            "role": "tool",
            "content": '{"ok": true}',
            "type": "conversation.tool_message",
            "payload": {"role": "tool", "content": '{"ok": true}', "tool_call_id": "call_1"},
        }
        yield {"role": "assistant", "content": "done", "type": "conversation.assistant_message"}


class FailingAgent:
    async def stream(self, context: Any, max_steps: int = 8) -> AsyncIterator[dict[str, Any]]:
        yield {"role": "assistant", "content": "partial", "type": "conversation.assistant_message"}
        raise RuntimeError("boom")


class DeltaAgent:
    async def stream(self, context: Any, max_steps: int = 8) -> AsyncIterator[dict[str, Any]]:
        yield {
            "role": "assistant",
            "content": "Hel",
            "type": "conversation.assistant_delta",
            "payload": {"role": "assistant", "content": "Hel", "delta": "Hel"},
        }
        yield {
            "role": "assistant",
            "content": "lo",
            "type": "conversation.assistant_delta",
            "payload": {"role": "assistant", "content": "lo", "delta": "lo"},
        }
        yield {"role": "assistant", "content": "Hello", "type": "conversation.assistant_message"}


class DirectPygentMessageAgent:
    async def stream(self, context: Any, max_steps: int = 8) -> AsyncIterator[AssistantMessage]:
        yield AssistantMessage(
            content="done",
            reasoning_content="Direct object reasoning.",
            usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        )


class ToolCallingLLM:
    def __init__(self) -> None:
        self.request_messages: list[list[dict[str, Any]]] = []

    async def stream_forward(self, context: Any, **kwargs: Any) -> AsyncIterator[AssistantMessageChunk]:
        self.request_messages.append([message.to_dict() for message in context.history.data])
        if len(self.request_messages) == 1:
            context.add_message(
                AssistantMessage(
                    content="",
                    reasoning_content="Need to call the calculator.",
                    tool_calls=[
                        ToolCall(
                            tool_call_id="call_add",
                            tool_name="add_numbers",
                            arguments={"a": 2, "b": 3},
                        )
                    ],
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                )
            )
            return

        yield AssistantMessageChunk(content="sum is 5", reasoning_content="Now answer.", usage={"total_tokens": 22})
        context.add_message(
            AssistantMessage(
                content="sum is 5",
                reasoning_content="Now answer.",
                usage={"prompt_tokens": 15, "completion_tokens": 7, "total_tokens": 22},
            )
        )


class RepeatedToolCallingLLM:
    def __init__(self) -> None:
        self.request_messages: list[list[dict[str, Any]]] = []

    async def stream_forward(self, context: Any, **kwargs: Any) -> AsyncIterator[AssistantMessageChunk]:
        self.request_messages.append([message.to_dict() for message in context.history.data])
        call_count = len(self.request_messages)
        if call_count <= 2:
            context.add_message(
                AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            tool_call_id=f"call_add_{call_count}",
                            tool_name="add_numbers",
                            arguments={"a": call_count, "b": call_count},
                        )
                    ],
                )
            )
            return

        yield AssistantMessageChunk(content="done")
        context.add_message(AssistantMessage(content="done"))


class AddNumbersTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="add_numbers",
            description="Add two numbers and return the sum.",
            category=ToolCategory.CALCULATION,
        )
        self.parameters.data["a"]["description"] = "First number."
        self.parameters.data["b"]["description"] = "Second number."

    def forward(self, a: float, b: float) -> float:
        return a + b


class ToolEnabledLoraAgent(LoraAgent):
    def start_run(self, case_run_ref: Any, turn_id: str | None) -> None:
        super().start_run(case_run_ref, turn_id)
        self._register_tool(AddNumbersTool())


class AgentRuntimeAdapterTests(unittest.TestCase):
    def test_lora_agent_registers_selected_pygent_tools_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            agent = LoraAgent(config)

            agent.start_run(run, "turn-0001")

            self.assertEqual(list(agent._tools), ["bash", "read", "write", "edit", "glob", "grep"])
            self.assertEqual(
                [tool["function"]["name"] for tool in agent.tools_param()],
                ["bash", "read", "write", "edit", "glob", "grep"],
            )
            self.assertNotIn("delete_file", agent._tools)

    def test_run_case_records_messages_events_and_saves_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("case-a")
            run = manager.start_case_run(ref.session_id, "case-a")
            session = manager.load(ref.session_id)
            case = CaseDefinition.from_dict(
                {
                    "id": "case-a",
                    "input": {"messages": [{"role": "user", "content": "hello"}]},
                }
            )
            agent = RecordingAgent()

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_case(session, case, run)
            )

            loaded = manager.load(ref.session_id)
            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            messages = _read_jsonl(Path(run.run_dir) / "messages.jsonl")

            self.assertEqual(result.status, "passed")
            self.assertEqual(result.final_answer, "thinkingdone")
            self.assertEqual(agent.seen_session_id, ref.session_id)
            self.assertEqual([item["role"] for item in loaded.history], ["user", "assistant", "tool", "assistant"])
            self.assertIn("conversation.user_message", [event["type"] for event in events])
            self.assertIn("conversation.tool_message", [event["type"] for event in events])
            self.assertEqual([message["role"] for message in messages], ["user", "assistant", "tool", "assistant"])

    def test_carry_context_false_hides_prior_history_but_preserves_it_after_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("case-a")
            session = manager.load(ref.session_id)
            session.history = [{"role": "user", "content": "old context"}]
            manager.save(session)
            run = manager.start_case_run(ref.session_id, "case-a")
            session = manager.load(ref.session_id)
            case = CaseDefinition.from_dict(
                {
                    "id": "case-a",
                    "session": {"carry_context": False},
                    "input": {"content": "fresh input"},
                }
            )
            agent = RecordingAgent()

            asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_case(session, case, run)
            )

            loaded = manager.load(ref.session_id)
            self.assertEqual([item["content"] for item in agent.seen_history], ["fresh input"])
            self.assertEqual(loaded.history[0]["content"], "old context")
            self.assertEqual(loaded.history[1]["content"], "fresh input")

    def test_agent_failure_preserves_partial_trace_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("case-a")
            run = manager.start_case_run(ref.session_id, "case-a")
            session = manager.load(ref.session_id)
            case = CaseDefinition.from_dict({"id": "case-a", "input": {"content": "explode"}})

            result = asyncio.run(
                AgentRuntimeAdapter(agent=FailingAgent(), config=config, session_manager=manager).run_case(
                    session,
                    case,
                    run,
                )
            )

            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            loaded = manager.load(ref.session_id)
            self.assertEqual(result.status, "error")
            self.assertEqual(result.final_answer, "partial")
            self.assertIn("boom", result.error or "")
            self.assertIn("runtime.error", [event["type"] for event in events])
            self.assertEqual(loaded.history[-1]["content"], "partial")

    def test_run_turn_streams_assistant_deltas_without_fragmenting_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            chunks: list[str] = []

            result = asyncio.run(
                AgentRuntimeAdapter(agent=DeltaAgent(), config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="stream me",
                    case_run_ref=run,
                    turn_id="turn-0001",
                    on_assistant_delta=chunks.append,
                )
            )

            loaded = manager.load(ref.session_id)
            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            messages = _read_jsonl(Path(run.run_dir) / "messages.jsonl")

            self.assertEqual(chunks, ["Hel", "lo"])
            self.assertEqual(result["final_answer"], "Hello")
            self.assertEqual([item["role"] for item in loaded.history], ["user", "assistant"])
            self.assertEqual(loaded.history[-1]["content"], "Hello")
            self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
            self.assertNotIn("conversation.assistant_delta", [event["type"] for event in events])

    def test_lora_agent_persists_assistant_tool_calls_in_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            llm = ToolCallingLLM()
            agent = ToolEnabledLoraAgent(config)
            agent.llm = llm

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="add 2 and 3",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            assistant = loaded.history[1]
            tool_message = loaded.history[2]
            event_assistant = next(event for event in events if event["type"] == "conversation.assistant_message")

            self.assertEqual(result["status"], "passed")
            self.assertEqual([item["role"] for item in loaded.history], ["user", "assistant", "tool", "assistant"])
            self.assertEqual(assistant["tool_calls"][0]["id"], "call_add")
            self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "add_numbers")
            self.assertEqual(json.loads(assistant["tool_calls"][0]["function"]["arguments"]), {"a": 2, "b": 3})
            self.assertEqual(assistant["reasoning_content"], "Need to call the calculator.")
            self.assertEqual(assistant["usage"]["total_tokens"], 15)
            self.assertEqual(tool_message["tool_call_id"], "call_add")
            self.assertEqual(event_assistant["payload"]["tool_calls"][0]["id"], "call_add")
            self.assertEqual(event_assistant["payload"]["reasoning_content"], "Need to call the calculator.")
            self.assertEqual(event_assistant["payload"]["usage"]["total_tokens"], 15)
            self.assertEqual(loaded.history[-1]["reasoning_content"], "Now answer.")
            self.assertEqual(loaded.history[-1]["usage"]["total_tokens"], 22)
            self.assertEqual(llm.request_messages[1][-2]["tool_calls"][0]["id"], "call_add")
            self.assertEqual(llm.request_messages[1][-2]["reasoning_content"], "Need to call the calculator.")
            self.assertEqual(llm.request_messages[1][-2]["usage"]["total_tokens"], 15)
            self.assertEqual(llm.request_messages[1][-1]["tool_call_id"], "call_add")

    def test_lora_agent_allows_unlimited_steps_until_no_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora", max_steps=-1)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            llm = RepeatedToolCallingLLM()
            agent = ToolEnabledLoraAgent(config)
            agent.llm = llm

            result = asyncio.run(
                AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="keep using tools",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["final_answer"], "done")
            self.assertEqual(len(llm.request_messages), 3)
            self.assertEqual(
                [item["role"] for item in loaded.history],
                ["user", "assistant", "tool", "assistant", "tool", "assistant"],
            )

    def test_runtime_preserves_direct_pygent_message_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)

            result = asyncio.run(
                AgentRuntimeAdapter(agent=DirectPygentMessageAgent(), config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="hello",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            assistant = loaded.history[-1]

            self.assertEqual(result["final_answer"], "done")
            self.assertEqual(assistant["content"], "done")
            self.assertEqual(assistant["reasoning_content"], "Direct object reasoning.")
            self.assertEqual(assistant["usage"]["total_tokens"], 6)

    def test_to_pygent_message_restores_assistant_metadata(self) -> None:
        message = _to_pygent_message(
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "Need to call the calculator.",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "tool_calls": [
                    {
                        "id": "call_add",
                        "type": "function",
                        "function": {"name": "add_numbers", "arguments": "{\"a\": 2, \"b\": 3}"},
                    }
                ],
            }
        )

        self.assertIsNotNone(message)
        restored = message.to_dict()  # type: ignore[union-attr]
        self.assertEqual(restored["tool_calls"][0]["id"], "call_add")
        self.assertEqual(restored["tool_calls"][0]["function"]["name"], "add_numbers")
        self.assertEqual(json.loads(restored["tool_calls"][0]["function"]["arguments"]), {"a": 2, "b": 3})
        self.assertEqual(restored["reasoning_content"], "Need to call the calculator.")
        self.assertEqual(restored["usage"]["total_tokens"], 15)

    def test_run_turn_records_chat_messages_and_saves_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)

            result = asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=session,
                    user_input="hello chat",
                    case_run_ref=run,
                    turn_id="turn-0001",
                )
            )

            loaded = manager.load(ref.session_id)
            events = _read_jsonl(Path(run.run_dir) / "events.jsonl")
            prompt_event = next(event for event in events if event["type"] == "prompt.rendered")
            prompt_text_path = Path(prompt_event["payload"]["prompt_text_path"])
            session_prompt_text_path = (
                Path(ref.session_dir)
                / "context"
                / "rendered_prompts"
                / run.case_run_id
                / "turn-0001.txt"
            )

            self.assertEqual(result["status"], "passed")
            self.assertIn("Lora agent is wired into chat", result["final_answer"])
            self.assertIn("You are Lora", loaded.system_prompt)
            self.assertNotIn("Available tools", loaded.system_prompt)
            self.assertTrue(prompt_text_path.exists())
            self.assertTrue(session_prompt_text_path.exists())
            rendered_prompt = prompt_text_path.read_text(encoding="utf-8")
            self.assertIn("__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__", rendered_prompt)
            self.assertIn("# Available Tools", rendered_prompt)
            self.assertIn(
                "Tools currently available for this request: bash, read, write, edit, glob, grep.",
                rendered_prompt,
            )
            self.assertTrue(prompt_event["payload"]["static_prompt_created"])
            self.assertEqual(prompt_event["payload"]["injection_decision"]["reason"], "before_model_request")
            self.assertEqual([item["role"] for item in loaded.history], ["user", "assistant"])
            self.assertEqual(
                [event["type"] for event in events],
                [
                    "conversation.user_message",
                    "model.request",
                    "context.projection_created",
                    "prompt.rendered",
                    "conversation.assistant_message",
                    "model.response",
                    "context.checkpoint",
                ],
            )

    def test_static_prompt_is_cached_per_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")

            first_run = manager.start_case_run(ref.session_id, "chat")
            first_session = manager.load(ref.session_id)
            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=first_session,
                    user_input="first",
                    case_run_ref=first_run,
                    turn_id="turn-0001",
                )
            )

            metadata_path = Path(ref.session_dir) / "context" / "prompts" / "static_prompt.json"
            text_path = Path(ref.session_dir) / "context" / "prompts" / "static_prompt.txt"
            first_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            first_text = text_path.read_text(encoding="utf-8")

            second_run = manager.start_case_run(ref.session_id, "chat")
            second_session = manager.load(ref.session_id)
            asyncio.run(
                AgentRuntimeAdapter(config=config, session_manager=manager).run_turn(
                    session=second_session,
                    user_input="second",
                    case_run_ref=second_run,
                    turn_id="turn-0002",
                )
            )

            second_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            second_text = text_path.read_text(encoding="utf-8")
            first_prompt_event = next(
                event for event in _read_jsonl(Path(first_run.run_dir) / "events.jsonl") if event["type"] == "prompt.rendered"
            )
            second_prompt_event = next(
                event
                for event in _read_jsonl(Path(second_run.run_dir) / "events.jsonl")
                if event["type"] == "prompt.rendered"
            )

            self.assertEqual(first_text, second_text)
            self.assertEqual(first_metadata["created_at"], second_metadata["created_at"])
            self.assertEqual(first_metadata["prompt_hash"], second_metadata["prompt_hash"])
            self.assertTrue(first_prompt_event["payload"]["static_prompt_created"])
            self.assertFalse(second_prompt_event["payload"]["static_prompt_created"])
            self.assertIn("tool.available", second_prompt_event["payload"]["dynamic_module_ids"])


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
