from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lora.evaluation import CaseManager
from lora.schema import CaseDefinition, CaseRunRef


class CaseManagerTests(unittest.TestCase):
    def test_load_validates_case_and_hash_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_path = Path(tmp) / "case.yaml"
            case_path.write_text(
                "id: c1\ntitle: Demo\ntype: e2e\ninput:\n  content: hello\nexpect:\n  answer:\n    contains:\n      - Echo\n",
                encoding="utf-8",
            )
            manager = CaseManager(tmp)
            case = manager.load(case_path)

            self.assertEqual(case.id, "c1")
            self.assertEqual(manager.case_hash(case), manager.case_hash(case))

    def test_invalid_case_reports_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_path = Path(tmp) / "case.yaml"
            case_path.write_text("id: c1\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "input"):
                CaseManager(tmp).load(case_path)

    def test_prepare_workspace_copies_fixture_and_writes_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixtures" / "case"
            fixture.mkdir(parents=True)
            (fixture / "a.txt").write_text("hello", encoding="utf-8")
            case_path = root / "case.yaml"
            case_path.write_text(
                "\n".join(
                    [
                        "id: c1",
                        "input:",
                        "  content: hello",
                        "workspace:",
                        "  setup:",
                        "    - type: copy_fixture",
                        "      from: fixtures/case",
                        "      to: work/case",
                        "expect:",
                        "  files:",
                        "    unchanged:",
                        "      - work/case/a.txt",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            manager = CaseManager(root)
            case = manager.load(case_path)
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=root / "run")

            workspace = manager.prepare_workspace(case, run)

            self.assertTrue((root / "work" / "case" / "a.txt").exists())
            baseline = json.loads(Path(workspace.baseline_path or "").read_text(encoding="utf-8"))
            self.assertTrue(baseline["work/case/a.txt"]["exists"])

    def test_prepare_workspace_supports_setup_actions_and_records_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stale-dir").mkdir()
            (root / "stale-dir" / "old.txt").write_text("old", encoding="utf-8")
            case_path = root / "case.yaml"
            case_path.write_text(
                "\n".join(
                    [
                        "id: setup-actions",
                        "input:",
                        "  content: hello",
                        "workspace:",
                        "  setup:",
                        "    - type: write_file",
                        "      path: work/input.txt",
                        "      content: hello from setup",
                        "    - type: mkdir",
                        "      path: work/out",
                        "    - type: delete_path",
                        "      path: stale-dir",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            manager = CaseManager(root)
            case = manager.load(case_path)
            run = CaseRunRef(session_id="s1", case_id="setup-actions", case_run_id="r1", run_dir=root / "run")

            manager.prepare_workspace(case, run)

            actions = _read_jsonl(root / "run" / "workspace" / "setup_actions.jsonl")
            self.assertEqual([item["type"] for item in actions], ["write_file", "mkdir", "delete_path"])
            self.assertEqual([item["status"] for item in actions], ["success", "success", "success"])
            self.assertEqual((root / "work" / "input.txt").read_text(encoding="utf-8"), "hello from setup")
            self.assertTrue((root / "work" / "out").is_dir())
            self.assertFalse((root / "stale-dir").exists())

    def test_prepare_workspace_records_failure_for_unknown_setup_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_path = root / "case.yaml"
            case_path.write_text(
                "id: bad-setup\ninput:\n  content: hello\nworkspace:\n  setup:\n    - type: nope\n",
                encoding="utf-8",
            )
            manager = CaseManager(root)
            case = manager.load(case_path)
            run = CaseRunRef(session_id="s1", case_id="bad-setup", case_run_id="r1", run_dir=root / "run")

            with self.assertRaisesRegex(ValueError, "Unsupported workspace setup type"):
                manager.prepare_workspace(case, run)

            actions = _read_jsonl(root / "run" / "workspace" / "setup_actions.jsonl")
            self.assertEqual(actions[0]["status"], "failure")
            self.assertEqual(actions[0]["error_type"], "ValueError")

    def test_prepare_workspace_rejects_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("nope", encoding="utf-8")
            case_path = root / "case.yaml"
            case_path.write_text(
                "id: escape\ninput:\n  content: hello\nworkspace:\n  setup:\n    - type: write_file\n      path: ../outside.txt\n      content: nope\n",
                encoding="utf-8",
            )
            manager = CaseManager(root)
            case = manager.load(case_path)
            run = CaseRunRef(session_id="s1", case_id="escape", case_run_id="r1", run_dir=root / "run")

            with self.assertRaisesRegex(ValueError, "workspace.setup\\[\\].path"):
                manager.prepare_workspace(case, run)

            self.assertEqual(outside.read_text(encoding="utf-8"), "nope")

    def test_prepare_workspace_rejects_copy_fixture_source_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            case_path = root / "case.yaml"
            case_path.write_text(
                "\n".join(
                    [
                        "id: escape-source",
                        "input:",
                        "  content: hello",
                        "workspace:",
                        "  setup:",
                        "    - type: copy_fixture",
                        "      from: ../outside.txt",
                        "      to: copied.txt",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            manager = CaseManager(root)
            case = manager.load(case_path)
            run = CaseRunRef(session_id="s1", case_id="escape-source", case_run_id="r1", run_dir=root / "run")

            with self.assertRaisesRegex(ValueError, "workspace.setup\\[\\].from"):
                manager.prepare_workspace(case, run)

            self.assertFalse((root / "copied.txt").exists())

    def test_prepare_workspace_rejects_copy_fixture_target_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            (root / "fixture.txt").write_text("content", encoding="utf-8")
            outside = Path(tmp) / "escaped.txt"
            case_path = root / "case.yaml"
            case_path.write_text(
                "\n".join(
                    [
                        "id: escape-target",
                        "input:",
                        "  content: hello",
                        "workspace:",
                        "  setup:",
                        "    - type: copy_fixture",
                        "      from: fixture.txt",
                        "      to: ../escaped.txt",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            manager = CaseManager(root)
            case = manager.load(case_path)
            run = CaseRunRef(session_id="s1", case_id="escape-target", case_run_id="r1", run_dir=root / "run")

            with self.assertRaisesRegex(ValueError, "workspace.setup\\[\\].to"):
                manager.prepare_workspace(case, run)

            self.assertFalse(outside.exists())

    def test_prepare_workspace_rejects_unchanged_baseline_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            (Path(tmp) / "outside.txt").write_text("secret", encoding="utf-8")
            case_path = root / "case.yaml"
            case_path.write_text(
                "\n".join(
                    [
                        "id: escape-baseline",
                        "input:",
                        "  content: hello",
                        "expect:",
                        "  files:",
                        "    unchanged:",
                        "      - ../outside.txt",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            manager = CaseManager(root)
            case = manager.load(case_path)
            run = CaseRunRef(session_id="s1", case_id="escape-baseline", case_run_id="r1", run_dir=root / "run")

            with self.assertRaisesRegex(ValueError, "expect.files.unchanged"):
                manager.prepare_workspace(case, run)

            self.assertFalse((root / "run" / "workspace" / "before_hashes.json").exists())

    def test_prepare_workspace_allows_absolute_paths_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            source = root / "fixtures" / "source.txt"
            source.parent.mkdir()
            source.write_text("content", encoding="utf-8")
            target = root / "work" / "target.txt"
            case = CaseDefinition(
                id="absolute-inside",
                title="absolute-inside",
                type="e2e",
                input={"content": "hello"},
                workspace={
                    "setup": [
                        {
                            "type": "copy_fixture",
                            "from": str(source),
                            "to": str(target),
                        }
                    ]
                },
                expect={"files": {"unchanged": [str(target)]}},
            )
            manager = CaseManager(root)
            run = CaseRunRef(session_id="s1", case_id="absolute-inside", case_run_id="r1", run_dir=root / "run")

            workspace = manager.prepare_workspace(case, run)

            self.assertEqual(target.read_text(encoding="utf-8"), "content")
            baseline = json.loads(Path(workspace.baseline_path or "").read_text(encoding="utf-8"))
            self.assertTrue(baseline[str(target)]["exists"])

    def test_run_command_requires_allow_commands_and_records_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            disabled_case_path = root / "disabled.yaml"
            disabled_case_path.write_text(
                "id: disabled-command\ninput:\n  content: hello\nworkspace:\n  setup:\n    - type: run_command\n      command: echo nope\n",
                encoding="utf-8",
            )
            manager = CaseManager(root)
            disabled = manager.load(disabled_case_path)
            disabled_run = CaseRunRef(session_id="s1", case_id="disabled-command", case_run_id="r1", run_dir=root / "run1")

            with self.assertRaisesRegex(ValueError, "allow_commands"):
                manager.prepare_workspace(disabled, disabled_run)

            enabled_case_path = root / "enabled.yaml"
            enabled_case_path.write_text(
                "\n".join(
                    [
                        "id: enabled-command",
                        "input:",
                        "  content: hello",
                        "workspace:",
                        "  allow_commands: true",
                        "  setup:",
                        "    - type: run_command",
                        "      command: echo setup-ok",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            enabled = manager.load(enabled_case_path)
            enabled_run = CaseRunRef(session_id="s1", case_id="enabled-command", case_run_id="r2", run_dir=root / "run2")

            manager.prepare_workspace(enabled, enabled_run)

            actions = _read_jsonl(root / "run2" / "workspace" / "setup_actions.jsonl")
            self.assertEqual(actions[0]["status"], "success")
            self.assertEqual(actions[0]["exit_code"], 0)
            self.assertIn("setup-ok", actions[0]["stdout"])

    def test_run_command_failure_records_exit_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_path = root / "case.yaml"
            case_path.write_text(
                "\n".join(
                    [
                        "id: command-failure",
                        "input:",
                        "  content: hello",
                        "workspace:",
                        "  allow_commands: true",
                        "  setup:",
                        "    - type: run_command",
                        "      command: exit 7",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            manager = CaseManager(root)
            case = manager.load(case_path)
            run = CaseRunRef(session_id="s1", case_id="command-failure", case_run_id="r1", run_dir=root / "run")

            with self.assertRaisesRegex(RuntimeError, "exit code 7"):
                manager.prepare_workspace(case, run)

            actions = _read_jsonl(root / "run" / "workspace" / "setup_actions.jsonl")
            self.assertEqual(actions[0]["status"], "failure")
            self.assertEqual(actions[0]["exit_code"], 7)
            self.assertIn("stdout", actions[0])
            self.assertIn("stderr", actions[0])


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
