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

    def test_tool_interceptor_allows_outside_read_effects_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(EventStore(run), workspace_root=workspace, track_file_effects=True)
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            result = interceptor.call_tool(
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

    def test_file_read_event_keeps_returned_content_for_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.txt"
            path.write_text("a\nb\nc\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            tracker = FileStateTracker(Path(tmp) / "state")

            tracker.read_text_file(path, offset=2, limit=1, event_store=EventStore(run), turn_id="turn-0001")

            file_events = list(EventStore.iter_jsonl(Path(run.run_dir) / "file_events.jsonl"))
            self.assertEqual(file_events[0]["payload"]["returned_content"], "b")


class FileEffectTrackerSpecTests(unittest.TestCase):
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

    def test_file_effect_tracker_ignores_runtime_and_dependency_directories(self) -> None:
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


def _file_effect_tracker_class():
    import lora.tools as tools

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


if __name__ == "__main__":
    unittest.main()
