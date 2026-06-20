from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from gui.workers import InspectorEvent
from gui.widgets.inspector import TraceInspector
from lora.schema import ContextEvent
from lora.tracing import DESIGN_EVENT_TYPES


class GuiInspectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_events_tab_lists_all_design_event_types_as_collapsible_groups(self) -> None:
        inspector = TraceInspector()

        event_types = [inspector.events.topLevelItem(index).text(0) for index in range(inspector.events.topLevelItemCount())]

        self.assertEqual(set(event_types), DESIGN_EVENT_TYPES)
        self.assertEqual(len(event_types), len(DESIGN_EVENT_TYPES))

    def test_trace_event_is_added_under_matching_event_type_with_payload_detail(self) -> None:
        inspector = TraceInspector()
        event = ContextEvent(
            id="evt_1",
            session_id="session_1",
            case_id="chat",
            case_run_id="run_1",
            turn_id="turn-0001",
            type="tool.result",
            timestamp="2026-06-02T08:00:00Z",
            actor="tool",
            payload={"tool_call_id": "call_1", "status": "success", "result": "done"},
        )

        inspector.add_trace_event(event)

        group = inspector.event_type_item("tool.result")
        self.assertIsNotNone(group)
        assert group is not None
        self.assertEqual(group.childCount(), 1)
        child = group.child(0)
        self.assertIn("evt_1", child.text(0))
        self.assertIn('"result": "done"', child.text(1))
        self.assertIn('"tool_call_id": "call_1"', child.text(1))

    def test_tool_trace_tab_uses_collapsed_summary_with_detail_child(self) -> None:
        inspector = TraceInspector()
        event = ContextEvent(
            id="evt_tool",
            session_id="session_1",
            case_id="chat",
            case_run_id="run_1",
            turn_id="turn-0001",
            type="tool.call",
            timestamp="2026-06-02T08:00:00Z",
            actor="assistant",
            payload={"tool_name": "read", "args": {"path": "README.md"}},
        )

        inspector.add_trace_event(event)

        self.assertEqual(inspector.tools.topLevelItemCount(), 1)
        item = inspector.tools.topLevelItem(0)
        self.assertFalse(item.isExpanded())
        self.assertEqual(item.text(0), "read")
        self.assertIn("tool.call", item.text(1))
        self.assertEqual(item.childCount(), 1)
        self.assertIn('"path": "README.md"', item.child(0).text(1))

    def test_tool_trace_tab_merges_call_and_result_by_tool_call_id(self) -> None:
        inspector = TraceInspector()
        call = ContextEvent(
            id="evt_call",
            session_id="session_1",
            case_id="chat",
            case_run_id="run_1",
            turn_id="turn-0001",
            type="tool.call",
            timestamp="2026-06-02T08:00:00Z",
            actor="assistant",
            payload={"tool_call_id": "call_1", "tool_name": "read", "args": {"path": "README.md"}},
        )
        result = ContextEvent(
            id="evt_result",
            session_id="session_1",
            case_id="chat",
            case_run_id="run_1",
            turn_id="turn-0001",
            type="tool.result",
            timestamp="2026-06-02T08:00:03Z",
            actor="tool",
            payload={"tool_call_id": "call_1", "status": "success", "result": "done"},
        )

        inspector.add_trace_event(call)
        inspector.add_trace_event(result)

        self.assertEqual(inspector.tools.topLevelItemCount(), 1)
        item = inspector.tools.topLevelItem(0)
        self.assertEqual(item.text(0), "read")
        self.assertIn("success", item.text(1))
        self.assertEqual([item.child(index).text(0) for index in range(item.childCount())], ["Call", "Result"])
        self.assertIn('"path": "README.md"', item.child(0).text(1))
        self.assertIn('"result": "done"', item.child(1).text(1))

    def test_tool_trace_tab_matches_result_to_call_event_id(self) -> None:
        inspector = TraceInspector()
        call = ContextEvent(
            id="evt_call",
            session_id="session_1",
            case_id="chat",
            case_run_id="run_1",
            turn_id="turn-0001",
            type="tool.call",
            timestamp="2026-06-02T08:00:00Z",
            actor="assistant",
            payload={"tool_name": "read", "args": {"path": "README.md"}},
        )
        result = ContextEvent(
            id="evt_result",
            session_id="session_1",
            case_id="chat",
            case_run_id="run_1",
            turn_id="turn-0001",
            type="tool.result",
            timestamp="2026-06-02T08:00:03Z",
            actor="tool",
            payload={"tool_call_id": "evt_call", "status": "success", "result": "done"},
        )

        inspector.add_trace_event(call)
        inspector.add_trace_event(result)

        self.assertEqual(inspector.tools.topLevelItemCount(), 1)
        item = inspector.tools.topLevelItem(0)
        self.assertEqual(item.text(0), "read")
        self.assertIn("success", item.text(1))
        self.assertEqual([item.child(index).text(0) for index in range(item.childCount())], ["Call", "Result"])

    def test_live_tool_tab_updates_matching_running_call_with_result(self) -> None:
        inspector = TraceInspector()

        inspector.add_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: read",
                detail='{"path": "README.md"}',
                tone="accent",
                metadata={"tool_call_id": "call_read"},
            )
        )
        inspector.add_event(
            InspectorEvent(
                kind="tool",
                title="Tool call: bash",
                detail='{"command": "pytest"}',
                tone="accent",
                metadata={"tool_call_id": "call_test"},
            )
        )
        inspector.add_event(
            InspectorEvent(
                kind="tool",
                title="Tool result: call_test error",
                detail="failed",
                tone="error",
                metadata={"tool_call_id": "call_test"},
            )
        )

        self.assertEqual(inspector.tools.topLevelItemCount(), 2)
        read_item = inspector.tools.topLevelItem(0)
        bash_item = inspector.tools.topLevelItem(1)
        self.assertEqual(read_item.text(0), "read")
        self.assertIn("running", read_item.text(1))
        self.assertEqual(bash_item.text(0), "bash")
        self.assertIn("error", bash_item.text(1))
        self.assertEqual([bash_item.child(index).text(0) for index in range(bash_item.childCount())], ["Call", "Result"])
        self.assertIn("failed", bash_item.child(1).text(1))

    def test_file_trace_tab_uses_collapsed_summary_with_detail_child(self) -> None:
        inspector = TraceInspector()
        event = ContextEvent(
            id="evt_file",
            session_id="session_1",
            case_id="chat",
            case_run_id="run_1",
            turn_id="turn-0001",
            type="file.read",
            timestamp="2026-06-02T08:00:00Z",
            actor="tool",
            payload={"path": "E:/Projects/lora/README.md", "tool_name": "read"},
        )

        inspector.add_trace_event(event)

        self.assertEqual(inspector.files.topLevelItemCount(), 1)
        item = inspector.files.topLevelItem(0)
        self.assertFalse(item.isExpanded())
        self.assertEqual(item.text(0), "README.md")
        self.assertIn("file.read", item.text(1))
        self.assertEqual(item.childCount(), 1)
        self.assertIn('"path": "E:/Projects/lora/README.md"', item.child(0).text(1))

    def test_status_indicator_tracks_running_and_error_states(self) -> None:
        inspector = TraceInspector()

        inspector.set_status("Running")
        indicator = inspector.findChild(QLabel, "InspectorStatusIndicator")
        self.assertIsNotNone(indicator)
        assert indicator is not None
        self.assertEqual(indicator.property("status"), "running")

        inspector.set_status("Error")

        self.assertEqual(indicator.property("status"), "error")

    def test_config_view_uses_property_rows(self) -> None:
        inspector = TraceInspector()

        inspector.set_config({"workspace": "E:/Projects/lora", "model": "gpt-test"})

        rows = [inspector.config.itemWidget(inspector.config.item(index)) for index in range(inspector.config.count())]
        keys = [
            row.findChild(QLabel, "InspectorConfigKey").text()
            for row in rows
            if row is not None and row.findChild(QLabel, "InspectorConfigKey") is not None
        ]
        values = [
            row.findChild(QLabel, "InspectorConfigValue").text()
            for row in rows
            if row is not None and row.findChild(QLabel, "InspectorConfigValue") is not None
        ]

        self.assertEqual(keys, ["workspace", "model"])
        self.assertEqual(values, ["E:/Projects/lora", "gpt-test"])


if __name__ == "__main__":
    unittest.main()
