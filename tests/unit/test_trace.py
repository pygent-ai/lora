from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.schema import CaseRunRef, ContextEvent
from lora.trace import EventStore


class EventStoreTests(unittest.TestCase):
    def test_append_writes_events_and_message_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)

            event_id = store.append(
                "conversation.user_message",
                actor="user",
                payload={"role": "user", "content": "hello"},
                turn_id="turn-0001",
            )

            events = store.list_by_run()
            messages = list(EventStore.iter_jsonl(Path(tmp) / "events" / "messages.jsonl"))
            self.assertEqual(events[0].id, event_id)
            self.assertEqual(events[0].session_id, "s1")
            self.assertEqual(events[0].case_run_id, "r1")
            self.assertEqual(messages[0]["role"], "user")
            self.assertEqual(messages[0]["content"], "hello")

    def test_append_context_event_validates_run_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            event = ContextEvent(
                id="evt_custom",
                session_id="s1",
                case_id="c1",
                case_run_id="r1",
                turn_id="turn-0001",
                type="case.started",
                timestamp="2026-05-27T00:00:00+00:00",
                actor="system",
                payload={"ok": True},
            )

            self.assertEqual(store.append(event), "evt_custom")

            bad_event = ContextEvent(
                id="evt_bad",
                session_id="other",
                case_id="c1",
                case_run_id="r1",
                turn_id=None,
                type="case.started",
                timestamp="2026-05-27T00:00:00+00:00",
                actor="system",
                payload={},
            )
            with self.assertRaises(ValueError):
                store.append(bad_event)

    def test_tool_and_file_events_are_projected_to_dedicated_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "events"
            store = _store(tmp)

            call_id = store.append(
                "tool.call",
                actor="assistant",
                payload={"tool_name": "read_text_file", "args": {"path": "README.md"}},
                turn_id="turn-0001",
            )
            store.append(
                "tool.result",
                actor="tool",
                payload={"tool_call_id": call_id, "status": "success", "result": "content"},
                turn_id="turn-0001",
            )
            store.append(
                "file.read",
                actor="tool",
                payload={"path": "README.md", "content_hash": "abc123"},
                turn_id="turn-0001",
            )

            tool_calls = list(EventStore.iter_jsonl(run_dir / "tool_calls.jsonl"))
            tool_results = list(EventStore.iter_jsonl(run_dir / "tool_results.jsonl"))
            file_events = list(EventStore.iter_jsonl(run_dir / "file_events.jsonl"))
            self.assertEqual(tool_calls[0]["tool_name"], "read_text_file")
            self.assertEqual(tool_results[0]["tool_call_id"], call_id)
            self.assertEqual(file_events[0]["type"], "file.read")
            self.assertEqual(file_events[0]["content_hash"], "abc123")

    def test_session_level_context_artifacts_are_written_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "c1" / "runs" / "r1"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            store = EventStore(CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=run_dir))

            store.append(
                "conversation.user_message",
                actor="user",
                payload={"role": "user", "content": "hello"},
                turn_id="turn-0001",
            )
            call_id = store.append(
                "tool.call",
                actor="assistant",
                payload={"tool_name": "read_text_file", "args": {"relative_path": "README.md"}},
                turn_id="turn-0001",
            )
            store.append(
                "tool.result",
                actor="tool",
                payload={"tool_call_id": call_id, "status": "success", "result": {"ok": True}},
                turn_id="turn-0001",
            )
            store.append(
                "file.read",
                actor="tool",
                payload={"path": "README.md", "content_hash": "abc123", "returned_content": "hello"},
                turn_id="turn-0001",
            )
            store.append(
                "prompt.rendered",
                actor="system",
                payload={"prompt": "System prompt text", "prompt_hash": "hash1", "module_ids": ["system.identity"]},
                turn_id="turn-0001",
            )

            history = list(EventStore.iter_jsonl(session_dir / "context" / "history.jsonl"))
            session_tool_calls = list(EventStore.iter_jsonl(session_dir / "logs" / "tool_calls.jsonl"))
            session_tool_results = list(EventStore.iter_jsonl(session_dir / "logs" / "tool_results.jsonl"))
            session_file_events = list(EventStore.iter_jsonl(session_dir / "logs" / "file_events.jsonl"))
            prompt_log = list(EventStore.iter_jsonl(session_dir / "logs" / "rendered_prompts.jsonl"))
            prompt_text = session_dir / "context" / "rendered_prompts" / "r1" / "turn-0001.txt"

            self.assertEqual(history[0]["seq"], 1)
            self.assertEqual(history[0]["content"], "hello")
            self.assertEqual(session_tool_calls[0]["tool_name"], "read_text_file")
            self.assertEqual(session_tool_results[0]["tool_call_id"], call_id)
            self.assertEqual(session_file_events[0]["payload"]["returned_content"], "hello")
            self.assertEqual(prompt_log[0]["prompt_hash"], "hash1")
            self.assertEqual(prompt_text.read_text(encoding="utf-8"), "System prompt text")

    def test_queries_filter_by_session_case_run_and_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.append("case.started", actor="system", payload={}, turn_id="turn-0001")
            store.append("case.finished", actor="system", payload={"status": "passed"}, turn_id="turn-0002")

            self.assertEqual(len(store.list_by_session("s1")), 2)
            self.assertEqual(len(store.list_by_case("c1")), 2)
            self.assertEqual(len(store.list_by_run("s1", "r1")), 2)
            self.assertEqual([event.type for event in store.list_by_turn("turn-0002")], ["case.finished"])
            self.assertEqual(store.list_by_run("s1", "missing"), [])

    def test_append_error_records_replayable_error_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)

            event_id = store.append_error(RuntimeError("boom"), turn_id="turn-0001")

            events = store.list_by_run()
            self.assertEqual(events[0].id, event_id)
            self.assertEqual(events[0].type, "error")
            self.assertEqual(events[0].payload["error"], "boom")
            self.assertEqual(events[0].payload["error_type"], "RuntimeError")

    def test_iter_jsonl_returns_empty_iterator_for_missing_path(self) -> None:
        rows = list(EventStore.iter_jsonl(Path("does-not-exist.jsonl")))
        self.assertEqual(rows, [])


def _store(tmp: str) -> EventStore:
    return EventStore(
        CaseRunRef(
            session_id="s1",
            case_id="c1",
            case_run_id="r1",
            run_dir=str(Path(tmp) / "events"),
        )
    )


if __name__ == "__main__":
    unittest.main()
