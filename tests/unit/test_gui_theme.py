from __future__ import annotations

import unittest

from gui.theme import _candidate_ui_font_paths, available_themes, theme_stylesheet


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

    def test_resources_styles_are_defined(self) -> None:
        css = theme_stylesheet("day")

        self.assertIn("#ResourcesWindow", css)
        self.assertIn("#ResourceGalleryPane", css)
        self.assertIn("#ResourceCard", css)
        self.assertIn("#ResourceSpecPane", css)

    def test_theme_uses_microsoft_yahei_as_primary_ui_font(self) -> None:
        css = theme_stylesheet("day")

        self.assertIn('font-family: "Microsoft YaHei UI"', css)
        self.assertNotIn('font-family: "STLiti"', css)

    def test_registers_system_microsoft_yahei_font_candidates(self) -> None:
        font_files = [path.name for path in _candidate_ui_font_paths()]

        self.assertIn("msyh.ttc", font_files)
        self.assertIn("msyhbd.ttc", font_files)


if __name__ == "__main__":
    unittest.main()
