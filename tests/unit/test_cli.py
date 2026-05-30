from __future__ import annotations

import unittest

from lora.cli import _format_runtime_message_for_chat
from lora.runtime import RuntimeMessage


class CliFormattingTests(unittest.TestCase):
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
