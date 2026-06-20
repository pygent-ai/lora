from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lora.evaluation import CaseManager
from lora.schema import RunConfig
from lora.sessions import SessionManager
from lora.evaluation import RegressionRegistrar, TestGenerator
from lora.tracing import EventStore


class TestGeneratorTests(unittest.TestCase):
    def test_generate_failed_run_writes_case_metadata_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(workspace_root=root, lora_root=root / ".lora")
            manager = SessionManager(config)
            source_case = _write_case(root / "case.yaml", expected="missing-token")
            session = manager.create("source-case")
            run = manager.start_case_run(session.session_id, "source-case", run_config=config)
            shutil.copy2(source_case, Path(run.run_dir) / "case.yaml")
            _write_json(
                Path(run.run_dir) / "verdict.json",
                {
                    "status": "failed",
                    "failures": [
                        {"type": "answer.contains", "expected": "missing-token", "message": "missing token"}
                    ],
                    "errors": [],
                },
            )

            result = TestGenerator(config=config, session_manager=manager).generate(session.session_id, run.case_run_id)

            self.assertEqual(result.status, "generated")
            generated_path = Path(result.generated_path or "")
            metadata_path = Path(result.metadata_path or "")
            self.assertTrue(generated_path.exists())
            self.assertTrue(metadata_path.exists())
            generated = CaseManager(root).load(generated_path)
            self.assertEqual(generated.id, "source-case-generated")
            self.assertEqual(generated.session["mode"], "new")
            self.assertEqual(generated.expect["answer"]["contains"], ["missing-token"])
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertFalse(metadata["registered"])
            events = EventStore(run).list_by_run()
            test_events = [event for event in events if event.type == "test.generated"]
            self.assertEqual(len(test_events), 1)
            self.assertEqual(test_events[0].payload["source_case_run_id"], run.case_run_id)
            self.assertEqual(test_events[0].payload["generated_path"], str(generated_path))

    def test_generate_passed_run_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = RunConfig(workspace_root=root, lora_root=root / ".lora")
            manager = SessionManager(config)
            source_case = _write_case(root / "case.yaml", expected="ok")
            session = manager.create("source-case")
            run = manager.start_case_run(session.session_id, "source-case", run_config=config)
            shutil.copy2(source_case, Path(run.run_dir) / "case.yaml")
            _write_json(Path(run.run_dir) / "verdict.json", {"status": "passed", "failures": [], "errors": []})

            result = TestGenerator(config=config, session_manager=manager).generate(session.session_id, run.case_run_id)

            self.assertEqual(result.status, "skipped")
            self.assertIn("passed", result.reason or "")
            self.assertFalse((Path(session.session_dir) / "generated_tests").exists())


class RegressionRegistrarTests(unittest.TestCase):
    def test_register_creates_manifest_dedupes_and_sorts_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cases").mkdir()
            config = RunConfig(workspace_root=root, lora_root=root / ".lora")
            z_case = _write_case(root / "cases" / "z.yaml", expected="z")
            a_case = _write_case(root / "cases" / "a.yaml", expected="a", case_id="a-case")
            registrar = RegressionRegistrar(config=config)

            first = registrar.register(z_case)
            registrar.register(a_case)
            duplicate = registrar.register(z_case)

            manifest = json.loads((root / ".lora" / "regression.json").read_text(encoding="utf-8"))
            self.assertEqual(first["status"], "registered")
            self.assertEqual(duplicate["status"], "unchanged")
            self.assertEqual([case["path"] for case in manifest["cases"]], ["cases/a.yaml", "cases/z.yaml"])
            self.assertEqual(len(manifest["cases"]), 2)


def _write_case(path: Path, *, expected: str, case_id: str = "source-case") -> Path:
    path.write_text(
        "\n".join(
            [
                f"id: {case_id}",
                "input:",
                "  content: hello",
                "expect:",
                "  answer:",
                "    contains:",
                f"      - {expected}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
