from __future__ import annotations

import unittest

from lora.runtime import RuntimeMessage

from gui.workers import InspectorEvent, runtime_message_to_inspector_event, runtime_message_to_inspector_events


class GuiWorkerTests(unittest.TestCase):
    def test_runtime_message_to_inspector_event_formats_tool_call(self) -> None:
        message = RuntimeMessage(
            role="assistant",
            content="",
            payload={
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "read", "arguments": '{"path": "README.md"}'},
                    }
                ]
            },
        )

        event = runtime_message_to_inspector_event(message)

        self.assertEqual(
            event,
            InspectorEvent(
                kind="tool",
                title="Tool call: read",
                detail='{"path": "README.md"}',
                tone="accent",
                metadata={"tool_call_id": "call_1"},
            ),
        )

    def test_runtime_message_to_inspector_events_keeps_all_tool_calls(self) -> None:
        message = RuntimeMessage(
            role="assistant",
            content="",
            payload={
                "tool_calls": [
                    {"id": "call_1", "function": {"name": "read", "arguments": '{"description": "Read files"}'}},
                    {"id": "call_2", "function": {"name": "bash", "arguments": '{"description": "Run tests"}'}},
                ]
            },
        )

        events = runtime_message_to_inspector_events(message)

        self.assertEqual([event.title for event in events], ["Tool call: read", "Tool call: bash"])
        self.assertEqual([event.metadata["tool_call_id"] for event in events], ["call_1", "call_2"])

    def test_runtime_message_to_inspector_event_formats_tool_result(self) -> None:
        message = RuntimeMessage(
            role="tool",
            content='{"status": "success", "result": "done"}',
            payload={"tool_call_id": "call_1"},
        )

        event = runtime_message_to_inspector_event(message)

        self.assertEqual(event.kind, "tool")
        self.assertEqual(event.title, "Tool result: call_1 success")
        self.assertEqual(event.detail, "done")
        self.assertEqual(event.tone, "ok")
        self.assertEqual(event.metadata["tool_call_id"], "call_1")


if __name__ == "__main__":
    unittest.main()
