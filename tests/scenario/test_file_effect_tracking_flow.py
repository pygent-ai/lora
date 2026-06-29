from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.schema import CaseRunRef
from lora.runtime import ToolContext, ToolInterceptor
from lora.tracing import EventStore


class FileEffectTrackingScenarioTests(unittest.IsolatedAsyncioTestCase):
    """Scenario specs for net file-effect tracking around arbitrary tools."""

    async def test_tracked_tool_call_records_bash_net_workspace_effects_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            edited = workspace / "edited.txt"
            deleted = workspace / "deleted.txt"
            edited.write_text("old\n", encoding="utf-8")
            deleted.write_text("remove\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = _tracked_interceptor(run, workspace)
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            result = await interceptor.call_tool(
                "bash",
                {"command": "simulate file changes"},
                ctx,
                lambda command: _simulate_bash_net_effects(workspace),
            )

            self.assertEqual(result.status, "success")
            file_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
            self.assertEqual([event["type"] for event in file_events], ["file.write", "file.edit", "file.delete"])
            self.assertEqual([Path(event["path"]).name for event in file_events], ["created.txt", "edited.txt", "deleted.txt"])
            for event in file_events:
                self.assertEqual(event["payload"]["tool_call_id"], result.tool_call_id)
                self.assertEqual(event["payload"]["tool_name"], "bash")
                self.assertEqual(event["payload"]["detected_by"], ["snapshot_diff"])
                self.assertEqual(event["payload"]["confidence"], "observed")

    async def test_tracked_tool_call_records_effects_before_error_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = _tracked_interceptor(run, workspace)
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            result = await interceptor.call_tool(
                "bash",
                {"command": "partial failure"},
                ctx,
                lambda command: _simulate_partial_failure(workspace),
            )

            self.assertEqual(result.status, "error")
            file_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
            tool_results = list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
            self.assertEqual(len(file_events), 1)
            self.assertEqual(file_events[0]["type"], "file.write")
            self.assertEqual(Path(file_events[0]["path"]).name, "partial.txt")
            self.assertEqual(tool_results[0]["status"], "error")

    async def test_tracked_tool_call_merges_write_tool_args_with_observed_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            path = workspace / "declared.txt"
            path.write_text("old\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = _tracked_interceptor(run, workspace)
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            result = await interceptor.call_tool(
                "write",
                {"file_path": str(path), "content": "new\n"},
                ctx,
                lambda file_path, content: Path(file_path).write_text(content, encoding="utf-8"),
            )

            self.assertEqual(result.status, "success")
            file_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
            self.assertEqual(len(file_events), 1)
            self.assertEqual(file_events[0]["type"], "file.edit")
            self.assertEqual(file_events[0]["payload"]["detected_by"], ["tool_args", "snapshot_diff"])
            self.assertEqual(file_events[0]["payload"]["confidence"], "observed")

    async def test_tracked_tool_call_does_not_record_workspace_outside_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            outside = Path(tmp) / "outside.txt"
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = _tracked_interceptor(run, workspace)
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            result = await interceptor.call_tool(
                "bash",
                {"command": "write outside"},
                ctx,
                lambda command: outside.write_text("outside\n", encoding="utf-8"),
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl")), [])

    async def test_deferred_worker_records_effects_after_tool_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "c1" / "runs" / "r1"
            workspace = Path(tmp) / "workspace"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            workspace.mkdir()
            edited = workspace / "edited.txt"
            edited.write_text("old\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=run_dir)

            from lora.runtime.file_effects import DeferredFileEffectBatch, FileEffectBackgroundWorker, FileEffectBaselineStore
            from lora.runtime.tools import FileEffectTracker

            store = EventStore(run)
            tracker = FileEffectTracker(workspace_root=workspace, store=store)
            FileEffectBaselineStore(session_dir).save(tracker.snapshot_workspace())
            interceptor = ToolInterceptor(
                EventStore(run),
                workspace_root=workspace,
                track_file_effects=True,
                defer_file_effects=True,
            )
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            result = await interceptor.call_tool(
                "bash",
                {"command": "simulate edit"},
                ctx,
                lambda command: edited.write_text("new\n", encoding="utf-8"),
            )
            tool_results_before_worker = list(EventStore.iter_jsonl(run_dir / "tool_results.jsonl"))
            self.assertEqual(result.status, "success")
            self.assertEqual(tool_results_before_worker[0]["status"], "success")
            self.assertFalse((run_dir / "file_events.jsonl").exists())

            worker = FileEffectBackgroundWorker(session_dir=session_dir, workspace_root=workspace)
            worker.enqueue(
                DeferredFileEffectBatch.create(
                    case_run_ref=run,
                    workspace_root=workspace,
                    jobs=interceptor.drain_file_effect_jobs(),
                )
            )
            await worker.wait_idle()

            file_events = list(EventStore.iter_jsonl(run_dir / "file_events.jsonl"))
            self.assertEqual([event["type"] for event in file_events], ["file.edit"])
            self.assertEqual(file_events[0]["payload"]["tool_call_id"], result.tool_call_id)


def _tracked_interceptor(run: CaseRunRef, workspace: Path) -> ToolInterceptor:
    return ToolInterceptor(EventStore(run), workspace_root=workspace, track_file_effects=True)


def _simulate_bash_net_effects(workspace: Path) -> str:
    (workspace / "created.txt").write_text("first\n", encoding="utf-8")
    (workspace / "created.txt").write_text("final\n", encoding="utf-8")
    (workspace / "edited.txt").write_text("new\n", encoding="utf-8")
    (workspace / "deleted.txt").unlink()
    transient = workspace / "transient.txt"
    transient.write_text("temporary\n", encoding="utf-8")
    transient.unlink()
    return "done"


def _simulate_partial_failure(workspace: Path) -> str:
    (workspace / "partial.txt").write_text("created before failure\n", encoding="utf-8")
    raise RuntimeError("tool failed after modifying the workspace")


if __name__ == "__main__":
    unittest.main()
