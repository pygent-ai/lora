from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from lora.repair import RepairWorkflow, _gate_status
from lora.schema import RunConfig
from lora.sessions import SessionManager
from lora.tracing import EventStore


class RepairWorkflowTests(unittest.TestCase):
    def test_plan_writes_artifact_for_failed_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow, run = _failed_run(tmp)
            EventStore(run).append(
                "file.edit",
                actor="tool",
                payload={"path": str(Path(tmp) / "src" / "broken.py")},
                turn_id="turn-0001",
            )

            plan = workflow.plan(run.session_id, run.case_run_id)

            self.assertEqual(plan["status"], "planned")
            self.assertEqual(plan["session_id"], run.session_id)
            self.assertEqual(plan["case_run_id"], run.case_run_id)
            self.assertEqual(plan["failures"][0]["type"], "answer.contains")
            self.assertIn(str(Path(tmp) / "src" / "broken.py"), plan["suspected_files"])
            self.assertTrue(Path(plan["plan_path"]).exists())
            events = [event.type for event in EventStore(run).list_by_run()]
            self.assertIn("repair.started", events)

    def test_plan_skips_passed_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
            manager = SessionManager(config)
            session = manager.create("passing-case")
            run = manager.start_case_run(session.session_id, "passing-case", run_config=config)
            (Path(run.run_dir) / "verdict.json").write_text(
                json.dumps({"status": "passed", "failures": [], "errors": []}),
                encoding="utf-8",
            )
            manager.finish_case_run(run, "passed")

            plan = RepairWorkflow(config=config, session_manager=manager).plan(run.session_id, run.case_run_id)

            self.assertEqual(plan["status"], "skipped")
            self.assertIn("already passed", plan["reason"])

    def test_apply_captures_patch_and_gate_runs_configured_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".lora").mkdir()
            (root / ".lora" / "repair.json").write_text(
                json.dumps({"commands": [[sys.executable, "-c", "print('repair gate ok')"]]}),
                encoding="utf-8",
            )
            workflow, run = _failed_run(tmp)
            plan = workflow.plan(run.session_id, run.case_run_id)

            target = root / "changed.txt"
            target.write_text("manual repair\n", encoding="utf-8")
            attempt = workflow.apply(plan["plan_path"])
            gate = workflow.gate(attempt["attempt_id"])

            self.assertTrue(Path(attempt["patch_path"]).exists())
            self.assertEqual(gate["status"], "passed")
            self.assertIn("repair gate ok", gate["commands"][0]["stdout"])
            self.assertTrue((Path(attempt["patch_path"]).parent / "gate_result.json").exists())
            events = [event.type for event in EventStore(run).list_by_run()]
            self.assertIn("repair.patch_created", events)
            self.assertIn("repair.finished", events)


class RepairGateStatusTests(unittest.TestCase):
    def test_gate_status_failed_takes_precedence_over_passed(self) -> None:
        self.assertEqual(_gate_status([{"status": "passed"}, {"status": "failed"}]), "failed")

    def test_gate_status_error_takes_precedence(self) -> None:
        self.assertEqual(_gate_status([{"status": "failed"}, {"status": "error"}]), "error")

    def test_gate_status_empty_is_skipped(self) -> None:
        self.assertEqual(_gate_status([]), "skipped")


def _failed_run(tmp: str):
    config = RunConfig(workspace_root=tmp, lora_root=Path(tmp) / ".lora")
    manager = SessionManager(config)
    session = manager.create("failing-case")
    run = manager.start_case_run(session.session_id, "failing-case", run_config=config)
    (Path(run.run_dir) / "verdict.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "failures": [{"type": "answer.contains", "message": "missing impossible-token"}],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    (Path(run.run_dir) / "analysis.json").write_text(
        json.dumps(
            {
                "root_causes": [
                    {
                        "type": "ASSERTION_FAILED",
                        "summary": "Run output did not satisfy deterministic case assertions.",
                        "suspected_modules": ["evaluation"],
                        "recommended_tests": [{"kind": "unit", "description": "Cover failed assertion."}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manager.finish_case_run(run, "failed")
    return RepairWorkflow(config=config, session_manager=manager), run


if __name__ == "__main__":
    unittest.main()
