from __future__ import annotations

import unittest

from gui.theme import available_themes, theme_stylesheet


class GuiThemeTests(unittest.TestCase):
    def test_day_and_night_themes_share_unified_selectors(self) -> None:
        day = theme_stylesheet("day")
        night = theme_stylesheet("night")

        self.assertIn("#SessionSidebar", day)
        self.assertIn("#SessionSidebar", night)
        self.assertIn("#ThemeDayButton", day)
        self.assertIn("#ThemeNightButton", night)
        self.assertNotEqual(day, night)

    def test_available_themes_are_day_and_night(self) -> None:
        self.assertEqual(available_themes(), ("day", "night"))

    def test_unknown_theme_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            theme_stylesheet("high-contrast")

    def test_session_group_styles_are_defined(self) -> None:
        css = theme_stylesheet("day")

        self.assertIn("#SessionTree", css)
        self.assertIn("#SessionGroupHeader", css)
        self.assertIn("#SessionGroupTitle", css)
        self.assertIn("#SessionGroupCount", css)
        self.assertIn("#SessionGroupDeleteButton", css)


if __name__ == "__main__":
    unittest.main()
