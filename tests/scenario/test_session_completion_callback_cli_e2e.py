from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread


def test_child_completion_reaches_parent_after_tool_result(tmp_path: Path) -> None:
    requests: list[dict[str, object]] = []
    requests_lock = Lock()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with requests_lock:
                request_index = len(requests)
                requests.append(body)
            if request_index == 0:
                chunks = [
                    {"choices": [{"delta": {"content": "child report"}}]},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ]
            elif request_index == 1:
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
                    {"choices": [{"delta": {"content": "callback observed"}}]},
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
                "          api_key_env: LORA_SESSION_CALLBACK_TEST_KEY",
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
        "LORA_SESSION_CALLBACK_TEST_KEY": "local-test-only",
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

    def invoke(*arguments: str) -> dict[str, object]:
        completed = subprocess.run(
            [*base, *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=90,
        )
        return dict(json.loads(completed.stdout))

    try:
        parent = invoke("create", "--case", "parent", "--mode", "agent")
        parent_session_id = str(parent["session_id"])
        started = invoke(
            "start",
            "--source-session",
            parent_session_id,
            "--message",
            "Research the issue.",
            "--submission-id",
            "callback-e2e",
        )
        operation_id = str(started["operation_id"])
        child_session_id = str(started["target_session_id"])
        completed = invoke("wait", operation_id)
        assert completed["status"] == "passed"
        assert completed["final_answer"] == "child report"

        parent_result = invoke(
            "run",
            parent_session_id,
            "--message",
            "Read seed.txt, then summarize child progress.",
        )
        assert parent_result["final_answer"] == "callback observed"

        with requests_lock:
            assert len(requests) == 3
            messages = requests[2]["messages"]
        assert isinstance(messages, list)
        tool_message = next(
            item
            for item in messages
            if isinstance(item, dict) and item.get("role") == "tool"
        )
        callback = str(tool_message["content"])
        assert '<runtime-context>\n  <agent-message message-id="msg-' in callback
        assert f'source-session-id="{child_session_id}"' in callback
        assert 'source-agent-alias="test"' in callback
        assert '"type": "session.completed"' in callback
        assert f'"operation_id": "{operation_id}"' in callback
        assert '"status": "passed"' in callback
        assert '"final_answer": "child report"' in callback
    finally:
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=5)
