from __future__ import annotations

import os
import unittest
from contextlib import redirect_stdout
from io import StringIO

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt, QEvent
from PySide6.QtGui import QMouseEvent, QPointingDevice
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from gui.devtools import (
    UiHitTestFilter,
    attach_chat_layout_probe,
    build_widget_path,
    chat_bubble_diagnostics,
    devtools_clipboard_enabled,
    devtools_enabled,
    describe_widget_hit,
    install_ui_hit_test_filter,
)
from gui.widgets.chat import ChatPane
from gui.widgets.settings import SettingsDialog
from lora.schema import RunConfig


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

    def test_hit_test_filter_records_path_without_clipboard_by_default(self) -> None:
        widget = QLabel("Clickable")
        widget.setObjectName("ClickableLabel")
        records: list[str] = []
        hit_filter = UiHitTestFilter(on_hit=records.append)
        previous_clipboard = self.app.clipboard().text()

        hit_filter.record_widget_hit(widget)

        self.assertEqual(records, ['ClickableLabel | QLabel text="Clickable"'])
        self.assertEqual(self.app.clipboard().text(), previous_clipboard)

    def test_hit_test_filter_copies_clipboard_when_enabled(self) -> None:
        widget = QLabel("Clickable")
        widget.setObjectName("ClickableLabel")
        previous = os.environ.get("LORA_UI_DEBUG_HITTEST_CLIPBOARD")
        try:
            os.environ["LORA_UI_DEBUG_HITTEST_CLIPBOARD"] = "1"
            self.assertTrue(devtools_clipboard_enabled())
            UiHitTestFilter().record_widget_hit(widget)
            self.assertEqual(self.app.clipboard().text(), 'ClickableLabel | QLabel text="Clickable"')
        finally:
            if previous is None:
                os.environ.pop("LORA_UI_DEBUG_HITTEST_CLIPBOARD", None)
            else:
                os.environ["LORA_UI_DEBUG_HITTEST_CLIPBOARD"] = previous

    def test_hit_test_filter_prints_hit_to_stdout(self) -> None:
        widget = QLabel("Clickable")
        widget.setObjectName("ClickableLabel")
        hit_filter = UiHitTestFilter()
        output = StringIO()

        with redirect_stdout(output):
            hit_filter.record_widget_hit(widget)

        self.assertEqual(output.getvalue(), '[ui-hit] ClickableLabel | QLabel text="Clickable"\n')

    def test_chat_bubble_diagnostics_reports_user_and_assistant_pixel_calculations(self) -> None:
        pane = ChatPane()
        pane.resize(420, 320)
        pane.show()
        self.app.processEvents()
        pane.add_message("user", "hello")
        pane.add_message("assistant", "hi there")
        self.app.processEvents()

        lines = chat_bubble_diagnostics(pane)

        text = "\n".join(lines)
        self.assertIn("[ui-chat-bubble]", text)
        self.assertIn("viewport_max=", text)
        self.assertIn("user_limit_72pct=", text)
        self.assertIn("text_advance=", text)
        self.assertIn("natural=", text)
        self.assertIn("inside_pad=", text)
        self.assertIn("assistant[0]", text)
        self.assertIn("target_82pct=", text)

    def test_attach_chat_layout_probe_prints_diagnostics_on_startup(self) -> None:
        previous = os.environ.get("LORA_UI_DEBUG_HITTEST")
        try:
            os.environ["LORA_UI_DEBUG_HITTEST"] = "1"
            pane = ChatPane()
            pane.resize(420, 320)
            pane.show()
            self.app.processEvents()
            pane.add_message("user", "hello")
            pane.add_message("assistant", "hi")
            output = StringIO()
            with redirect_stdout(output):
                attach_chat_layout_probe(pane)
                self.app.processEvents()
            text = output.getvalue()
            self.assertIn("[ui-chat-layout] reason=startup", text)
            self.assertIn("[ui-chat-bubble]", text)
            self.assertIn("text_advance=", text)
        finally:
            if previous is None:
                os.environ.pop("LORA_UI_DEBUG_HITTEST", None)
            else:
                os.environ["LORA_UI_DEBUG_HITTEST"] = previous

    def test_hit_test_filter_prints_chat_layout_diagnostics_for_chat_hits(self) -> None:
        pane = ChatPane()
        pane.resize(420, 320)
        pane.show()
        self.app.processEvents()
        pane.add_message("user", "hello")
        pane.add_message("assistant", "hi")
        self.app.processEvents()
        hit_filter = UiHitTestFilter()
        output = StringIO()

        with redirect_stdout(output):
            hit_filter.record_widget_hit(pane.scroll_body)

        text = output.getvalue()
        self.assertIn("[ui-hit]", text)
        self.assertIn("ChatScrollBody", text)
        self.assertIn("[ui-chat-layout] root=ChatPane hit=ChatScrollBody", text)
        self.assertIn("scrollbar=", text)
        self.assertIn("scroll_body=", text)
        self.assertIn("last_row=", text)
        self.assertIn("[ui-chat-bubble]", text)
        self.assertIn("text_advance=", text)

    def test_event_filter_records_mouse_button_press_events(self) -> None:
        widget = QLabel("Clickable")
        widget.setObjectName("ClickableLabel")
        widget.resize(120, 32)
        widget.move(0, 0)
        widget.show()
        self.app.processEvents()
        records: list[str] = []
        hit_filter = UiHitTestFilter(on_hit=records.append)
        global_pos = widget.mapToGlobal(widget.rect().center())
        event = QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(widget.rect().center()),
            global_pos,
            global_pos,
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

    def test_settings_dialog_fields_and_buttons_have_stable_hit_test_paths(self) -> None:
        config = RunConfig(
            workspace_root=".",
            lora_root=".",
            agent_alias="default",
            model_name="gpt-test",
        )
        dialog = SettingsDialog(config)

        self.assertEqual(build_widget_path(dialog.workspace), "SettingsDialog > SettingsWorkspaceInput")
        self.assertEqual(build_widget_path(dialog.config_path), "SettingsDialog > SettingsConfigPathInput")
        self.assertEqual(build_widget_path(dialog.agent_alias), "SettingsDialog > SettingsAgentAliasInput")
        self.assertEqual(build_widget_path(dialog.model), "SettingsDialog > SettingsModelInput")
        self.assertEqual(build_widget_path(dialog.max_steps), "SettingsDialog > SettingsMaxStepsInput")
        self.assertEqual(build_widget_path(dialog.apply_button), "SettingsDialog > SettingsButtonBox > SettingsApplyButton")
        self.assertEqual(
            build_widget_path(dialog.cancel_button),
            "SettingsDialog > SettingsButtonBox > SettingsCancelButton",
        )


if __name__ == "__main__":
    unittest.main()
