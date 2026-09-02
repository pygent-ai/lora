from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.runtime.file_effects import (
    DeferredFileEffectBatch,
    DeferredFileEffectJob,
    FileEffectBaselineStore,
    process_file_effect_batch,
)
from lora.runtime.file_effect_models import FileEffect
from lora.runtime.tools import FileEffectTracker, FileSnapshot
from lora.schema import CaseRunRef
from lora.tracing import EventStore


class FileEffectStateTests(unittest.IsolatedAsyncioTestCase):
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

    def test_process_declared_only_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            workspace = Path(tmp) / "workspace"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=run_dir)

            from lora.runtime.file_effects import process_file_effect_batch

            job = DeferredFileEffectJob(
                tool_call_id="tool-read",
                tool_name="read",
                args={"file_path": "README.md"},
                turn_id="turn-0001",
                declared=[],
                requires_snapshot=False,
            )

            batch = DeferredFileEffectBatch.create(case_run_ref=run, workspace_root=workspace, jobs=[job])
            process_file_effect_batch(batch)

    def test_first_declared_write_is_recorded_as_new_and_seeds_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            workspace = Path(tmp) / "workspace"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            workspace.mkdir()
            created = workspace / "born.txt"
            created.write_text("new\n", encoding="utf-8")
            run = CaseRunRef(
                session_id="s1",
                case_id="chat",
                case_run_id="r1",
                run_dir=run_dir,
            )
            declared = FileEffect(
                type="file.write",
                path=str(created.resolve()),
                tool_call_id="tool-write",
                tool_name="write",
                detected_by=["tool_args"],
                confidence="declared",
                before_exists=False,
                after_exists=True,
            )
            job = DeferredFileEffectJob(
                tool_call_id="tool-write",
                tool_name="write",
                args={"file_path": str(created)},
                turn_id="turn-0001",
                declared=[declared],
            )

            process_file_effect_batch(
                DeferredFileEffectBatch.create(
                    case_run_ref=run,
                    workspace_root=workspace,
                    jobs=[job],
                )
            )

            rows = list(EventStore.iter_jsonl(run_dir / "file_events.jsonl"))
            self.assertEqual([(row["type"], row["path"]) for row in rows], [
                ("file.write", str(created.resolve()))
            ])
            baseline = FileEffectBaselineStore(session_dir).load()
            self.assertIsNotNone(baseline)
            self.assertIn(str(created.resolve()), baseline)

    def test_multi_tool_batch_preserves_declared_tool_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            workspace = Path(tmp) / "workspace"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            workspace.mkdir()
            first = workspace / "first.txt"
            second = workspace / "second.txt"
            first.write_text("before", encoding="utf-8")
            second.write_text("before", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=run_dir)
            tracker = FileEffectTracker(workspace, EventStore(run))
            FileEffectBaselineStore(session_dir).save(tracker.snapshot_workspace())
            first.write_text("after", encoding="utf-8")
            second.write_text("after", encoding="utf-8")

            jobs = [
                DeferredFileEffectJob(
                    tool_call_id=call_id,
                    tool_name="edit",
                    args={"file_path": str(path)},
                    turn_id="turn-1",
                    declared=[
                        FileEffect(
                            type="file.edit",
                            path=str(path.resolve()),
                            tool_call_id=call_id,
                            tool_name="edit",
                            detected_by=["tool_args"],
                            confidence="declared",
                        )
                    ],
                )
                for call_id, path in (("call-1", first), ("call-2", second))
            ]

            process_file_effect_batch(
                DeferredFileEffectBatch.create(
                    case_run_ref=run,
                    workspace_root=workspace,
                    jobs=jobs,
                )
            )

            rows = list(EventStore.iter_jsonl(run_dir / "file_events.jsonl"))
            owners = {
                Path(row["path"]).name: row["payload"]["tool_call_id"]
                for row in rows
            }
            self.assertEqual(owners, {"first.txt": "call-1", "second.txt": "call-2"})


if __name__ == "__main__":
    unittest.main()
