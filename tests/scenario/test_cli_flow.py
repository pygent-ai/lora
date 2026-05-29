from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliScenarioTests(unittest.TestCase):
    def test_session_create_show_and_case_run_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_file = root / "case.yaml"
            case_file.write_text(
                "\n".join(
                    [
                        "id: read-file-basic",
                        "title: Read file basic",
                        "type: e2e",
                        "session:",
                        "  mode: new",
                        "input:",
                        "  messages:",
                        "    - role: user",
                        "      content: summarize examples",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}
            create = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lora",
                    "--workspace-root",
                    str(root),
                    "session",
                    "create",
                    "--case",
                    "read-file-basic",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            session_id = json.loads(create.stdout)["session_id"]

            show = subprocess.run(
                [sys.executable, "-m", "lora", "--workspace-root", str(root), "session", "show", session_id],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(json.loads(show.stdout)["session"]["session_id"], session_id)

            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lora",
                    "--workspace-root",
                    str(root),
                    "case",
                    "run",
                    str(case_file),
                    "--session",
                    session_id,
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            run_payload = json.loads(run.stdout)
            self.assertEqual(run_payload["status"], "passed")
            self.assertIn("Lora agent is wired into chat", run_payload["final_answer"])
            self.assertTrue((Path(run_payload["run_dir"]) / "case.yaml").exists())
            self.assertTrue((Path(run_payload["run_dir"]) / "run_config.json").exists())
            self.assertTrue((Path(run_payload["run_dir"]) / "events.jsonl").exists())
            self.assertTrue((Path(run_payload["run_dir"]) / "messages.jsonl").exists())
            self.assertTrue((Path(run_payload["run_dir"]) / "result.json").exists())
            self.assertTrue((Path(run_payload["run_dir"]) / "metrics.json").exists())
            self.assertTrue((Path(run_payload["run_dir"]) / "verdict.json").exists())

            replay = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lora",
                    "--workspace-root",
                    str(root),
                    "case",
                    "replay",
                    session_id,
                    run_payload["case_run_id"],
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            replay_payload = json.loads(replay.stdout)
            self.assertIn("case.started", [event["type"] for event in replay_payload["events"]])

    def test_optimize_analyzes_failed_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_file = root / "case.yaml"
            case_file.write_text(
                "\n".join(
                    [
                        "id: failing-case",
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
            run = subprocess.run(
                [sys.executable, "-m", "lora", "--workspace-root", str(root), "optimize", str(case_file)],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(run.stdout)

            self.assertEqual(payload["run"]["status"], "failed")
            self.assertEqual(payload["analysis"]["root_causes"][0]["type"], "ANSWER_ASSERTION_FAILED")
            self.assertTrue((Path(payload["run"]["run_dir"]) / "analysis.json").exists())

    def test_regression_run_executes_manifest_cases_and_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases_dir = root / "cases"
            cases_dir.mkdir()
            (root / ".lora").mkdir()
            pass_case = cases_dir / "pass.yaml"
            pass_case.write_text(
                "\n".join(
                    [
                        "id: regression-pass",
                        "input:",
                        "  content: hello",
                        "expect:",
                        "  answer:",
                        "    contains:",
                        "      - Lora agent is wired into chat",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            fail_case = cases_dir / "fail.yaml"
            fail_case.write_text(
                "\n".join(
                    [
                        "id: regression-fail",
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
            (root / ".lora" / "regression.json").write_text(
                json.dumps(
                    {
                        "version": "1.0",
                        "cases": [
                            {"path": "cases/pass.yaml", "session_mode": "new"},
                            {"path": "cases/fail.yaml", "session_mode": "new"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}

            run = subprocess.run(
                [sys.executable, "-m", "lora", "--workspace-root", str(root), "regression", "run"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(run.stdout)
            result_path = Path(payload["result_path"])
            case_runs_path = Path(payload["case_runs_path"])

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["total"], 2)
            self.assertEqual(payload["passed"], 1)
            self.assertEqual(payload["failed"], 1)
            self.assertTrue(result_path.exists())
            self.assertTrue(case_runs_path.exists())
            self.assertTrue((Path(payload["run_dir"]) / "regression_config.json").exists())
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8"))["status"], "failed")
            case_runs = _read_jsonl(case_runs_path)
            self.assertEqual([item["status"] for item in case_runs], ["passed", "failed"])
            self.assertIn("session_id", case_runs[0])
            self.assertIn("case_run_id", case_runs[0])
            self.assertIn("metrics", case_runs[0])
            self.assertIn("verdict", case_runs[0])

    def test_case_run_supports_fork_session_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}
            create = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lora",
                    "--workspace-root",
                    str(root),
                    "session",
                    "create",
                    "--case",
                    "source-case",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            source_session_id = json.loads(create.stdout)["session_id"]
            case_file = root / "fork-case.yaml"
            case_file.write_text(
                "\n".join(
                    [
                        "id: fork-case",
                        "session:",
                        "  mode: fork",
                        f"  source_session_id: {source_session_id}",
                        "input:",
                        "  content: hello from fork",
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
                env=env,
            )
            run_payload = json.loads(run.stdout)
            show = subprocess.run(
                [sys.executable, "-m", "lora", "--workspace-root", str(root), "session", "show", run_payload["session_id"]],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            show_payload = json.loads(show.stdout)

            self.assertNotEqual(run_payload["session_id"], source_session_id)
            self.assertEqual(show_payload["session"]["metadata"]["forked_from"], source_session_id)

    def test_case_run_supports_resume_and_shared_session_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}
            create = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lora",
                    "--workspace-root",
                    str(root),
                    "session",
                    "create",
                    "--case",
                    "shared-case",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            session_id = json.loads(create.stdout)["session_id"]

            for mode in ("resume", "shared"):
                case_file = root / f"{mode}-case.yaml"
                case_file.write_text(
                    "\n".join(
                        [
                            f"id: {mode}-case",
                            "session:",
                            f"  mode: {mode}",
                            f"  session_id: {session_id}",
                            "input:",
                            f"  content: hello from {mode}",
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
                    env=env,
                )
                self.assertEqual(json.loads(run.stdout)["session_id"], session_id)

    def test_case_run_executes_workspace_setup_actions_before_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stale.txt").write_text("old", encoding="utf-8")
            case_file = root / "setup-case.yaml"
            case_file.write_text(
                "\n".join(
                    [
                        "id: setup-case",
                        "input:",
                        "  content: hello",
                        "workspace:",
                        "  setup:",
                        "    - type: write_file",
                        "      path: sandbox/input.txt",
                        "      content: from setup",
                        "    - type: mkdir",
                        "      path: sandbox/out",
                        "    - type: delete_path",
                        "      path: stale.txt",
                        "expect:",
                        "  answer:",
                        "    contains:",
                        "      - Lora agent is wired into chat",
                        "  files:",
                        "    unchanged:",
                        "      - sandbox/input.txt",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}

            run = subprocess.run(
                [sys.executable, "-m", "lora", "--workspace-root", str(root), "case", "run", str(case_file)],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(run.stdout)
            run_dir = Path(payload["run_dir"])
            actions = _read_jsonl(run_dir / "workspace" / "setup_actions.jsonl")

            self.assertEqual(payload["status"], "passed")
            self.assertEqual((root / "sandbox" / "input.txt").read_text(encoding="utf-8"), "from setup")
            self.assertTrue((root / "sandbox" / "out").is_dir())
            self.assertFalse((root / "stale.txt").exists())
            self.assertEqual([item["type"] for item in actions], ["write_file", "mkdir", "delete_path"])
            self.assertTrue((run_dir / "workspace" / "before_hashes.json").exists())

    def test_case_run_rejects_workspace_setup_path_escape_before_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("safe", encoding="utf-8")
            case_file = root / "unsafe-case.yaml"
            case_file.write_text(
                "\n".join(
                    [
                        "id: unsafe-case",
                        "input:",
                        "  content: hello",
                        "workspace:",
                        "  setup:",
                        "    - type: write_file",
                        "      path: ../outside.txt",
                        "      content: mutated",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}

            run = subprocess.run(
                [sys.executable, "-m", "lora", "--workspace-root", str(root), "case", "run", str(case_file)],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(run.returncode, 2)
            self.assertIn("workspace.setup[].path", run.stderr)
            self.assertEqual(outside.read_text(encoding="utf-8"), "safe")
            run_dirs = list((root / ".lora" / "sessions").glob("*/cases/unsafe-case/runs/*"))
            self.assertEqual(len(run_dirs), 1)
            self.assertFalse((run_dirs[0] / "result.json").exists())
            self.assertFalse((run_dirs[0] / "model_requests.jsonl").exists())

    def test_chat_message_creates_session_and_records_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}
            chat = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lora",
                    "--workspace-root",
                    str(root),
                    "chat",
                    "--message",
                    "hello agent",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(chat.stdout)
            run_dir = Path(payload["run_dir"])

            self.assertEqual(payload["status"], "passed")
            self.assertIn("Lora agent is wired into chat", payload["final_answer"])
            self.assertTrue(payload["session_id"].startswith("chat-chat-"))
            self.assertTrue((run_dir / "events.jsonl").exists())
            self.assertTrue((run_dir / "messages.jsonl").exists())
            events = _read_jsonl(run_dir / "events.jsonl")
            self.assertIn("conversation.user_message", [item["type"] for item in events])

            prompt_event = next(item for item in events if item["type"] == "prompt.rendered")
            rendered_prompt = Path(prompt_event["payload"]["prompt_text_path"]).read_text(encoding="utf-8")
            static_prompt = (
                root / ".lora" / "sessions" / payload["session_id"] / "context" / "prompts" / "static_prompt.txt"
            ).read_text(encoding="utf-8")

            self.assertIn("# Identity", static_prompt)
            self.assertIn("# Coding Work", static_prompt)
            self.assertNotIn("# Runtime Context", static_prompt)
            self.assertIn("__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__", rendered_prompt)
            self.assertIn("# Runtime Context", rendered_prompt)
            self.assertIn("# Available Tools", rendered_prompt)
            self.assertIn("# Recent Context", rendered_prompt)
            self.assertIn("system.identity", prompt_event["payload"]["static_module_ids"])
            self.assertIn("system.coding_rules", prompt_event["payload"]["static_module_ids"])
            self.assertIn("runtime.env_info", prompt_event["payload"]["dynamic_module_ids"])
            self.assertIn("tool.available", prompt_event["payload"]["dynamic_module_ids"])
            self.assertEqual(prompt_event["payload"]["injection_decision"]["reason"], "before_model_request")

    def test_chat_agent_alias_records_safe_profile_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "\n".join(
                    [
                        "agents:",
                        "  - alias: dev",
                        "    model_request:",
                        "      model_name: dev-model",
                        "      api_key_env: DEV_PROFILE_KEY",
                        "      base_url: https://profile.example/v1",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}
            env.pop("DEEPSEEK_API_KEY", None)
            env.pop("DEV_PROFILE_KEY", None)
            chat = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lora",
                    "--workspace-root",
                    str(root),
                    "--agent",
                    "dev",
                    "--model",
                    "cli-model",
                    "chat",
                    "--message",
                    "hello agent",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(chat.stdout)
            run_dir = Path(payload["run_dir"])
            run_config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
            request = next(item for item in _read_jsonl(run_dir / "events.jsonl") if item["type"] == "model.request")

            self.assertEqual(run_config["agent_alias"], "dev")
            self.assertEqual(run_config["model_name"], "cli-model")
            self.assertEqual(run_config["api_key_source"], "missing")
            self.assertIn("agent alias 'dev'", payload["final_answer"])
            self.assertEqual(request["payload"]["agent_alias"], "dev")
            self.assertEqual(request["payload"]["model_name"], "cli-model")

    def test_missing_agent_alias_returns_exit_code_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lora.yaml").write_text(
                "agents:\n  - alias: dev\n    model_request:\n      model_name: dev-model\n",
                encoding="utf-8",
            )
            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lora",
                    "--workspace-root",
                    str(root),
                    "--agent",
                    "missing",
                    "chat",
                    "--message",
                    "hello",
                ],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(run.returncode, 2)
            self.assertIn("Agent alias 'missing' is not configured", run.stderr)

def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
