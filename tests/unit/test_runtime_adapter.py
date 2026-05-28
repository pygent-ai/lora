from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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


class AgentRuntimeAdapterTests(unittest.TestCase):
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

            self.assertEqual(result["status"], "passed")
            self.assertIn("Lora agent is wired into chat", result["final_answer"])
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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
