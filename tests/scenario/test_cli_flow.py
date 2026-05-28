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
            self.assertEqual(payload["analysis"]["root_causes"][0]["type"], "ASSERTION_FAILED")
            self.assertTrue((Path(payload["run"]["run_dir"]) / "analysis.json").exists())

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
            self.assertIn(
                "conversation.user_message",
                [item["type"] for item in _read_jsonl(run_dir / "events.jsonl")],
            )

def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
