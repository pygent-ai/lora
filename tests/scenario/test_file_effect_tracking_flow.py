from __future__ import annotations

import tempfile
import unittest
import inspect
from pathlib import Path
from types import SimpleNamespace

from pygent import ToolResult as PygentToolResult

from lora.schema import CaseRunRef
from lora.runtime import ToolObserver
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
            ctx = "turn-0001"

            result = await _call_and_record(interceptor,
                "bash",
                {"command": "echo changed > workspace-files"},
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
            ctx = "turn-0001"

            result = await _call_and_record(interceptor,
                "bash",
                {"command": "echo partial > partial.txt"},
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
            ctx = "turn-0001"

            result = await _call_and_record(interceptor,
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
            ctx = "turn-0001"

            result = await _call_and_record(interceptor,
                "bash",
                {"command": "write outside"},
                ctx,
                lambda command: outside.write_text("outside\n", encoding="utf-8"),
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl")), [])

    async def test_durable_batch_executor_records_effects_after_tool_result(self) -> None:
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

            from lora.runtime.file_effects import DeferredFileEffectBatch, FileEffectBaselineStore, process_file_effect_batch
            from lora.runtime.tools import FileEffectTracker

            store = EventStore(run)
            tracker = FileEffectTracker(workspace_root=workspace, store=store)
            FileEffectBaselineStore(session_dir).save(tracker.snapshot_workspace())
            interceptor = ToolObserver(
                EventStore(run),
                workspace_root=workspace,
                track_file_effects=True,
                defer_file_effects=True,
            )
            ctx = "turn-0001"

            result = await _call_and_record(interceptor,
                "bash",
                {"command": "echo new > edited.txt"},
                ctx,
                lambda command: edited.write_text("new\n", encoding="utf-8"),
                process_jobs=False,
            )
            tool_results_before_worker = list(EventStore.iter_jsonl(run_dir / "tool_results.jsonl"))
            self.assertEqual(result.status, "success")
            self.assertEqual(tool_results_before_worker[0]["status"], "success")
            self.assertFalse((run_dir / "file_events.jsonl").exists())

            process_file_effect_batch(
                DeferredFileEffectBatch.create(
                    case_run_ref=run,
                    workspace_root=workspace,
                    jobs=interceptor.drain_file_effect_jobs(),
                )
            )

            file_events = list(EventStore.iter_jsonl(run_dir / "file_events.jsonl"))
            self.assertEqual([event["type"] for event in file_events], ["file.edit"])
            self.assertEqual(file_events[0]["payload"]["tool_call_id"], result.tool_call_id)


def _tracked_interceptor(run: CaseRunRef, workspace: Path) -> ToolObserver:
    return ToolObserver(
        EventStore(run),
        workspace_root=workspace,
        track_file_effects=True,
        defer_file_effects=True,
    )


async def _call_and_record(
    interceptor: ToolObserver,
    name: str,
    args: dict[str, object],
    ctx: str,
    tool: object,
    *,
    process_jobs: bool = True,
) -> SimpleNamespace:
    """Drive the current Pygent-result -> durable Lora diff projection flow."""

    from lora.runtime.file_effects import (
        DeferredFileEffectBatch,
        FileEffectBaselineStore,
        process_file_effect_batch,
    )

    tracker = interceptor.file_effect_tracker
    if tracker is not None:
        session_dir = interceptor.store.session_dir or interceptor.store.run_dir.parent
        (session_dir / "session.json").touch(exist_ok=True)
        FileEffectBaselineStore(session_dir).save(tracker.snapshot_workspace())
    try:
        inspect.signature(tool).bind(**args)
        output = tool(**args)  # type: ignore[operator]
        if inspect.isawaitable(output):
            output = await output
        status = "succeeded"
        error = None
        error_kind = None
    except Exception as exc:  # noqa: BLE001 - synthetic Pygent boundary for this scenario.
        output = None
        status = "failed"
        error = str(exc)
        error_kind = type(exc).__name__
    payload = interceptor.record_framework_result(
        name,
        args,
        ctx,
        PygentToolResult(
            call_id=f"test-{name}",
            name=name,
            status=status,
            output=output,
            error=error,
            error_kind=error_kind,
            side_effect_committed=status == "succeeded",
        ),
    )
    jobs = interceptor.drain_file_effect_jobs()
    if jobs and process_jobs:
        process_file_effect_batch(
            DeferredFileEffectBatch.create(
                case_run_ref=interceptor.store.case_run_ref,
                workspace_root=tracker.workspace_root if tracker is not None else Path.cwd(),
                jobs=jobs,
            )
        )
    elif jobs:
        interceptor._file_effect_jobs.extend(jobs)
    return SimpleNamespace(
        status=payload["status"],
        result=payload.get("result"),
        error=payload.get("error"),
        tool_call_id=payload["tool_call_id"],
    )


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
