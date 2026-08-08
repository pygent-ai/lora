from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.runtime.file_effects import (
    DeferredFileEffectBatch,
    DeferredFileEffectJob,
    FileEffectBaselineStore,
)
from lora.runtime.tools import FileSnapshot
from lora.schema import CaseRunRef


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


if __name__ == "__main__":
    unittest.main()
