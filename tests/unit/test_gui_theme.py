from __future__ import annotations

import unittest

from gui.theme import PALETTES, ThemePalette, available_themes, theme_stylesheet


class GuiThemeTests(unittest.TestCase):
    def test_day_and_night_themes_share_unified_selectors(self) -> None:
        day = theme_stylesheet("day")
        night = theme_stylesheet("night")

        self.assertIn("#SessionSidebar", day)
        self.assertIn("#SessionSidebar", night)
        self.assertIn("#ThemeDayButton", day)
        self.assertIn("#ThemeNightButton", night)
        self.assertNotEqual(day, night)

    def test_palettes_are_token_driven(self) -> None:
        day = PALETTES["day"]
        night = PALETTES["night"]

        self.assertIsInstance(day, ThemePalette)
        self.assertEqual(day.colors.bg, "#eef3f6")
        self.assertEqual(day.state_colors.running, "#0e7c86")
        self.assertEqual(night.colors.bg, "#111719")
        self.assertEqual(night.state_colors.error, "#ff6b68")
        self.assertEqual(day.radius.card, 8)
        self.assertEqual(night.radius.pane, 12)

    def test_day_theme_uses_command_center_day_palette(self) -> None:
        day = theme_stylesheet("day")

        self.assertIn("#eef3f6", day)
        self.assertIn("#d5dee6", day)
        self.assertIn("#SessionRowDeleteButton", day)

    def test_day_theme_uses_precise_radius_and_compact_controls(self) -> None:
        day = theme_stylesheet("day")

        self.assertIn("#f7fafc", day)
        self.assertIn("#ffffff", day)
        self.assertIn("#aebbc6", day)
        self.assertIn("qlineargradient", day)
        self.assertIn("border-radius: 8px", day)
        self.assertIn("padding: 9px 12px", day)

    def test_chat_theme_styles_transcript_status_rows(self) -> None:
        day = theme_stylesheet("day")

        self.assertIn("#MessageGroup", day)
        self.assertIn("#ToolStatusRow", day)
        self.assertIn("#ToolStatusIcon", day)
        self.assertIn("#ToolStatusRow[status=\"running\"]", day)
        self.assertIn("color: #63717c", day)

    def test_chat_theme_keeps_message_text_compact_and_avatars_visible(self) -> None:
        day = theme_stylesheet("day")

        self.assertIn("Segoe UI Variable Text", day)
        self.assertIn("DengXian", day)
        self.assertIn("font-size: 14px", day)
        self.assertIn("#UserAvatar, #AssistantAvatar", day)
        self.assertIn("#ToolStatusToggle", day)
        self.assertIn("#ChatRunStatus", day)
        self.assertIn("#InspectorConfigRow", day)

    def test_available_themes_are_day_and_night(self) -> None:
        self.assertEqual(available_themes(), ("day", "night"))

    def test_unknown_theme_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            theme_stylesheet("high-contrast")


if __name__ == "__main__":
    unittest.main()
