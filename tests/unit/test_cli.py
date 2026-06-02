from __future__ import annotations

import argparse
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from lora.cli import _chat_message_payload, _format_runtime_message_for_chat, main
from lora.schema import CaseRunRef
from lora.runtime import RuntimeMessage


class CliFormattingTests(unittest.TestCase):
    def test_main_prints_json_without_escaping_non_ascii(self) -> None:
        parser = argparse.ArgumentParser()
        parser.set_defaults(handler=lambda args: {"final_answer": "开发指南"})
        stdout = io.StringIO()

        with patch("lora.cli.build_parser", return_value=parser), patch("sys.stdout", stdout):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn('"final_answer": "开发指南"', stdout.getvalue())
        self.assertNotIn("\\u5f00\\u53d1\\u6307\\u5357", stdout.getvalue())

    def test_chat_message_payload_uses_final_answer_as_only_response_content(self) -> None:
        run_ref = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir="runs/r1")

        self.assertEqual(
            _chat_message_payload(
                run_ref,
                {
                    "status": "passed",
                    "final_answer": "agent answer",
                    "error": None,
                    "message_count": 2,
                    "turn_id": "turn-0001",
                },
            ),
            {
                "final_answer": "agent answer",
                "session_id": "s1",
                "case_run_id": "r1",
                "run_dir": str(Path("runs/r1").resolve()),
            },
        )

    def test_chat_message_payload_includes_error_when_agent_errors(self) -> None:
        run_ref = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir="runs/r1")

        self.assertEqual(
            _chat_message_payload(
                run_ref,
                {
                    "status": "error",
                    "final_answer": "partial answer",
                    "error": "boom",
                    "message_count": 1,
                    "turn_id": "turn-0001",
                },
            ),
            {
                "final_answer": "partial answer",
                "session_id": "s1",
                "case_run_id": "r1",
                "run_dir": str(Path("runs/r1").resolve()),
                "error": "boom",
            },
        )

    def test_format_tool_call_message(self) -> None:
        message = RuntimeMessage(
            role="assistant",
            content="",
            type="conversation.assistant_message",
            payload={
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path": "README.md"}'},
                    }
                ],
            },
        )

        self.assertEqual(_format_runtime_message_for_chat(message), ["[tool call] read {\"path\": \"README.md\"}"])

    def test_format_tool_result_limits_preview_to_five_lines(self) -> None:
        message = RuntimeMessage(
            role="tool",
            content='{"status": "success", "result": "one\\ntwo\\nthree\\nfour\\nfive\\nsix", "error": null}',
            type="conversation.tool_message",
            payload={"role": "tool", "content": '{"status": "success"}', "tool_call_id": "call_1"},
        )

        self.assertEqual(
            _format_runtime_message_for_chat(message),
            ["[tool result] call_1 success", "one", "two", "three", "four", "..."],
        )


if __name__ == "__main__":
    unittest.main()
