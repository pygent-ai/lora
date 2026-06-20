from __future__ import annotations

import re
import unittest

from gui.theme import UI_FONT_FAMILIES, _candidate_ui_font_paths, apply_ui_font, available_themes, theme_stylesheet


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

    def test_layered_history_drawer_styles_are_defined(self) -> None:
        css = theme_stylesheet("day")

        self.assertIn("#HistoryLayer", css)
        self.assertIn("#WorkbenchLayer", css)
        self.assertIn("#SidebarCollapseButton", css)
        self.assertIn('#SessionSidebar[collapsed="true"]', css)

    def test_resources_styles_are_defined(self) -> None:
        css = theme_stylesheet("day")

        self.assertIn("#ResourcesWindow", css)
        self.assertIn("#ResourceGalleryPane", css)
        self.assertIn("#ResourceCard", css)
        self.assertIn("#ResourceSpecPane", css)

    def test_theme_uses_enhanced_chinese_sans_fallback_stack(self) -> None:
        expected_prefix = ("Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC")
        app = _RecordingApplication()

        apply_ui_font(app)

        self.assertIsNotNone(app.font)
        self.assertEqual(UI_FONT_FAMILIES[:4], expected_prefix)
        self.assertEqual(tuple(app.font.families()[:4]), expected_prefix)

    def test_theme_stylesheet_does_not_override_application_font_family(self) -> None:
        css = theme_stylesheet("day")

        self.assertNotIn("font-family:", css)
        self.assertNotIn('font-family: "STLiti"', css)

    def test_registers_system_microsoft_yahei_font_candidates(self) -> None:
        font_files = [path.name for path in _candidate_ui_font_paths()]

        self.assertIn("msyh.ttc", font_files)
        self.assertIn("msyhbd.ttc", font_files)

    def test_theme_uses_point_font_sizes_for_dpi_friendly_text_rendering(self) -> None:
        css = theme_stylesheet("day")

        self.assertIn("font-size: 9.75pt;", css)
        self.assertIsNone(re.search(r"font-size:\s*[^;]+px;", css))

    def test_markdown_special_blocks_use_filled_surface_backgrounds(self) -> None:
        day = theme_stylesheet("day")
        night = theme_stylesheet("night")

        for selector in ("#ChatQuoteBlock", "#ChatCodeBlock"):
            self.assertIn("background: #f5f2ee;", _qss_block(day, selector))
            self.assertIn("background: #252b35;", _qss_block(night, selector))

    def test_markdown_table_body_uses_warm_neutral_fill(self) -> None:
        css = theme_stylesheet("day")
        table_block = _qss_block(css, "#ChatTableBlock")
        table_data_row = _qss_block(css, "#ChatTableDataRow")
        table_cell = _qss_block(css, "#ChatTableCell")

        self.assertIn("background: #f5f2ee;", table_block)
        self.assertIn("background: #f5f2ee;", table_data_row)
        self.assertIn("background: #f5f2ee;", table_cell)
        self.assertNotIn("background: #f8f9fb;", table_block)
        self.assertNotIn("background: transparent;", table_data_row)
        self.assertNotIn("background: transparent;", table_cell)

    def test_markdown_table_header_uses_warm_neutral_fill(self) -> None:
        css = theme_stylesheet("day")
        header_row = _qss_block(css, "#ChatTableHeaderRow")
        header_cell = _qss_block(css, '#ChatTableCell[header="true"]')

        self.assertIn("background: #efede9;", header_row)
        self.assertIn("background: #efede9;", header_cell)
        self.assertNotIn("background: transparent;", header_row)
        self.assertNotIn("background: #e8f2ff;", header_cell)

    def test_markdown_table_cells_use_compact_row_spacing(self) -> None:
        css = theme_stylesheet("day")
        table_cell = _qss_block(css, "#ChatTableCell")

        self.assertIn("line-height: 1.3;", table_cell)
        self.assertIn("padding: 4px 8px;", table_cell)
        self.assertNotIn("padding: 7px 9px;", table_cell)


class _RecordingApplication:
    def __init__(self) -> None:
        self.font = None

    def setFont(self, font) -> None:
        self.font = font


def _qss_block(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\n\}}", css, re.S)
    if match is None:
        raise AssertionError(f"Missing QSS selector {selector}")
    return match.group("body")


if __name__ == "__main__":
    unittest.main()
