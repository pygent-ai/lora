from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RepairFlowScenarioTests(unittest.TestCase):
    def test_repair_plan_apply_and_gate_cli_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}
            _write_no_api_config(root)
            (root / ".lora").mkdir()
            (root / ".lora" / "repair.json").write_text(
                json.dumps({"commands": [[sys.executable, "-c", "print('gate passed')"]]}),
                encoding="utf-8",
            )
            case_file = root / "failing-case.yaml"
            case_file.write_text(
                "\n".join(
                    [
                        "id: repair-failing-case",
                        "input:",
                        "  content: hello",
                        "expect:",
                        "  answer:",
                        "    contains:",
                        "      - impossible-token",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            run = subprocess.run(
                [sys.executable, "-m", "lora", "--workspace-root", str(root), "case", "run", str(case_file)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
            )
            run_payload = json.loads(run.stdout)
            self.assertEqual(run_payload["status"], "failed")

            plan = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lora",
                    "--workspace-root",
                    str(root),
                    "repair",
                    "plan",
                    run_payload["session_id"],
                    run_payload["case_run_id"],
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
            )
            plan_payload = json.loads(plan.stdout)
            plan_path = Path(plan_payload["plan_path"])
            self.assertEqual(plan_payload["status"], "planned")
            self.assertTrue(plan_path.exists())

            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
            target = root / "repair-target.txt"
            target.write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "add", "repair-target.txt"], cwd=root, check=True, capture_output=True, text=True)
            target.write_text("after\n", encoding="utf-8")

            apply = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lora",
                    "--workspace-root",
                    str(root),
                    "repair",
                    "apply",
                    str(plan_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
            )
            apply_payload = json.loads(apply.stdout)
            patch_path = Path(apply_payload["patch_path"])
            self.assertTrue(patch_path.exists())
            self.assertIn("repair-target.txt", patch_path.read_text(encoding="utf-8"))

            gate = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lora",
                    "--workspace-root",
                    str(root),
                    "repair",
                    "gate",
                    apply_payload["attempt_id"],
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
            )
            gate_payload = json.loads(gate.stdout)
            self.assertEqual(gate_payload["status"], "passed")
            self.assertTrue((patch_path.parent / "gate_result.json").exists())

            events = _read_jsonl(Path(run_payload["run_dir"]) / "events.jsonl")
            self.assertIn("repair.started", [event["type"] for event in events])
            self.assertIn("repair.patch_created", [event["type"] for event in events])
            self.assertIn("repair.finished", [event["type"] for event in events])


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_no_api_config(root: Path) -> None:
    (root / "lora.yaml").write_text(
        "\n".join(
            [
                "agent:",
                "  default_alias: test",
                "agents:",
                "  - alias: test",
                "    model_request:",
                "      model_name: test-model",
                "      api_key_env: LORA_SCENARIO_NO_API_KEY",
                "",
            ]
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
