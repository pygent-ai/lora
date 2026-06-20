from __future__ import annotations

import copy
import tempfile
import tomllib
import unittest
from pathlib import Path

from lora.evaluation import CaseManager
from lora.config import load_mapping_file, load_run_config
from lora.core.redaction import REDACTED, redact_secrets
from lora.schema import CaseRunRef, ContextEvent, RunConfig
from lora.sessions import SessionManager
from lora.tracing import EventStore


class DocumentationRegressionFindingTests(unittest.TestCase):
    def test_pyproject_declares_test_runner_used_by_docs(self) -> None:
        root = Path(__file__).resolve().parents[2]
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        dependency_groups = pyproject.get("dependency-groups", {})
        optional_dependencies = pyproject.get("project", {}).get("optional-dependencies", {})
        declared = [
            *pyproject.get("project", {}).get("dependencies", []),
            *dependency_groups.get("dev", []),
            *optional_dependencies.get("dev", []),
            *optional_dependencies.get("test", []),
        ]

        self.assertTrue(
            any(str(item).split("==", 1)[0].split(">=", 1)[0] == "pytest" for item in declared),
            "README and local workflow use pytest, but pyproject.toml does not declare it",
        )


class ConfigParserRegressionFindingTests(unittest.TestCase):
    def test_relative_config_file_is_resolved_from_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text("runtime:\n  model: workspace-model\n", encoding="utf-8")

            config = load_run_config(workspace_root=root, config_file="lora.yaml")

        self.assertEqual(config.model, "workspace-model")

    def test_yaml_parser_preserves_hash_inside_quoted_scalar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.yaml"
            path.write_text('input:\n  content: "keep # this text"\n', encoding="utf-8")

            data = load_mapping_file(path)

        self.assertEqual(data["input"]["content"], "keep # this text")

    def test_yaml_parser_preserves_hash_inside_single_quoted_scalar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.yaml"
            path.write_text("input:\n  content: 'keep # this text'\n", encoding="utf-8")

            data = load_mapping_file(path)

        self.assertEqual(data["input"]["content"], "keep # this text")


class WorkspacePathSafetyRegressionFindingTests(unittest.TestCase):
    def test_case_manager_rejects_case_id_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_path = root / "case.yaml"
            case_path.write_text(
                "id: ../escaped\ninput:\n  content: hello\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                CaseManager(root).load(case_path)

    def test_prepare_workspace_rejects_fixture_source_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            case_path = root / "case.yaml"
            case_path.write_text(
                "\n".join(
                    [
                        "id: unsafe-source",
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
            run = CaseRunRef(session_id="s1", case_id=case.id, case_run_id="r1", run_dir=root / "run")

            with self.assertRaises(ValueError):
                manager.prepare_workspace(case, run)

    def test_prepare_workspace_rejects_fixture_target_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            (root / "fixture.txt").write_text("content", encoding="utf-8")
            case_path = root / "case.yaml"
            case_path.write_text(
                "\n".join(
                    [
                        "id: unsafe-target",
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
            run = CaseRunRef(session_id="s1", case_id=case.id, case_run_id="r1", run_dir=root / "run")

            with self.assertRaises(ValueError):
                manager.prepare_workspace(case, run)

    def test_prepare_workspace_rejects_unchanged_baseline_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            (Path(tmp) / "outside.txt").write_text("secret", encoding="utf-8")
            case_path = root / "case.yaml"
            case_path.write_text(
                "\n".join(
                    [
                        "id: unsafe-baseline",
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
            run = CaseRunRef(session_id="s1", case_id=case.id, case_run_id="r1", run_dir=root / "run")

            with self.assertRaises(ValueError):
                manager.prepare_workspace(case, run)


class SessionPathSafetyRegressionFindingTests(unittest.TestCase):
    def test_load_rejects_session_id_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            escaped = Path(config.lora_root) / "outside"
            escaped.mkdir(parents=True)
            (escaped / "session.json").write_text(
                '{"session_id":"../outside","workspace_root":".","session_dir":".","created_at":"now","updated_at":"now"}',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                manager.load("../outside")

    def test_start_case_run_rejects_case_id_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora"))
            session = manager.create("safe")

            with self.assertRaises(ValueError):
                manager.start_case_run(session.session_id, "../../escaped-case")


class TraceRegressionFindingTests(unittest.TestCase):
    def test_append_rejects_unknown_event_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run"))

            with self.assertRaises(ValueError):
                store.append("totally.unknown", actor="system", payload={}, turn_id="turn-0001")

    def test_prompt_render_event_append_does_not_mutate_caller_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run"))
            payload = {"prompt": "hello", "prompt_hash": "h1", "module_ids": ["system.identity"]}
            event = ContextEvent(
                id="evt_prompt",
                session_id="s1",
                case_id="c1",
                case_run_id="r1",
                turn_id="turn-0001",
                type="prompt.rendered",
                timestamp="2026-05-29T00:00:00+00:00",
                actor="system",
                payload=copy.deepcopy(payload),
            )

            store.append(event)

            self.assertEqual(event.payload, payload)


class RedactionRegressionFindingTests(unittest.TestCase):
    def test_redacts_authorization_headers(self) -> None:
        value = {
            "headers": {
                "Authorization": "Bearer sk-1234567890abcdef1234567890abcdef",
                "X-Api-Key": "plain-secret-value",
            }
        }

        redacted = redact_secrets(value)

        self.assertEqual(redacted["headers"]["Authorization"], REDACTED)
        self.assertEqual(redacted["headers"]["X-Api-Key"], REDACTED)


if __name__ == "__main__":
    unittest.main()
