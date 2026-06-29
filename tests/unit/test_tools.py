from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from lora.schema import CaseRunRef
from lora.tracing import DiffTool
from lora.runtime import FileStateTracker, ReadRange, ToolContext, ToolInterceptor
from lora.tracing import EventStore


class ToolTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_tool_interceptor_records_success_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run))
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            ok = await interceptor.call_tool("add", {"a": 1, "b": 2}, ctx, lambda a, b: a + b)
            bad = await interceptor.call_tool("boom", {}, ctx, lambda: (_ for _ in ()).throw(RuntimeError("boom")))

            self.assertEqual(ok.status, "success")
            self.assertEqual(bad.status, "error")
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_calls.jsonl"))), 2)
            self.assertEqual(len(list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))), 2)

    async def test_tool_interceptor_awaits_async_tool_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run))
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            async def echo(message: str) -> str:
                return f"echo:{message}"

            result = await interceptor.call_tool("echo", {"message": "hi"}, ctx, echo)

            self.assertEqual(result.status, "success")
            self.assertEqual(result.result, "echo:hi")
            tool_result = next(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
            self.assertEqual(tool_result["result"], "echo:hi")

    async def test_tool_interceptor_records_model_tool_call_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run))
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            await interceptor.call_tool(
                "add",
                {"a": 1, "b": 2},
                ctx,
                lambda a, b: a + b,
                model_tool_call_id="call_model_add",
            )

            tool_call = next(EventStore.iter_jsonl(Path(run.run_dir) / "tool_calls.jsonl"))
            tool_result = next(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
            self.assertEqual(tool_call["model_tool_call_id"], "call_model_add")
            self.assertEqual(tool_result["model_tool_call_id"], "call_model_add")

    async def test_tool_interceptor_reports_unknown_arguments_before_calling_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run))
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")
            called = False

            def bash(command: str) -> str:
                nonlocal called
                called = True
                return command

            result = await interceptor.call_tool("bash", {"raw_arguments": "{}"}, ctx, bash)

            self.assertFalse(called)
            self.assertEqual(result.status, "error")
            self.assertIn("unsupported argument", result.error or "")
            self.assertIn("raw_arguments", result.error or "")
            self.assertIn("command", result.error or "")

    async def test_tool_interceptor_maps_structured_tool_error_result_to_error_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run))
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            result = await interceptor.call_tool(
                "read",
                {"file_path": "missing.txt"},
                ctx,
                lambda file_path: _structured_tool_error(
                    "错误：文件不存在 missing.txt",
                    error_type="FileNotFoundError",
                    details={"input_path": file_path, "path": "E:\\missing.txt"},
                ),
            )

            self.assertEqual(result.status, "error")
            self.assertEqual(result.error, "错误：文件不存在 missing.txt")
            tool_result = next(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
            self.assertEqual(tool_result["status"], "error")
            self.assertEqual(tool_result["error"], "错误：文件不存在 missing.txt")
            self.assertEqual(tool_result["error_type"], "FileNotFoundError")
            self.assertEqual(tool_result["details"]["input_path"], "missing.txt")

    async def test_tool_interceptor_spools_large_bash_result_to_run_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run))
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")
            output = "\n".join(f"line-{index:04d}" for index in range(700))

            result = await interceptor.call_tool("bash", {"command": "generate lots"}, ctx, lambda command: output)

            self.assertEqual(result.status, "success")
            self.assertIsInstance(result.result, dict)
            payload = result.result
            self.assertTrue(payload["truncated"])
            self.assertEqual(payload["line_count"], 700)
            self.assertNotIn("line-0699", payload["preview"])
            self.assertIn("read", payload["next_step"])
            self.assertIn("offset", payload["next_step"])
            output_path = Path(payload["full_output_path"])
            self.assertTrue(output_path.exists())
            self.assertEqual(output_path.read_text(encoding="utf-8"), output)
            tool_result = next(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
            self.assertEqual(tool_result["result"]["full_output_path"], str(output_path))

    async def test_tool_interceptor_spools_large_grep_result_to_run_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run))
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")
            output = "\n".join(f"path/file.py:{index}|{'x' * 500}" for index in range(170))

            result = await interceptor.call_tool(
                "grep",
                {"pattern": "DEFAULT_PYGENT_TOOL_NAMES", "output_mode": "content"},
                ctx,
                lambda pattern, output_mode: output,
            )

            self.assertEqual(result.status, "success")
            self.assertIsInstance(result.result, dict)
            payload = result.result
            self.assertTrue(payload["truncated"])
            self.assertEqual(payload["line_count"], 170)
            self.assertEqual(payload["reason"], "grep output exceeded the model-visible result limit")
            self.assertNotIn("path/file.py:169", payload["preview"])
            output_path = Path(payload["full_output_path"])
            self.assertTrue(output_path.exists())
            self.assertEqual(output_path.read_text(encoding="utf-8"), output)

    async def test_tool_interceptor_keeps_small_grep_result_inline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run))
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")
            output = "src/lora/agent.py:32|DEFAULT_PYGENT_TOOL_NAMES = (\"bash\", \"read\")"

            result = await interceptor.call_tool(
                "grep",
                {"pattern": "DEFAULT_PYGENT_TOOL_NAMES"},
                ctx,
                lambda pattern: output,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.result, output)
            self.assertFalse((Path(run.run_dir) / "tool_outputs").exists())

    async def test_tool_interceptor_does_not_spool_allowlisted_large_bash_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run), bash_full_output_allowlist=["lora"])
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")
            output = "\n".join(f"line-{index:04d}" for index in range(700))

            result = await interceptor.call_tool("bash", {"command": "lora chat --message hello"}, ctx, lambda command: output)

            self.assertEqual(result.status, "success")
            self.assertEqual(result.result, output)
            self.assertFalse((Path(run.run_dir) / "tool_outputs").exists())
            tool_result = next(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
            self.assertEqual(tool_result["result"], output)

    async def test_tool_interceptor_allowlist_requires_command_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run), bash_full_output_allowlist=["lora"])
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")
            output = "\n".join(f"line-{index:04d}" for index in range(700))

            result = await interceptor.call_tool("bash", {"command": "lorax chat"}, ctx, lambda command: output)

            self.assertIsInstance(result.result, dict)
            self.assertTrue(result.result["truncated"])

    async def test_tool_interceptor_allows_outside_read_effects_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run), workspace_root=workspace, track_file_effects=True)
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            result = await interceptor.call_tool(
                "read",
                {"path": str(outside)},
                ctx,
                lambda path: Path(path).read_text(encoding="utf-8"),
            )

            self.assertEqual(result.status, "success")
            file_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
            self.assertEqual(len(file_events), 1)
            self.assertEqual(file_events[0]["type"], "file.read")
            self.assertEqual(file_events[0]["path"], str(outside.resolve()))

    async def test_tool_interceptor_deferred_mode_does_not_snapshot_before_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(
                EventStore(run),
                workspace_root=workspace,
                track_file_effects=True,
                defer_file_effects=True,
            )
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            with unittest.mock.patch(
                "lora.runtime.tools.FileEffectTracker.snapshot_workspace",
                side_effect=AssertionError("snapshot should be deferred"),
            ):
                result = await interceptor.call_tool("bash", {"command": "echo hi"}, ctx, lambda command: "ok")

            self.assertEqual(result.status, "success")
            tool_results = list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
            self.assertEqual(tool_results[0]["status"], "success")
            self.assertFalse((Path(run.run_dir) / "file_events.jsonl").exists())

    async def test_tool_interceptor_drain_file_effect_jobs_returns_declared_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            path = workspace / "demo.txt"
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(
                EventStore(run),
                workspace_root=workspace,
                track_file_effects=True,
                defer_file_effects=True,
            )
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            await interceptor.call_tool(
                "write",
                {"file_path": str(path), "content": "hello\n"},
                ctx,
                lambda file_path, content: Path(file_path).write_text(content, encoding="utf-8"),
            )

            jobs = interceptor.drain_file_effect_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].tool_name, "write")
            self.assertEqual(jobs[0].turn_id, "turn-0001")
            self.assertEqual(jobs[0].declared[0].type, "file.write")
            self.assertEqual(interceptor.drain_file_effect_jobs(), [])

    async def test_tool_interceptor_deferred_mode_skips_read_only_bash_snapshot_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(
                EventStore(run),
                workspace_root=workspace,
                track_file_effects=True,
                defer_file_effects=True,
            )
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            await interceptor.call_tool(
                "bash",
                {"command": "rg -n \"DiffTool\" src/lora"},
                ctx,
                lambda command: "src/lora/tracing/diffing.py:135:class DiffTool",
            )

            self.assertEqual(interceptor.drain_file_effect_jobs(), [])

    def test_file_read_event_keeps_returned_content_for_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.txt"
            path.write_text("a\nb\nc\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileStateTracker(Path(tmp) / "state")

            tracker.read_text_file(path, offset=2, limit=1, event_store=EventStore(run), turn_id="turn-0001")

            file_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
            self.assertEqual(file_events[0]["payload"]["returned_content"], "b")


class FileEffectTrackerSpecTests(unittest.IsolatedAsyncioTestCase):
    """Specification tests for the planned workspace file-effect tracker."""

    def test_file_effect_tracker_records_one_net_write_per_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before = tracker.snapshot_workspace()

            path = workspace / "demo.txt"
            path.write_text("first\n", encoding="utf-8")
            path.write_text("second\n", encoding="utf-8")
            after = tracker.snapshot_workspace()

            effects = tracker.observed_effects(before, after, tool_name="bash", tool_call_id="evt_tool")
            tracker.append_effects(effects, turn_id="turn-0001")

            file_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
            self.assertEqual(len(file_events), 1)
            self.assertEqual(file_events[0]["type"], "file.write")
            self.assertEqual(file_events[0]["path"], str(path.resolve()))
            self.assertEqual(file_events[0]["payload"]["after_exists"], True)
            self.assertEqual(file_events[0]["payload"]["detected_by"], ["snapshot_diff"])

    def test_file_effect_tracker_merges_declared_and_observed_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            path = workspace / "demo.txt"
            path.write_text("old\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before = tracker.snapshot_workspace()

            declared = tracker.declared_effects(
                "edit",
                {"file_path": str(path), "old_string": "old", "new_string": "new"},
                tool_call_id="evt_tool",
            )
            path.write_text("new\n", encoding="utf-8")
            observed = tracker.observed_effects(
                before,
                tracker.snapshot_workspace(),
                tool_name="edit",
                tool_call_id="evt_tool",
            )

            merged = tracker.merge_effects(declared, observed)

            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0].type, "file.edit")
            self.assertEqual(merged[0].confidence, "observed")
            self.assertEqual(merged[0].detected_by, ["tool_args", "snapshot_diff"])

    def test_file_effect_tracker_records_delete_once_and_ignores_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            path = workspace / "demo.txt"
            path.write_text("bye\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before_delete = tracker.snapshot_workspace()

            path.unlink()
            after_delete = tracker.snapshot_workspace()
            effects = tracker.observed_effects(before_delete, after_delete, tool_name="bash", tool_call_id="evt_tool")
            tracker.append_effects(effects, turn_id="turn-0001")
            noop_effects = tracker.observed_effects(after_delete, tracker.snapshot_workspace(), tool_name="bash", tool_call_id="evt_tool")
            tracker.append_effects(noop_effects, turn_id="turn-0001")

            file_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
            self.assertEqual(len(file_events), 1)
            self.assertEqual(file_events[0]["type"], "file.delete")
            self.assertEqual(file_events[0]["payload"]["before_exists"], True)
            self.assertEqual(file_events[0]["payload"]["after_exists"], False)

    def test_file_effect_tracker_records_create_edit_delete_in_one_call_as_no_net_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before = tracker.snapshot_workspace()

            path = workspace / "transient.txt"
            path.write_text("temporary\n", encoding="utf-8")
            path.write_text("changed\n", encoding="utf-8")
            path.unlink()
            after = tracker.snapshot_workspace()

            effects = tracker.observed_effects(before, after, tool_name="bash", tool_call_id="evt_tool")

            self.assertEqual(effects, [])

    def test_file_effect_tracker_records_existing_file_changed_back_to_original_as_no_net_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            path = workspace / "stable.txt"
            path.write_text("original\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before = tracker.snapshot_workspace()

            path.write_text("temporary\n", encoding="utf-8")
            path.write_text("original\n", encoding="utf-8")
            after = tracker.snapshot_workspace()

            effects = tracker.observed_effects(before, after, tool_name="bash", tool_call_id="evt_tool")

            self.assertEqual(effects, [])

    def test_file_effect_tracker_records_multiple_paths_once_each_in_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            edited = workspace / "b.txt"
            deleted = workspace / "c.txt"
            edited.write_text("old\n", encoding="utf-8")
            deleted.write_text("remove\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before = tracker.snapshot_workspace()

            (workspace / "a.txt").write_text("new\n", encoding="utf-8")
            edited.write_text("new\n", encoding="utf-8")
            deleted.unlink()
            effects = tracker.observed_effects(before, tracker.snapshot_workspace(), tool_name="bash", tool_call_id="evt_tool")

            self.assertEqual([effect.type for effect in effects], ["file.write", "file.edit", "file.delete"])
            self.assertEqual([Path(effect.path).name for effect in effects], ["a.txt", "b.txt", "c.txt"])

    def test_file_effect_tracker_normalizes_equivalent_declared_paths_before_merging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            nested = workspace / "nested"
            nested.mkdir(parents=True)
            path = nested / "demo.txt"
            path.write_text("old\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before = tracker.snapshot_workspace()

            declared = tracker.declared_effects(
                "write",
                {"file_path": str(workspace / "nested" / ".." / "nested" / "demo.txt"), "content": "new\n"},
                tool_call_id="evt_tool",
            )
            path.write_text("new\n", encoding="utf-8")
            observed = tracker.observed_effects(before, tracker.snapshot_workspace(), tool_name="write", tool_call_id="evt_tool")
            merged = tracker.merge_effects(declared, observed)

            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0].path, str(path.resolve()))
            self.assertEqual(merged[0].detected_by, ["tool_args", "snapshot_diff"])

    def test_file_effect_tracker_accepts_common_windows_path_styles_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            path = workspace / "nested" / "README.md"
            path.parent.mkdir()
            path.write_text("hello\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))

            path_styles = [
                "nested/README.md",
                ".\\nested\\README.md",
                str(path),
                str(path).replace("\\", "/"),
            ]
            path_styles.extend(_windows_shell_path_styles(path))

            for raw_path in path_styles:
                with self.subTest(raw_path=raw_path):
                    effects = tracker.declared_effects("read", {"path": raw_path}, tool_call_id="evt_tool")

                    self.assertEqual(len(effects), 1)
                    self.assertEqual(effects[0].path, str(path.resolve()))

    def test_file_effect_tracker_accepts_windows_shell_style_paths_in_bash_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            path = workspace / "README.md"
            path.write_text("hello\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            raw_paths = _windows_shell_path_styles(path)
            if not raw_paths:
                self.skipTest("drive-rooted shell path styles are Windows-specific")

            for raw_path in raw_paths:
                with self.subTest(raw_path=raw_path):
                    effects = tracker.declared_effects("bash", {"command": f"cat {raw_path}"}, tool_call_id="evt_tool")

                    self.assertEqual(len(effects), 1)
                    self.assertEqual(effects[0].path, str(path.resolve()))

    def test_file_effect_tracker_ignores_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before = tracker.snapshot_workspace()

            for ignored in [".git", ".lora", ".venv", "__pycache__", ".pytest_cache", "sessions"]:
                ignored_dir = workspace / ignored
                ignored_dir.mkdir()
                (ignored_dir / "generated.txt").write_text("noise\n", encoding="utf-8")
            after = tracker.snapshot_workspace()

            self.assertEqual(tracker.observed_effects(before, after, tool_name="bash", tool_call_id="evt_tool"), [])

    def test_file_effect_tracker_tracks_node_modules_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before = tracker.snapshot_workspace()

            package_dir = workspace / "node_modules" / "pkg"
            package_dir.mkdir(parents=True)
            package_file = package_dir / "index.js"
            package_file.write_text("module.exports = 1;\n", encoding="utf-8")
            after = tracker.snapshot_workspace()

            effects = tracker.observed_effects(before, after, tool_name="bash", tool_call_id="evt_tool")

            self.assertEqual(len(effects), 1)
            self.assertEqual(effects[0].type, "file.write")
            self.assertEqual(effects[0].path, str(package_file.resolve()))

    def test_file_effect_tracker_rejects_declared_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            outside = Path(tmp) / "outside.txt"
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))

            with self.assertRaises(ValueError):
                tracker.declared_effects("write", {"file_path": str(outside), "content": "x"}, tool_call_id="evt_tool")

    def test_file_effect_tracker_allows_outside_read_paths_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("x", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))

            effects = tracker.declared_effects("read", {"path": str(outside)}, tool_call_id="evt_tool")

            self.assertEqual(len(effects), 1)
            self.assertEqual(effects[0].type, "file.read")
            self.assertEqual(effects[0].path, str(outside.resolve()))

    def test_file_effect_tracker_can_reject_outside_read_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("x", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(
                workspace_root=workspace,
                store=EventStore(run),
                allow_read_outside_workspace=False,
            )

            with self.assertRaises(ValueError):
                tracker.declared_effects("read", {"path": str(outside)}, tool_call_id="evt_tool")

    def test_file_effect_tracker_rejects_windows_shell_style_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("x", encoding="utf-8")
            raw_paths = _windows_shell_path_styles(outside)
            if not raw_paths:
                self.skipTest("drive-rooted shell path styles are Windows-specific")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(
                workspace_root=workspace,
                store=EventStore(run),
                allow_read_outside_workspace=False,
            )

            for raw_path in raw_paths:
                with self.subTest(raw_path=raw_path):
                    with self.assertRaises(ValueError):
                        tracker.declared_effects("read", {"path": raw_path}, tool_call_id="evt_tool")

    def test_file_effect_tracker_infers_bash_read_without_claiming_observed_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            path = workspace / "readme.txt"
            path.write_text("hello\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before = tracker.snapshot_workspace()

            declared = tracker.declared_effects("bash", {"command": f"cat {path}"}, tool_call_id="evt_tool")
            observed = tracker.observed_effects(before, tracker.snapshot_workspace(), tool_name="bash", tool_call_id="evt_tool")
            merged = tracker.merge_effects(declared, observed)

            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0].type, "file.read")
            self.assertEqual(merged[0].confidence, "inferred")
            self.assertEqual(merged[0].detected_by, ["bash_command_parse"])

    def test_file_effect_tracker_deduplicates_same_effect_key_across_append_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before = tracker.snapshot_workspace()
            (workspace / "demo.txt").write_text("content\n", encoding="utf-8")
            effects = tracker.observed_effects(before, tracker.snapshot_workspace(), tool_name="bash", tool_call_id="evt_tool")

            tracker.append_effects(effects, turn_id="turn-0001")
            tracker.append_effects(effects, turn_id="turn-0001")

            file_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
            self.assertEqual(len(file_events), 1)

    async def test_file_effect_tracker_persists_patch_for_edit_and_diff_tool_returns_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            path = workspace / "demo.txt"
            path.write_text("old\nsame\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            store = EventStore(run)
            tracker = FileEffectTracker(workspace_root=workspace, store=store)
            before = tracker.snapshot_workspace()

            path.write_text("new\nsame\n", encoding="utf-8")
            effects = tracker.observed_effects(before, tracker.snapshot_workspace(), tool_name="edit", tool_call_id="evt_tool")
            tracker.append_effects(effects, turn_id="turn-0001")

            diff_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "diffs" / "diff_events.jsonl"))
            file_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
            result = await DiffTool(case_run_ref=run, workspace_root=workspace).forward(format="patch")

            self.assertEqual(len(file_events), 1)
            self.assertEqual(len(diff_events), 1)
            self.assertEqual(diff_events[0]["change_type"], "edit")
            self.assertTrue(Path(diff_events[0]["snapshot_before_path"]).exists())
            self.assertTrue(Path(diff_events[0]["snapshot_after_path"]).exists())
            self.assertTrue(Path(diff_events[0]["patch_path"]).exists())
            self.assertEqual(result["count"], 1)
            self.assertIn("--- a/demo.txt\n+++ b/demo.txt\n", result["patch"])
            self.assertIn("-old", result["patch"])
            self.assertIn("+new", result["patch"])

    async def test_diff_tool_reads_session_level_diff_index_after_recreation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            FileEffectTracker = _file_effect_tracker_class()
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "c1" / "runs" / "r1"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            path = workspace / "created.txt"
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=run_dir)
            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            before = tracker.snapshot_workspace()

            path.write_text("hello\n", encoding="utf-8")
            effects = tracker.observed_effects(before, tracker.snapshot_workspace(), tool_name="write", tool_call_id="evt_tool")
            tracker.append_effects(effects, turn_id="turn-0001")

            recreated = DiffTool(case_run_ref=run, workspace_root=workspace)
            result = await recreated.forward(scope="session", format="summary")

            self.assertEqual(result["count"], 1)
            self.assertEqual(result["diffs"][0]["path"], "created.txt")
            self.assertEqual(result["diffs"][0]["change_type"], "write")

    def test_diff_tool_schema_exposes_scope_and_format_enums(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")

            schema = DiffTool(case_run_ref=run, workspace_root=workspace).to_openai_function()
            properties = schema["parameters"]["properties"]

            self.assertEqual(properties["scope"]["enum"], ["turn", "run", "session"])
            self.assertEqual(properties["format"]["enum"], ["summary", "patch", "json"])
            self.assertEqual(properties["limit"]["minimum"], 1)
            self.assertFalse(schema["parameters"]["additionalProperties"])


def _file_effect_tracker_class():
    import lora.runtime.tools as tools

    try:
        return tools.FileEffectTracker
    except AttributeError as exc:
        raise AssertionError("FileEffectTracker is not implemented yet") from exc


def _windows_shell_path_styles(path: Path) -> list[str]:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if not drive:
        return []
    tail = "/".join(resolved.parts[1:])
    return [f"/{drive}/{tail}", f"/mnt/{drive}/{tail}", f"/cygdrive/{drive}/{tail}"]


def _structured_tool_error(message: str, *, error_type: str, details: dict[str, str]) -> str:
    class StructuredToolError(str):
        pass

    error = StructuredToolError(message)
    error.error_type = error_type  # type: ignore[attr-defined]
    error.details = details  # type: ignore[attr-defined]
    return error


if __name__ == "__main__":
    unittest.main()
