from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.schema import RunConfig
from lora.session import SessionManager
from lora.trace import EventStore


class TraceEventStoreScenarioTests(unittest.TestCase):
    def test_case_run_trace_is_append_only_and_replayable_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            session = manager.create("trace-case")
            run = manager.start_case_run(session.session_id, "trace-case")
            store = EventStore(run)

            expected_types = [
                "case.started",
                "conversation.user_message",
                "prompt.rendered",
                "tool.call",
                "file.read",
                "tool.result",
                "conversation.assistant_message",
                "case.finished",
            ]
            for event_type in expected_types:
                store.append(
                    event_type,
                    actor=_actor_for(event_type),
                    payload=_payload_for(event_type),
                    turn_id="turn-0001",
                )

            replayed = store.list_by_run(session.session_id, run.case_run_id)
            self.assertEqual([event.type for event in replayed], expected_types)
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "events.jsonl"))), len(expected_types))
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "messages.jsonl"))), 2)
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_calls.jsonl"))), 1)
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))), 1)
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))), 1)


def _actor_for(event_type: str) -> str:
    if event_type.startswith("conversation.user"):
        return "user"
    if event_type.startswith("conversation.assistant"):
        return "assistant"
    if event_type.startswith("tool.") or event_type.startswith("file."):
        return "tool"
    return "system"


def _payload_for(event_type: str) -> dict[str, object]:
    if event_type == "conversation.user_message":
        return {"role": "user", "content": "read README"}
    if event_type == "conversation.assistant_message":
        return {"role": "assistant", "content": "done"}
    if event_type == "prompt.rendered":
        return {"prompt_hash": "hash1"}
    if event_type == "tool.call":
        return {"tool_name": "read_text_file", "args": {"path": "README.md"}}
    if event_type == "tool.result":
        return {"tool_call_id": "evt_call", "status": "success", "result": "README content"}
    if event_type == "file.read":
        return {"path": "README.md", "content_hash": "abc123"}
    if event_type == "case.finished":
        return {"status": "passed"}
    return {}


if __name__ == "__main__":
    unittest.main()
