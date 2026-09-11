from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread


def test_cli_message_reaches_running_agent_after_tool_result(tmp_path: Path) -> None:
    requests: list[dict[str, object]] = []
    requests_lock = Lock()
    first_request_started = Event()
    release_first_response = Event()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with requests_lock:
                request_index = len(requests)
                requests.append(body)
            if request_index == 0:
                first_request_started.set()
                if not release_first_response.wait(timeout=20):
                    self.send_error(504)
                    return
                chunks = [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call-read-seed",
                                            "type": "function",
                                            "function": {
                                                "name": "read",
                                                "arguments": '{"file_path":"seed.txt"}',
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                ]
            else:
                chunks = [
                    {"choices": [{"delta": {"content": "agent message observed"}}]},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ]
            chunks.append(
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                }
            )
            encoded = (
                "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "seed.txt").write_text("seed", encoding="utf-8")
    user_home = tmp_path / "home"
    user_root = user_home / ".lora"
    user_root.mkdir(parents=True)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    provider_thread = Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()
    (user_root / "config.yaml").write_text(
        "\n".join(
            [
                "agent:",
                "  default_alias: test",
                "agents:",
                "  - alias: test",
                "    model_request:",
                "      routes:",
                "        - id: primary",
                "          provider: openai",
                "          model_name: local-test",
                f"          base_url: http://127.0.0.1:{provider.server_port}/v1",
                "          api_key_env: LORA_SESSION_MESSAGE_TEST_KEY",
                "runtime:",
                "  approvals:",
                "    enabled: false",
                "eternal_conversation:",
                "  enabled: false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(Path.cwd() / "src"),
        "USERPROFILE": str(user_home),
        "HOME": str(user_home),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "LORA_SESSION_MESSAGE_TEST_KEY": "local-test-only",
    }
    base = [
        sys.executable,
        "-m",
        "lora",
        "--workspace-root",
        str(workspace),
        "--agent",
        "test",
        "--max-steps",
        "4",
        "session",
    ]

    def create(case_id: str) -> str:
        completed = subprocess.run(
            [*base, "create", "--case", case_id, "--mode", "agent"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        return str(json.loads(completed.stdout)["session_id"])

    process: subprocess.Popen[str] | None = None
    try:
        source_session_id = create("source-agent")
        target_session_id = create("target-agent")
        process = subprocess.Popen(
            [
                *base,
                "run",
                target_session_id,
                "--message",
                "Read seed.txt before answering.",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        assert first_request_started.wait(timeout=20)
        sent = subprocess.run(
            [
                *base,
                "send",
                target_session_id,
                "--source-session",
                source_session_id,
                "--message",
                "Inspect <artifact> & report back.",
                "--submission-id",
                "cli-agent-message-e2e",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        message = json.loads(sent.stdout)
        assert message["source_session_id"] == source_session_id
        assert message["source_agent_alias"] == "test"
        release_first_response.set()
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        result = json.loads(stdout)
        assert result["final_answer"] == "agent message observed"

        delivered = subprocess.run(
            [*base, "wait", message["message_id"]],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        delivery = json.loads(delivered.stdout)
        assert delivery["status"] == "delivered"
        assert delivery["delivered_execution_id"] == result["execution_id"]
        with requests_lock:
            assert len(requests) == 2
            messages = requests[1]["messages"]
        assert isinstance(messages, list)
        tool_message = next(
            item
            for item in messages
            if isinstance(item, dict) and item.get("role") == "tool"
        )
        followup = str(tool_message["content"])
        assert '<runtime-context>\n  <agent-message message-id="msg-' in followup
        assert f'source-session-id="{source_session_id}"' in followup
        assert 'source-agent-alias="test"' in followup
        assert "Inspect &lt;artifact&gt; &amp; report back." in followup
    finally:
        release_first_response.set()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=5)
