from __future__ import annotations

import pkgutil
from dataclasses import asdict, dataclass
from pathlib import Path

import PySide6
from PySide6.QtGui import QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QStyle

from gui.theme import PALETTES

_UNSUPPORTED_FONT_FILES = {"mstmc.ttf"}


@dataclass(frozen=True, slots=True)
class ResourceItem:
    resource_id: str
    category: str
    name: str
    source: str
    summary: str
    usage: str
    value: str = ""
    selector: str = ""
    properties: tuple[tuple[str, str], ...] = ()


def build_resource_catalog(*, extra_font_dirs: tuple[Path, ...] = ()) -> tuple[ResourceItem, ...]:
    items: list[ResourceItem] = []
    items.extend(_font_items(extra_font_dirs))
    items.extend(_color_items())
    items.extend(_style_items())
    items.extend(_component_items())
    items.extend(_icon_items())
    items.extend(_standard_icon_items())
    items.extend(_palette_role_items())
    items.extend(_layout_items())
    items.extend(_qt_module_items())
    return tuple(items)


def resource_spec_as_markdown(item: ResourceItem) -> str:
    lines = [
        f"# {item.name}",
        f"Category: {item.category}",
        f"Source: {item.source}",
    ]
    if item.selector:
        lines.append(f"Selector: {item.selector}")
    if item.value:
        lines.append(f"Value: {item.value}")
    lines.extend(["", "Summary:", item.summary, "", "Usage:", item.usage])
    if item.properties:
        lines.extend(["", "Properties:"])
        lines.extend(f"- {key}: {value}" for key, value in item.properties)
    return "\n".join(lines)


def _font_items(extra_font_dirs: tuple[Path, ...]) -> list[ResourceItem]:
    fonts_dir = Path("C:/Windows/Fonts")
    fonts = [
        ("Microsoft YaHei UI", "msyh.ttc", "Default Simplified Chinese UI family used first in the app font stack."),
        ("Segoe UI Variable Text", "SegUIVar.ttf", "Windows variable UI family used as a refined fallback."),
        ("SF Pro Text", "", "macOS system UI fallback in the shared stylesheet stack."),
        ("PingFang SC", "", "macOS Simplified Chinese fallback in the shared stylesheet stack."),
        ("Segoe UI", "segoeui.ttf", "Windows classic UI fallback."),
        ("Consolas", "consola.ttf", "Primary monospace code font for inline and block code."),
        ("Courier New", "cour.ttf", "Monospace fallback for code rendering."),
        ("DejaVu Sans Mono", "DejaVuSansMono.ttf", "Cross-platform monospace fallback when present."),
        ("Liberation Mono", "LiberationMono-Regular.ttf", "Linux-oriented monospace fallback when present."),
    ]
    curated = [
        ResourceItem(
            resource_id=f"font:{name.lower().replace(' ', '-')}",
            category="Fonts",
            name=name,
            source=str(fonts_dir / file_name) if file_name else "Qt/system font fallback",
            summary=summary,
            usage=f'Use with QFont.setFamilies(["{name}", ...]) or through the shared application QFont stack.',
            value=name,
            properties=(
                ("family", name),
                ("available", str((fonts_dir / file_name).exists()) if file_name else "system-dependent"),
            ),
        )
        for name, file_name, summary in fonts
    ]
    discovered = _font_file_items((fonts_dir,), origin="system")
    discovered.extend(_font_file_items((*_default_external_font_dirs(), *extra_font_dirs), origin="external"))
    return _dedupe_font_items((*curated, *discovered))


def _font_file_items(font_dirs: tuple[Path, ...], *, origin: str) -> list[ResourceItem]:
    items: list[ResourceItem] = []
    for font_dir in font_dirs:
        if not font_dir.exists():
            continue
        for font_path in sorted(_iter_font_files(font_dir)):
            families = _font_families_for_file(font_path) if _should_register_font_file(font_path, origin=origin) else ()
            family_names = families or (_font_family_from_filename(font_path),)
            for family in family_names:
                items.append(
                    ResourceItem(
                        resource_id=f"font-file:{origin}:{_slug(family)}:{font_path.name.lower()}",
                        category="Fonts",
                        name=family,
                        source=str(font_path),
                        summary=f"{origin.title()} font discovered from {font_path.parent}.",
                        usage=(
                            f'Use with QFont.setFamilies(["{family}", ...]). '
                            f"For external fonts, place .ttf/.ttc/.otf files in resources/fonts, "
                            f"src/gui/assets/fonts, or .lora/fonts before launching lora-resources."
                        ),
                        value=family,
                        properties=(
                            ("family", family),
                            ("origin", origin),
                            ("file", font_path.name),
                            ("registered", str(bool(families))),
                        ),
                    )
                )
    return items


def _iter_font_files(font_dir: Path):
    for pattern in ("*.ttf", "*.ttc", "*.otf"):
        yield from font_dir.glob(pattern)


def _default_external_font_dirs() -> tuple[Path, ...]:
    return (
        Path("resources/fonts"),
        Path("src/gui/assets/fonts"),
        Path(".lora/fonts"),
    )


def _font_families_for_file(font_path: Path) -> tuple[str, ...]:
    if QApplication.instance() is None:
        return ()
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id < 0:
        return ()
    return tuple(QFontDatabase.applicationFontFamilies(font_id))


def _should_register_font_file(font_path: Path, *, origin: str) -> bool:
    return origin == "external" and QApplication.instance() is not None and font_path.name.lower() not in _UNSUPPORTED_FONT_FILES


def _font_family_from_filename(font_path: Path) -> str:
    known = {
        "arial": "Arial",
        "arialbd": "Arial Bold",
        "arialbi": "Arial Bold Italic",
        "ariali": "Arial Italic",
        "bahnschrift": "Bahnschrift",
        "calibri": "Calibri",
        "cambria": "Cambria",
        "candara": "Candara",
        "consola": "Consolas",
        "consolab": "Consolas Bold",
        "consolai": "Consolas Italic",
        "consolaz": "Consolas Bold Italic",
        "cour": "Courier New",
        "deng": "DengXian",
        "msyh": "Microsoft YaHei UI",
        "msyhbd": "Microsoft YaHei UI Bold",
        "segoeui": "Segoe UI",
        "seguisym": "Segoe UI Symbol",
        "simhei": "SimHei",
    }
    stem = font_path.stem.lower()
    if stem in known:
        return known[stem]
    return " ".join(part.capitalize() for part in font_path.stem.replace("_", "-").split("-") if part) or font_path.stem


def _dedupe_font_items(items: tuple[ResourceItem, ...]) -> list[ResourceItem]:
    seen: set[tuple[str, str]] = set()
    deduped: list[ResourceItem] = []
    for item in items:
        key = (item.name.casefold(), item.source.casefold())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")


def _color_items() -> list[ResourceItem]:
    items: list[ResourceItem] = []
    for palette_name, palette in PALETTES.items():
        for group_name, group in (("colors", palette.colors), ("state_colors", palette.state_colors)):
            for field_name, value in asdict(group).items():
                token_name = f"{palette_name}.{field_name}"
                source = f"gui.theme.PALETTES['{palette_name}'].{group_name}.{field_name}"
                items.append(
                    ResourceItem(
                        resource_id=f"color:{token_name}",
                        category="Colors",
                        name=token_name,
                        source=source,
                        summary=f"{palette_name} theme {field_name.replace('_', ' ')} token.",
                        usage=f"Use this token in QSS as {source} when styling matching {palette_name} theme surfaces.",
                        value=str(value),
                        properties=(("theme", palette_name), ("token", field_name)),
                    )
                )
    return items


def _style_items() -> list[ResourceItem]:
    items: list[ResourceItem] = []
    for palette_name, palette in PALETTES.items():
        for field_name, value in asdict(palette.typography).items():
            items.append(_style_item(palette_name, "typography", field_name, value, "Font size token in pixels."))
        for field_name, value in asdict(palette.spacing).items():
            items.append(_style_item(palette_name, "spacing", field_name, value, "Spacing token in pixels."))
        for field_name, value in asdict(palette.radius).items():
            items.append(_style_item(palette_name, "radius", field_name, value, "Border radius token in pixels."))
    items.extend(
        [
            ResourceItem(
                resource_id="style:glass-pane",
                category="Styles",
                name="Glass pane",
                source="#SessionSidebar, #ChatPane, #TraceInspector",
                selector="#SessionSidebar, #ChatPane, #TraceInspector",
                summary="Translucent pane treatment used for the major application columns.",
                usage="Apply to major framed work surfaces that should sit over the gradient app background.",
                properties=(("border-radius", "ThemeRadius.pane"), ("background", "ThemeColors.glass")),
            ),
            ResourceItem(
                resource_id="style:accent-action",
                category="Styles",
                name="Accent action",
                source="#AccentButton, #PrimaryButton",
                selector="#AccentButton, #PrimaryButton",
                summary="Primary blue gradient action style used for committed actions.",
                usage="Use for one high-priority action per surface, such as creating or applying.",
                properties=(("background", "accent_fill"), ("font-weight", "700")),
            ),
            ResourceItem(
                resource_id="style:segmented-control",
                category="Styles",
                name="Segmented control",
                source="#ThemeSegment, #ThemeDayButton, #ThemeNightButton",
                selector="#ThemeSegment",
                summary="Two-option segmented control treatment used by the day/night theme switcher.",
                usage="Use for compact mutually exclusive modes where both options should stay visible.",
                properties=(("container", "#ThemeSegment"), ("buttons", "#ThemeDayButton / #ThemeNightButton")),
            ),
        ]
    )
    return items


def _style_item(palette_name: str, group: str, field_name: str, value: object, summary: str) -> ResourceItem:
    token_name = f"{palette_name}.{group}.{field_name}"
    source = f"gui.theme.PALETTES['{palette_name}'].{group}.{field_name}"
    return ResourceItem(
        resource_id=f"style:{token_name}",
        category="Styles",
        name=token_name,
        source=source,
        summary=summary,
        usage=f"Use {source} when aligning custom widgets with the {palette_name} theme scale.",
        value=str(value),
        properties=(("theme", palette_name), ("token", field_name), ("group", group)),
    )


def _component_items() -> list[ResourceItem]:
    components = [
        ("QPushButton", "QPushButton", "Standard command button, styled globally and specialized by objectName."),
        ("QToolButton", "QToolButton", "Compact icon or menu button for dense tool surfaces."),
        ("QCheckBox", "QCheckBox", "Boolean option control with checked, unchecked, and disabled states."),
        ("QRadioButton", "QRadioButton", "Single-choice option control for mutually exclusive groups."),
        ("QComboBox", "QComboBox", "Dropdown selector for compact option sets."),
        ("QLineEdit", "QLineEdit", "Single-line text input used in settings and search-like controls."),
        ("QPlainTextEdit", "QPlainTextEdit", "Plain multiline text surface for specs, code, and logs."),
        ("QTextEdit", "QTextEdit", "Rich text editor/view when formatted text is needed."),
        ("QSpinBox", "QSpinBox", "Bounded integer input used for numeric settings."),
        ("QDoubleSpinBox", "QDoubleSpinBox", "Bounded decimal numeric input."),
        ("QSlider", "QSlider", "Continuous value selector for numeric tuning."),
        ("QProgressBar", "QProgressBar", "Progress and completion indicator."),
        ("QScrollBar", "QScrollBar", "Scrollable range control used by scroll areas and custom panes."),
        ("QDial", "QDial", "Circular numeric control for compact analog adjustments."),
        ("QLCDNumber", "QLCDNumber", "Segment display for numeric readouts."),
        ("QDateEdit", "QDateEdit", "Date input control."),
        ("QTimeEdit", "QTimeEdit", "Time input control."),
        ("QDateTimeEdit", "QDateTimeEdit", "Combined date and time input control."),
        ("QFontComboBox", "QFontComboBox", "Font family picker backed by installed fonts."),
        ("QListWidget", "QListWidget", "Simple list for configuration rows or single-column resource lists."),
        ("QTreeWidget", "QTreeWidget", "Hierarchical event, file, tool, or category browser."),
        ("QTableWidget", "QTableWidget", "Small tabular data grid for dense structured records."),
        ("QTabWidget", "QTabWidget", "Tabbed secondary navigation, already used in the trace inspector."),
        ("QScrollArea", "QScrollArea", "Scrollable custom widget host for dense card grids."),
        ("QSplitter", "QSplitter", "Resizable multi-pane layout for independent tools like Resources."),
        ("QGroupBox", "QGroupBox", "Labeled grouping frame for related controls."),
        ("QFrame", "QFrame", "Styled surface primitive for cards, headers, and panes."),
        ("QLabel", "QLabel", "Static text, metadata, icon pixmap, and sample typography primitive."),
        ("QDialogButtonBox", "QDialogButtonBox", "Native dialog action row for OK/Cancel style workflows."),
        ("QMenu", "QMenu", "Popup menu for row actions and overflow commands."),
        ("QMessageBox", "QMessageBox", "Native message dialog for warnings, errors, and confirmations."),
        ("QFileDialog", "QFileDialog", "Native file and folder picker dialog."),
        ("QColorDialog", "QColorDialog", "Native color picker dialog."),
        ("QFontDialog", "QFontDialog", "Native font picker dialog."),
    ]
    return [
        ResourceItem(
            resource_id=f"component:{name.lower()}",
            category="Components",
            name=name,
            source=f"PySide6.QtWidgets.{name}",
            selector=selector,
            summary=summary,
            usage=f"Instantiate {name} in PySide6 and set objectName when a more specific QSS selector is needed.",
            properties=(("module", "PySide6.QtWidgets"), ("selector", selector)),
        )
        for name, selector, summary in components
    ]


def _icon_items() -> list[ResourceItem]:
    icon_dir = Path(__file__).resolve().parent / "assets" / "icons"
    items: list[ResourceItem] = []
    for icon_path in sorted(icon_dir.glob("*.svg")):
        name = icon_path.name
        icon_name = icon_path.stem
        items.append(
            ResourceItem(
                resource_id=f"icon:{icon_name}",
                category="Icons",
                name=name,
                source=str(icon_path),
                summary=f"Bundled SVG icon loaded through gui.icons.icon('{icon_name}').",
                usage=f"Use gui.icons.icon('{icon_name}') on QPushButton, QToolButton, or QLabel pixmaps.",
                value=icon_name,
                properties=(("loader", "gui.icons.icon"), ("file", name)),
            )
        )
    return items


def _standard_icon_items() -> list[ResourceItem]:
    items: list[ResourceItem] = []
    for name in sorted(enum_name for enum_name in dir(QStyle.StandardPixmap) if enum_name.startswith("SP_")):
        items.append(
            ResourceItem(
                resource_id=f"standard-icon:{name}",
                category="Standard Icons",
                name=name,
                source=f"PySide6.QtWidgets.QStyle.StandardPixmap.{name}",
                summary="Qt style-provided standard icon from the active platform/style.",
                usage=f"Use widget.style().standardIcon(QStyle.StandardPixmap.{name}) for platform-native iconography.",
                value=name,
                properties=(("enum", name), ("module", "PySide6.QtWidgets.QStyle")),
            )
        )
    return items


def _palette_role_items() -> list[ResourceItem]:
    items: list[ResourceItem] = []
    for role in sorted(name for name in dir(QPalette.ColorRole) if not name.startswith("_")):
        if role in {"NColorRoles", "NoRole"}:
            continue
        items.append(
            ResourceItem(
                resource_id=f"palette-role:{role}",
                category="Palette Roles",
                name=f"Palette.{role}",
                source=f"PySide6.QtGui.QPalette.ColorRole.{role}",
                summary="Qt palette role resolved by the active application palette.",
                usage=f"Use QApplication.palette().color(QPalette.ColorRole.{role}) for native theme-aware colors.",
                value=role,
                properties=(("role", role), ("module", "PySide6.QtGui.QPalette")),
            )
        )
    return items


def _layout_items() -> list[ResourceItem]:
    layouts = [
        ("QHBoxLayout", "Horizontal row layout for compact toolbars and metadata lines."),
        ("QVBoxLayout", "Vertical stack layout for panes, forms, and card contents."),
        ("QGridLayout", "Grid layout for resource cards, forms, and dense option matrices."),
        ("QFormLayout", "Two-column label/value form layout for settings and specs."),
        ("QStackedLayout", "Single-visible-page layout for wizard-like surfaces."),
        ("QSplitter", "User-resizable multi-pane layout widget."),
        ("QScrollArea", "Scrollable viewport for custom content larger than its pane."),
    ]
    return [
        ResourceItem(
            resource_id=f"layout:{name.lower()}",
            category="Layouts",
            name=name,
            source=f"PySide6.QtWidgets.{name}",
            summary=summary,
            usage=f"Use {name} to compose Qt widgets while preserving native resize behavior.",
            value=name,
            properties=(("module", "PySide6.QtWidgets"), ("layout", name)),
        )
        for name, summary in layouts
    ]


def _qt_module_items() -> list[ResourceItem]:
    items: list[ResourceItem] = []
    for module_info in sorted(pkgutil.iter_modules(PySide6.__path__), key=lambda entry: entry.name):
        if not module_info.name.startswith("Qt"):
            continue
        items.append(
            ResourceItem(
                resource_id=f"qt-module:{module_info.name.lower()}",
                category="Qt Modules",
                name=module_info.name,
                source=f"PySide6.{module_info.name}",
                summary="Installed PySide6 module available in the current environment.",
                usage=f"Import with `from PySide6 import {module_info.name}` when this module's APIs are needed.",
                value="installed",
                properties=(("module", module_info.name), ("package", "PySide6")),
            )
        )
    return items
