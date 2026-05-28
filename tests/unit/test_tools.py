from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.schema import CaseRunRef
from lora.tools import FileStateTracker, ReadRange, ToolContext, ToolInterceptor
from lora.trace import EventStore


class ToolTests(unittest.TestCase):
    def test_file_state_tracker_stubs_contained_unchanged_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.txt"
            path.write_text("a\nb\nc\n", encoding="utf-8")
            tracker = FileStateTracker(Path(tmp) / "state")

            first = tracker.read_text_file(path)
            second = tracker.read_text_file(path, offset=2, limit=1)

            self.assertEqual(first["status"], "success")
            self.assertEqual(second["status"], "stubbed")
            self.assertIn("earlier", second["content"])

    def test_hash_change_disables_old_read_stub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.txt"
            path.write_text("a\nb\n", encoding="utf-8")
            tracker = FileStateTracker(Path(tmp) / "state")
            tracker.read_text_file(path, offset=1, limit=1)
            path.write_text("changed\nb\n", encoding="utf-8")

            result = tracker.read_text_file(path, offset=1, limit=1)

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["content"], "changed")

    def test_should_stub_exact_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = FileStateTracker(Path(tmp) / "state")
            tracker.record_read("file.txt", "hash", ReadRange(unit="line", start=1, end=3), "evt_1")

            decision = tracker.should_stub_read("file.txt", "hash", ReadRange(unit="line", start=1, end=3))

            self.assertEqual(decision.action, "stub")
            self.assertEqual(decision.reason, "exact")

    def test_tool_interceptor_records_success_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run))
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            ok = interceptor.call_tool("add", {"a": 1, "b": 2}, ctx, lambda a, b: a + b)
            bad = interceptor.call_tool("boom", {}, ctx, lambda: (_ for _ in ()).throw(RuntimeError("boom")))

            self.assertEqual(ok.status, "success")
            self.assertEqual(bad.status, "error")
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_calls.jsonl"))), 2)
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))), 2)

    def test_file_read_event_keeps_returned_content_for_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.txt"
            path.write_text("a\nb\nc\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileStateTracker(Path(tmp) / "state")

            tracker.read_text_file(path, offset=2, limit=1, event_store=EventStore(run), turn_id="turn-0001")

            file_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
            self.assertEqual(file_events[0]["payload"]["returned_content"], "b")


if __name__ == "__main__":
    unittest.main()
