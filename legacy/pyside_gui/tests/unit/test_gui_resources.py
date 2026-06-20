from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton

from gui.resource_catalog import build_resource_catalog, resource_spec_as_markdown
from gui.resources_app import run_resources_app
from gui.resources_window import ResourcesWindow
from gui.theme import UI_FONT_FAMILIES


class ResourceCatalogTests(unittest.TestCase):
    def test_catalog_includes_theme_fonts_components_styles_colors_and_icons(self) -> None:
        catalog = build_resource_catalog()
        categories = {item.category for item in catalog}
        names = {item.name for item in catalog}

        self.assertGreaterEqual(
            {"Fonts", "Components", "Styles", "Colors", "Icons", "Standard Icons", "Palette Roles", "Layouts", "Qt Modules"},
            categories,
        )
        self.assertIn("Microsoft YaHei UI", names)
        self.assertIn("day.accent", names)
        self.assertIn("night.surface", names)
        self.assertIn("QPushButton", names)
        self.assertIn("QCheckBox", names)
        self.assertIn("QVBoxLayout", names)
        self.assertIn("settings.svg", names)
        self.assertIn("SP_DialogSaveButton", names)
        self.assertIn("Palette.Window", names)
        self.assertIn("QtSvg", names)

    def test_catalog_discovers_system_font_files(self) -> None:
        catalog = build_resource_catalog()
        arial_items = [item for item in catalog if item.name == "Arial" and item.category == "Fonts"]

        self.assertTrue(arial_items)
        self.assertIn("C:\\Windows\\Fonts", arial_items[0].source)
        self.assertIn(("registered", "False"), arial_items[0].properties)

    def test_catalog_does_not_register_system_font_files_when_qapplication_exists(self) -> None:
        QApplication.instance() or QApplication([])

        catalog = build_resource_catalog()
        arial_items = [item for item in catalog if item.name == "Arial" and item.category == "Fonts"]

        self.assertTrue(arial_items)
        self.assertIn(("registered", "False"), arial_items[0].properties)

    def test_catalog_discovers_external_font_directories(self) -> None:
        source_font = next(Path("C:/Windows/Fonts").glob("arial.ttf"), None)
        if source_font is None:
            self.skipTest("Arial font is not available on this Windows image")
        with tempfile.TemporaryDirectory() as tmp:
            external_font = Path(tmp) / "external-demo.ttf"
            shutil.copyfile(source_font, external_font)

            catalog = build_resource_catalog(extra_font_dirs=(Path(tmp),))

        external_items = [item for item in catalog if item.source == str(external_font)]
        self.assertTrue(external_items)
        self.assertEqual(external_items[0].category, "Fonts")
        self.assertIn(("origin", "external"), external_items[0].properties)

    def test_resource_spec_markdown_contains_copyable_style_contract(self) -> None:
        catalog = build_resource_catalog()
        item = next(entry for entry in catalog if entry.name == "day.accent")

        spec = resource_spec_as_markdown(item)

        self.assertIn("# day.accent", spec)
        self.assertIn("Category: Colors", spec)
        self.assertIn("Source: gui.theme.PALETTES['day'].colors.accent", spec)
        self.assertIn("Value: #147efb", spec)


class ResourcesWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_selecting_resource_updates_detail_spec(self) -> None:
        window = ResourcesWindow()
        target = next(item for item in window.catalog_items() if item.name == "QPushButton")

        window.select_resource(target.resource_id)

        self.assertEqual(window.selected_resource_id(), target.resource_id)
        self.assertIn("# QPushButton", window.spec_text())
        self.assertIn("Selector: QPushButton", window.spec_text())

    def test_search_filters_preview_items(self) -> None:
        window = ResourcesWindow()

        window.set_search_text("QPushButton")

        visible_names = window.visible_resource_names()
        self.assertIn("QPushButton", visible_names)
        self.assertNotIn("settings.svg", visible_names)

    def test_copy_selected_spec_writes_to_clipboard(self) -> None:
        window = ResourcesWindow()
        target = next(item for item in window.catalog_items() if item.name == "QPushButton")
        window.select_resource(target.resource_id)

        window.copy_selected_spec()

        self.assertEqual(QApplication.clipboard().text(), window.spec_text())

    def test_resources_app_smoke_mode_starts_without_event_loop(self) -> None:
        self.assertEqual(run_resources_app(smoke=True), 0)

    def test_resources_app_applies_shared_ui_font_stack(self) -> None:
        QApplication.instance().setFont(QFont())

        run_resources_app(smoke=True)

        self.assertEqual(QApplication.instance().font().families()[:4], list(UI_FONT_FAMILIES[:4]))

    def test_color_cards_render_visible_swatch_preview(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("day.accent")

        swatch = window.findChild(QLabel, "ResourceColorSwatch")

        self.assertIsNotNone(swatch)
        self.assertEqual(swatch.property("preview_value"), "#147efb")
        self.assertIn("background: #147efb", swatch.styleSheet())

    def test_font_cards_render_sample_with_matching_family(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("Consolas")

        sample = window.findChild(QLabel, "ResourceFontSample")
        headline = window.findChild(QLabel, "ResourceFontHeadline")
        ruler = window.findChild(QLabel, "ResourceFontRuler")
        resolved = window.findChild(QLabel, "ResourceFontResolved")

        self.assertIsNotNone(sample)
        self.assertIsNotNone(headline)
        self.assertIsNotNone(ruler)
        self.assertIsNotNone(resolved)
        self.assertIn("Ag", headline.text())
        self.assertIn("Aa", sample.text())
        self.assertIn("111", ruler.text())
        self.assertIn("Resolved:", resolved.text())
        self.assertIn("Consolas", sample.font().families())
        self.assertIn('font-family: "Consolas"', headline.styleSheet())
        self.assertIn('font-family: "Consolas"', sample.styleSheet())
        self.assertIn('font-family: "Consolas"', ruler.styleSheet())

    def test_system_font_preview_registers_selected_font_file_before_rendering(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("Arial")

        sample = window.findChild(QLabel, "ResourceFontSample")
        resolved = window.findChild(QLabel, "ResourceFontResolved")

        self.assertIsNotNone(sample)
        self.assertIsNotNone(resolved)
        self.assertIn('font-family: "Arial"', sample.styleSheet())
        self.assertIn("Resolved: Arial", resolved.text())

    def test_component_cards_render_real_widget_preview(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("QPushButton")

        preview = window.findChild(QPushButton, "ResourceComponentPreviewButton")

        self.assertIsNotNone(preview)
        self.assertEqual(preview.text(), "Button")

    def test_added_pyside_standard_icon_cards_render_icon_preview(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("SP_DialogSaveButton")

        preview = window.findChild(QLabel, "ResourceStandardIconPreview")

        self.assertIsNotNone(preview)
        self.assertFalse(preview.pixmap().isNull())

    def test_added_palette_role_cards_render_palette_swatch(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("Palette.Window")

        swatch = window.findChild(QLabel, "ResourcePaletteSwatch")

        self.assertIsNotNone(swatch)
        self.assertEqual(swatch.property("palette_role"), "Window")
        self.assertIn("background:", swatch.styleSheet())

    def test_added_control_cards_render_specific_control_preview(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("QCheckBox")

        preview = window.findChild(QCheckBox, "ResourceComponentPreviewCheckBox")

        self.assertIsNotNone(preview)
        self.assertEqual(preview.text(), "Check")

    def test_added_layout_cards_render_layout_diagram(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("QVBoxLayout")

        preview = window.findChild(QLabel, "ResourceLayoutPreview")

        self.assertIsNotNone(preview)
        self.assertEqual(preview.property("layout_name"), "QVBoxLayout")

    def test_typography_style_cards_render_font_size_preview(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("day.typography.title_size")

        preview = window.findChild(QLabel, "ResourceTypographyPreview")

        self.assertIsNotNone(preview)
        self.assertEqual(preview.property("style_group"), "typography")
        self.assertEqual(preview.font().pixelSize(), 24)

    def test_spacing_style_cards_render_measure_preview(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("day.spacing.gutter")

        preview = window.findChild(QLabel, "ResourceSpacingPreview")

        self.assertIsNotNone(preview)
        self.assertEqual(preview.property("style_group"), "spacing")
        self.assertEqual(preview.property("preview_value"), 14)
        self.assertIn("min-width: 14px", preview.styleSheet())

    def test_radius_style_cards_render_rounded_preview(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("day.radius.pane")

        preview = window.findChild(QLabel, "ResourceRadiusPreview")

        self.assertIsNotNone(preview)
        self.assertEqual(preview.property("style_group"), "radius")
        self.assertIn("border-radius: 16px", preview.styleSheet())

    def test_named_style_cards_render_specific_effect_preview(self) -> None:
        window = ResourcesWindow()
        window.set_search_text("Accent action")

        preview = window.findChild(QPushButton, "ResourceAccentActionPreview")

        self.assertIsNotNone(preview)
        self.assertEqual(preview.text(), "Primary")

    def test_color_lab_uses_selected_color_resource(self) -> None:
        window = ResourcesWindow()
        target = next(item for item in window.catalog_items() if item.name == "day.accent")

        window.select_resource(target.resource_id)

        swatch = window.findChild(QLabel, "ResourceColorLabSwatch")
        self.assertIsNotNone(swatch)
        self.assertEqual(window.color_lab_hex(), "#147efb")
        self.assertEqual(swatch.property("preview_value"), "#147efb")
        self.assertIn("background: #147efb", swatch.styleSheet())

    def test_color_lab_hex_input_updates_preview(self) -> None:
        window = ResourcesWindow()

        window.set_color_lab_hex("2dd4bf")

        swatch = window.findChild(QLabel, "ResourceColorLabSwatch")
        self.assertIsNotNone(swatch)
        self.assertEqual(window.color_lab_hex(), "#2dd4bf")
        self.assertEqual(swatch.property("preview_value"), "#2dd4bf")
        self.assertIn("background: #2dd4bf", swatch.styleSheet())

    def test_color_lab_copies_hex_and_rgb_values(self) -> None:
        window = ResourcesWindow()
        window.set_color_lab_hex("#2dd4bf")

        window.copy_color_lab_hex()

        self.assertEqual(QApplication.clipboard().text(), "#2dd4bf")

        window.copy_color_lab_rgb()

        self.assertEqual(QApplication.clipboard().text(), "rgb(45, 212, 191)")


if __name__ == "__main__":
    unittest.main()
