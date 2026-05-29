from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.schema import RunConfig
from lora.session import SessionManager
from lora.trace import DESIGN_EVENT_TYPES, EventStore
from lora.regression import RegressionRunner
from lora.case import CaseManager


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
                "model.request",
                "context.projection_created",
                "prompt.rendered",
                "tool.call",
                "file.read",
                "file.write",
                "file.edit",
                "file.delete",
                "tool.result",
                "conversation.tool_message",
                "conversation.assistant_message",
                "model.response",
                "context.checkpoint",
                "analysis.created",
                "test.generated",
                "repair.started",
                "repair.patch_created",
                "repair.finished",
                "regression.started",
                "regression.finished",
                "case.finished",
            ]
            self.assertEqual(set(expected_types), DESIGN_EVENT_TYPES)
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
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "messages.jsonl"))), 3)
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_calls.jsonl"))), 1)
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))), 1)
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))), 4)

    def test_regression_run_records_started_and_finished_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".lora").mkdir()
            manifest = root / ".lora" / "regression.json"
            manifest.write_text('{"version": "1.0", "cases": []}', encoding="utf-8")
            config = RunConfig(workspace_root=root, lora_root=root / ".lora")

            result = RegressionRunner(
                config=config,
                session_manager=SessionManager(config),
                case_manager=CaseManager(root),
            ).run(manifest)

            events = list(EventStore.iter_jsonl(Path(result["run_dir"]) / "events.jsonl"))
            self.assertEqual([event["type"] for event in events], ["regression.started", "regression.finished"])
            self.assertEqual(events[-1]["payload"]["status"], "skipped")


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
    if event_type == "model.request":
        return {"agent": "test-agent", "max_steps": 8}
    if event_type == "model.response":
        return {"status": "passed", "message_count": 3}
    if event_type == "context.projection_created":
        return {"projection": {"recent_messages": []}}
    if event_type == "context.checkpoint":
        return {"status": "passed", "history_message_count": 3}
    if event_type == "tool.call":
        return {"tool_name": "read_text_file", "args": {"path": "README.md"}}
    if event_type == "tool.result":
        return {"tool_call_id": "evt_call", "status": "success", "result": "README content"}
    if event_type.startswith("file."):
        return {"path": "README.md", "content_hash": "abc123"}
    if event_type == "analysis.created":
        return {"status": "failed", "root_causes": []}
    if event_type == "test.generated":
        return {"recommended_tests": []}
    if event_type.startswith("repair."):
        return {"status": "skipped"}
    if event_type.startswith("regression."):
        return {"status": "skipped"}
    if event_type == "case.finished":
        return {"status": "passed"}
    return {}


if __name__ == "__main__":
    unittest.main()
