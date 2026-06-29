from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.runtime.file_effects import (
    DeferredFileEffectBatch,
    DeferredFileEffectJob,
    FileEffectBaselineStore,
    FileEffectPendingStore,
    pending_file_effect_batches,
)
from lora.runtime.tools import FileSnapshot
from lora.schema import CaseRunRef


async def _raise_snapshot_failure(tracker):
    raise RuntimeError("snapshot exploded")


class FileEffectStateTests(unittest.IsolatedAsyncioTestCase):
    def test_pending_store_tracks_batch_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            run_dir.mkdir(parents=True)
            run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=run_dir)
            store = FileEffectPendingStore(session_dir)

            store.mark_queued(
                batch_id="batch-1",
                case_run_ref=run,
                turn_id="turn-0001",
                tool_call_ids=["tool-1"],
            )
            store.mark_running("batch-1")
            store.mark_completed("batch-1")

            state = store.read()
            self.assertEqual(state["batches"]["batch-1"]["status"], "completed")
            self.assertEqual(state["batches"]["batch-1"]["case_run_id"], "r1")
            self.assertEqual(pending_file_effect_batches(run, scope="run"), [])

    def test_pending_query_filters_run_session_and_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            first_run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            second_run_dir = session_dir / "cases" / "chat" / "runs" / "r2"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            first_run_dir.mkdir(parents=True)
            second_run_dir.mkdir(parents=True)
            first = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=first_run_dir)
            second = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r2", run_dir=second_run_dir)
            store = FileEffectPendingStore(session_dir)

            store.mark_queued(batch_id="first", case_run_ref=first, turn_id="turn-0001", tool_call_ids=["tool-1"])
            store.mark_queued(batch_id="second", case_run_ref=second, turn_id="turn-0002", tool_call_ids=["tool-2"])

            self.assertEqual([row["batch_id"] for row in pending_file_effect_batches(first, scope="run")], ["first"])
            self.assertEqual(
                [row["batch_id"] for row in pending_file_effect_batches(first, scope="turn", turn_id="turn-0001")],
                ["first"],
            )
            self.assertEqual(
                [row["batch_id"] for row in pending_file_effect_batches(first, scope="session")],
                ["first", "second"],
            )

    def test_baseline_store_round_trips_file_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            store = FileEffectBaselineStore(session_dir)
            snapshot = FileSnapshot(
                path=str((Path(tmp) / "workspace" / "demo.txt").resolve()),
                exists=True,
                kind="file",
                size=4,
                mtime_ns=123,
                content_hash="hash-1",
                content="demo",
                content_available=True,
            )

            store.save({snapshot.path: snapshot})

            loaded = store.load()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded[snapshot.path].content_hash, "hash-1")
            self.assertEqual(loaded[snapshot.path].content, "demo")

    def test_batch_create_records_tool_call_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=Path(tmp) / "run")
            job = DeferredFileEffectJob(
                tool_call_id="tool-1",
                tool_name="bash",
                args={"command": "echo hi"},
                turn_id="turn-0001",
                declared=[],
            )

            batch = DeferredFileEffectBatch.create(case_run_ref=run, workspace_root=Path(tmp), jobs=[job])

            self.assertEqual(batch.case_run_ref.case_run_id, "r1")
            self.assertEqual(batch.turn_id, "turn-0001")
            self.assertEqual(batch.tool_call_ids, ["tool-1"])

    async def test_worker_processes_batch_and_records_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            workspace = Path(tmp) / "workspace"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            workspace.mkdir()
            path = workspace / "demo.txt"
            path.write_text("old\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=run_dir)

            from lora.runtime.file_effects import FileEffectBackgroundWorker
            from lora.runtime.tools import FileEffectTracker
            from lora.tracing import EventStore

            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            FileEffectBaselineStore(session_dir).save(tracker.snapshot_workspace())
            path.write_text("new\n", encoding="utf-8")
            worker = FileEffectBackgroundWorker(session_dir=session_dir, workspace_root=workspace)
            job = DeferredFileEffectJob(
                tool_call_id="tool-bash",
                tool_name="bash",
                args={"command": "rewrite demo"},
                turn_id="turn-0001",
                declared=[],
            )

            worker.enqueue(DeferredFileEffectBatch.create(case_run_ref=run, workspace_root=workspace, jobs=[job]))
            await worker.wait_idle()

            file_events = list(EventStore.iter_jsonl(run_dir / "file_events.jsonl"))
            diff_events = list(EventStore.iter_jsonl(run_dir / "diffs" / "diff_events.jsonl"))
            self.assertEqual([event["type"] for event in file_events], ["file.edit"])
            self.assertEqual(file_events[0]["payload"]["tool_call_id"], "tool-bash")
            self.assertEqual(diff_events[0]["change_type"], "edit")
            self.assertEqual(pending_file_effect_batches(run, scope="run"), [])

    async def test_worker_marks_failure_without_raising_to_caller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            workspace = Path(tmp) / "workspace"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=run_dir)

            from lora.runtime.file_effects import FileEffectBackgroundWorker

            worker = FileEffectBackgroundWorker(session_dir=session_dir, workspace_root=workspace, snapshot_timeout_seconds=0.001)
            worker._snapshot_workspace = _raise_snapshot_failure
            job = DeferredFileEffectJob(
                tool_call_id="tool-bash",
                tool_name="bash",
                args={"command": "fail tracking"},
                turn_id="turn-0001",
                declared=[],
            )

            worker.enqueue(DeferredFileEffectBatch.create(case_run_ref=run, workspace_root=workspace, jobs=[job]))
            await worker.wait_idle()

            state = FileEffectPendingStore(session_dir).read()
            row = next(iter(state["batches"].values()))
            self.assertEqual(row["status"], "failed")
            self.assertIn("snapshot exploded", row["error"])


if __name__ == "__main__":
    unittest.main()
