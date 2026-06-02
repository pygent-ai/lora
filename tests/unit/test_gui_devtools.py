from __future__ import annotations

import os
import unittest
from contextlib import redirect_stdout
from io import StringIO

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt, QEvent
from PySide6.QtGui import QMouseEvent, QPointingDevice
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from gui.devtools import UiHitTestFilter, build_widget_path, devtools_enabled, describe_widget_hit, install_ui_hit_test_filter


class GuiDevtoolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_build_widget_path_uses_named_widget_ancestors(self) -> None:
        root = QWidget()
        root.setObjectName("RootPane")
        layout = QVBoxLayout(root)
        unnamed = QWidget()
        child = QLabel("Hello")
        child.setObjectName("GreetingLabel")
        layout.addWidget(unnamed)
        child.setParent(unnamed)

        self.assertEqual(build_widget_path(child), "RootPane > GreetingLabel")

    def test_build_widget_path_adds_index_for_duplicate_named_siblings(self) -> None:
        root = QWidget()
        root.setObjectName("RootPane")
        layout = QVBoxLayout(root)
        first = QLabel("First")
        first.setObjectName("RepeatedLabel")
        second = QLabel("Second")
        second.setObjectName("RepeatedLabel")
        layout.addWidget(first)
        layout.addWidget(second)

        self.assertEqual(build_widget_path(first), "RootPane > RepeatedLabel[1]")
        self.assertEqual(build_widget_path(second), "RootPane > RepeatedLabel[2]")

    def test_build_widget_path_includes_dev_id_property(self) -> None:
        label = QLabel("Session title")
        label.setObjectName("SessionRowTitle")
        label.setProperty("dev_id", "chat-123")

        self.assertEqual(build_widget_path(label), "SessionRowTitle{chat-123}")

    def test_describe_widget_hit_includes_widget_type_and_text_preview(self) -> None:
        label = QLabel("A long content preview that should be shortened for logs")
        label.setObjectName("AssistantBubble")

        self.assertEqual(
            describe_widget_hit(label),
            'AssistantBubble | QLabel text="A long content preview that should be..."',
        )

    def test_describe_widget_hit_uses_tooltip_when_widget_has_no_text(self) -> None:
        widget = QWidget()
        widget.setObjectName("IconOnlyControl")
        widget.setToolTip("Delete session chat-123")

        self.assertEqual(
            describe_widget_hit(widget),
            'IconOnlyControl | QWidget tooltip="Delete session chat-123"',
        )

    def test_hit_test_filter_records_path_and_copies_clipboard(self) -> None:
        widget = QLabel("Clickable")
        widget.setObjectName("ClickableLabel")
        records: list[str] = []
        hit_filter = UiHitTestFilter(on_hit=records.append)

        hit_filter.record_widget_hit(widget)

        self.assertEqual(records, ['ClickableLabel | QLabel text="Clickable"'])
        self.assertEqual(self.app.clipboard().text(), 'ClickableLabel | QLabel text="Clickable"')

    def test_hit_test_filter_prints_hit_to_stdout(self) -> None:
        widget = QLabel("Clickable")
        widget.setObjectName("ClickableLabel")
        hit_filter = UiHitTestFilter()
        output = StringIO()

        with redirect_stdout(output):
            hit_filter.record_widget_hit(widget)

        self.assertEqual(output.getvalue(), '[ui-hit] ClickableLabel | QLabel text="Clickable"\n')

    def test_event_filter_records_mouse_button_press_events(self) -> None:
        widget = QLabel("Clickable")
        widget.setObjectName("ClickableLabel")
        records: list[str] = []
        hit_filter = UiHitTestFilter(on_hit=records.append)
        event = QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(1, 1),
            QPointF(1, 1),
            QPointF(1, 1),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
            QPointingDevice.primaryPointingDevice(),
        )

        hit_filter.eventFilter(widget, event)

        self.assertEqual(records, ['ClickableLabel | QLabel text="Clickable"'])

    def test_devtools_enabled_reads_truthy_environment_values(self) -> None:
        previous = os.environ.get("LORA_UI_DEBUG_HITTEST")
        try:
            os.environ["LORA_UI_DEBUG_HITTEST"] = "1"
            self.assertTrue(devtools_enabled())
            os.environ["LORA_UI_DEBUG_HITTEST"] = "false"
            self.assertFalse(devtools_enabled())
        finally:
            if previous is None:
                os.environ.pop("LORA_UI_DEBUG_HITTEST", None)
            else:
                os.environ["LORA_UI_DEBUG_HITTEST"] = previous

    def test_install_ui_hit_test_filter_keeps_filter_alive_on_application(self) -> None:
        installed = install_ui_hit_test_filter(self.app, enabled=True)

        self.assertIsInstance(installed, UiHitTestFilter)
        self.assertIs(getattr(self.app, "_lora_ui_hit_test_filter"), installed)


if __name__ == "__main__":
    unittest.main()
