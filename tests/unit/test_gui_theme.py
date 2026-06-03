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
        self.assertEqual(day.colors.bg, "#f5f5f7")
        self.assertEqual(day.colors.accent, "#007aff")
        self.assertEqual(day.state_colors.running, "#64d2ff")
        self.assertEqual(night.colors.bg, "#1c1c1e")
        self.assertEqual(night.colors.accent, "#0a84ff")
        self.assertEqual(night.state_colors.error, "#ff453a")
        self.assertEqual(day.radius.card, 8)
        self.assertEqual(night.radius.pane, 12)

    def test_day_theme_uses_apple_studio_palette(self) -> None:
        day = theme_stylesheet("day")

        self.assertIn("#f5f5f7", day)
        self.assertIn("#007aff", day)
        self.assertIn("#dadde3", day)
        self.assertIn("#SessionRowDeleteButton", day)

    def test_day_theme_uses_mac_like_surfaces_and_compact_controls(self) -> None:
        day = theme_stylesheet("day")

        self.assertIn("#fbfbfd", day)
        self.assertIn("#ffffff", day)
        self.assertIn("#d2d2d7", day)
        self.assertIn("rgba(255,255,255,0.72)", day)
        self.assertIn("border-radius: 8px", day)
        self.assertIn("padding: 8px 12px", day)

    def test_chat_theme_styles_transcript_status_rows(self) -> None:
        day = theme_stylesheet("day")

        self.assertIn("#MessageGroup", day)
        self.assertIn("#ToolStatusRow", day)
        self.assertIn("#ToolStatusIcon", day)
        self.assertIn("#ToolStatusRow[status=\"running\"]", day)
        self.assertIn("color: #6e6e73", day)

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
