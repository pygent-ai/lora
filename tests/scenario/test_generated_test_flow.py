from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class GeneratedTestFlowScenarioTests(unittest.TestCase):
    def test_failed_run_generates_case_and_registers_regression_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_no_api_config(root)
            case_file = root / "failing.yaml"
            case_file.write_text(
                "\n".join(
                    [
                        "id: generated-flow",
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
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}

            run = _lora(root, env, "case", "run", str(case_file))
            self.assertEqual(run["status"], "failed")

            generated = _lora(root, env, "test", "generate", run["session_id"], run["case_run_id"])
            generated_path = Path(generated["generated_path"])
            metadata_path = Path(generated["metadata_path"])
            self.assertEqual(generated["status"], "generated")
            self.assertTrue(generated_path.exists())
            self.assertTrue(metadata_path.exists())

            generated_run = _lora(root, env, "case", "run", str(generated_path))
            self.assertEqual(generated_run["status"], "failed")
            self.assertEqual(generated_run["case_id"], "generated-flow-generated")

            first_register = _lora(root, env, "test", "register", str(generated_path))
            second_register = _lora(root, env, "test", "register", str(generated_path))
            manifest_path = root / ".lora" / "regression.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(first_register["status"], "registered")
            self.assertEqual(second_register["status"], "unchanged")
            self.assertEqual(first_register["total"], 1)
            self.assertEqual(len(manifest["cases"]), 1)
            self.assertEqual(manifest["cases"][0]["path"], generated_path.relative_to(root).as_posix())

            events = _read_jsonl(Path(run["run_dir"]) / "events.jsonl")
            generated_events = [event for event in events if event["type"] == "test.generated"]
            self.assertEqual(len(generated_events), 1)
            self.assertEqual(generated_events[0]["payload"]["source_case_run_id"], run["case_run_id"])
            self.assertEqual(generated_events[0]["payload"]["generated_path"], str(generated_path))


def _lora(root: Path, env: dict[str, str], *args: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-m", "lora", "--workspace-root", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return json.loads(result.stdout)


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


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
