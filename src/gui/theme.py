from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QFontDatabase

THEMES = ("day", "night")


@dataclass(frozen=True, slots=True)
class ThemeColors:
    bg: str
    surface: str
    surface_alt: str
    surface_elevated: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_soft: str
    accent: str
    accent_hover: str
    accent_text: str
    selection: str
    input: str


@dataclass(frozen=True, slots=True)
class ThemeTypography:
    body_size: int = 13
    message_size: int = 14
    meta_size: int = 11
    title_size: int = 22
    section_size: int = 11


@dataclass(frozen=True, slots=True)
class ThemeSpacing:
    pane_margin: int = 14
    gutter: int = 14
    pane_padding: int = 18
    control_pad_y: int = 9
    control_pad_x: int = 12


@dataclass(frozen=True, slots=True)
class ThemeRadius:
    chip: int = 6
    card: int = 8
    row: int = 8
    pane: int = 12


@dataclass(frozen=True, slots=True)
class ThemeStateColors:
    success: str
    warning: str
    error: str
    running: str


@dataclass(frozen=True, slots=True)
class ThemePalette:
    name: str
    colors: ThemeColors
    typography: ThemeTypography
    spacing: ThemeSpacing
    radius: ThemeRadius
    state_colors: ThemeStateColors


PALETTES: dict[str, ThemePalette] = {
    "day": ThemePalette(
        name="day",
        colors=ThemeColors(
            bg="#eef3f6",
            surface="#ffffff",
            surface_alt="#f7fafc",
            surface_elevated="#edf5f7",
            border="#d5dee6",
            border_strong="#aebbc6",
            text="#1e2a2f",
            text_muted="#63717c",
            text_soft="#8a98a3",
            accent="#0f6f78",
            accent_hover="#0e7c86",
            accent_text="#ffffff",
            selection="#dcecef",
            input="#fbfdfe",
        ),
        typography=ThemeTypography(),
        spacing=ThemeSpacing(),
        radius=ThemeRadius(),
        state_colors=ThemeStateColors(
            success="#0b8a6a",
            warning="#b57613",
            error="#c84645",
            running="#0e7c86",
        ),
    ),
    "night": ThemePalette(
        name="night",
        colors=ThemeColors(
            bg="#111719",
            surface="#182125",
            surface_alt="#202b30",
            surface_elevated="#26343a",
            border="#314247",
            border_strong="#587078",
            text="#dce8ea",
            text_muted="#96a8ad",
            text_soft="#73888f",
            accent="#39c6c9",
            accent_hover="#5ed6d9",
            accent_text="#071012",
            selection="#213f47",
            input="#131d21",
        ),
        typography=ThemeTypography(),
        spacing=ThemeSpacing(),
        radius=ThemeRadius(),
        state_colors=ThemeStateColors(
            success="#32c99a",
            warning="#e7a83c",
            error="#ff6b68",
            running="#39c6c9",
        ),
    ),
}


def available_themes() -> tuple[str, str]:
    return THEMES


def theme_stylesheet(theme: str) -> str:
    try:
        palette = PALETTES[theme]
    except KeyError as exc:
        raise ValueError(f"Unknown theme {theme!r}") from exc
    return _stylesheet(palette)


def register_ui_fonts() -> None:
    for font_path in _candidate_ui_font_paths():
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))


def _candidate_ui_font_paths() -> tuple[Path, ...]:
    fonts_dir = Path("C:/Windows/Fonts")
    return (
        fonts_dir / "msyh.ttc",
        fonts_dir / "msyhbd.ttc",
        fonts_dir / "NotoSansSC-VF.ttf",
        fonts_dir / "Deng.ttf",
        fonts_dir / "simhei.ttf",
    )


def _stylesheet(palette: ThemePalette) -> str:
    c = palette.colors
    t = palette.typography
    s = palette.spacing
    r = palette.radius
    state = palette.state_colors
    sidebar_gradient_start = "#ffffff" if palette.name == "day" else "#121b1f"
    pane_gradient_end = "#f2f7f9" if palette.name == "day" else "#141d21"
    return f"""
QWidget {{
    font-family: "Segoe UI Variable Text", "DengXian", "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: {t.body_size}px;
    color: {c.text};
}}
QMainWindow, #CentralShell {{
    background: {c.bg};
}}
#CentralShell {{
    border: 0;
}}
#SessionSidebar, #ChatPane, #TraceInspector {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {sidebar_gradient_start}, stop:0.58 {c.surface}, stop:1 {pane_gradient_end});
    border: 1px solid {c.border};
    border-radius: {r.pane}px;
}}
#Brand {{
    color: {c.text};
    font-size: {t.title_size}px;
    font-weight: 800;
}}
#SidebarMeta, #SessionRowMeta, #InspectorMeta, #ChatHeaderMeta {{
    color: {c.text_muted};
    font-size: {t.meta_size}px;
}}
#SectionLabel {{
    color: {c.text_soft};
    font-size: {t.section_size}px;
    font-weight: 700;
    letter-spacing: 0px;
    padding-top: 6px;
}}
#AccentButton, #PrimaryButton {{
    background: {c.accent};
    border: 1px solid {c.accent_hover};
    border-radius: {r.card}px;
    color: {c.accent_text};
    font-weight: 700;
    padding: {s.control_pad_y}px {s.control_pad_x}px;
}}
#AccentButton:hover, #PrimaryButton:hover {{
    background: {c.accent_hover};
}}
#PrimaryButton:disabled {{
    background: {c.surface_elevated};
    color: {c.text_muted};
    border-color: {c.border_strong};
}}
QPushButton {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: {r.card}px;
    color: {c.text};
    padding: {s.control_pad_y}px {s.control_pad_x}px;
}}
QPushButton:hover {{
    border-color: {c.border_strong};
    background: {c.surface_elevated};
}}
QPushButton:disabled {{
    color: {c.text_soft};
    background: {c.surface_alt};
}}
#SessionList {{
    background: transparent;
    border: 0;
    color: {c.text};
    outline: 0;
}}
#SessionList::item {{
    margin: 3px 0;
    padding: 0;
    border: 0;
    outline: 0;
}}
#SessionList::item:selected {{
    background: transparent;
    border: 0;
    outline: 0;
}}
#SessionRow {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: {r.row}px;
}}
#SessionRow:hover {{
    background: {c.surface_elevated};
    border-color: {c.border_strong};
}}
#SessionRow[selected="true"] {{
    background: {c.selection};
    border: 1px solid {c.accent};
}}
#SessionRowRail {{
    background: transparent;
    border-radius: {r.chip}px;
}}
#SessionRowRail[selected="true"] {{
    background: {c.accent};
}}
#SessionRowTitle {{
    color: {c.text};
    font-weight: 700;
}}
#SessionRowDeleteButton {{
    background: transparent;
    border: 0;
    border-radius: {r.chip}px;
    color: {c.text_muted};
    padding: 2px;
}}
#SessionRowDeleteButton:hover {{
    background: {c.surface};
    color: {c.text};
}}
#ThemeSegment {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: {r.card}px;
}}
#ThemeDayButton, #ThemeNightButton {{
    background: transparent;
    border: 0;
    border-radius: {r.chip}px;
    color: {c.text_muted};
    padding: 8px 10px;
}}
#ThemeDayButton:checked, #ThemeNightButton:checked {{
    background: {c.surface};
    color: {c.text};
    border: 1px solid {c.border_strong};
}}
#SettingsButton {{
    padding: 9px 12px;
}}
#ChatHeader {{
    background: {c.surface};
    border-bottom: 1px solid {c.border};
    border-top-left-radius: {r.pane}px;
    border-top-right-radius: {r.pane}px;
}}
#ChatHeaderTitle {{
    color: {c.text};
    font-size: 16px;
    font-weight: 800;
}}
#ChatRunStatus, #InspectorStatusIndicator {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: {r.chip}px;
    color: {c.text_muted};
    font-size: {t.meta_size}px;
    font-weight: 800;
    padding: 3px 7px;
}}
#ChatRunStatus[status="running"], #InspectorStatusIndicator[status="running"] {{
    background: {state.running};
    border-color: {state.running};
    color: {c.accent_text};
}}
#ChatRunStatus[status="error"], #InspectorStatusIndicator[status="error"] {{
    background: {state.error};
    border-color: {state.error};
    color: #ffffff;
}}
#ChatRunStatus[status="success"], #InspectorStatusIndicator[status="success"] {{
    background: {state.success};
    border-color: {state.success};
    color: #ffffff;
}}
#ChatScroll, #ChatScrollBody {{
    background: transparent;
    border: 0;
}}
#MessageGroup, #UserMessageGroup, #AssistantMessageGroup {{
    background: transparent;
    border: 0;
}}
#UserBubble, #AssistantBubble {{
    border-radius: {r.card}px;
    padding: 12px 15px;
    max-width: 720px;
    line-height: 1.45;
    font-size: {t.message_size}px;
}}
#UserBubble {{
    background: {c.selection};
    color: {c.text};
    border: 1px solid {c.border_strong};
}}
#AssistantBubble {{
    background: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
}}
#UserAvatar, #AssistantAvatar {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: {r.card}px;
    color: {c.text_muted};
    font-weight: 800;
}}
#ToolStatusRow {{
    background: {c.surface};
    border: 1px solid {c.border};
    border-radius: {r.card}px;
}}
#ToolStatusRow[status="running"] {{
    border-color: {state.running};
}}
#ToolStatusRow[status="success"] {{
    border-color: {state.success};
}}
#ToolStatusRow[status="error"] {{
    border-color: {state.error};
}}
#ToolStatusIcon {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: {r.chip}px;
    color: {c.text_soft};
    font-size: 12px;
    font-weight: 800;
    padding: 0;
}}
#ToolStatusToggle {{
    background: transparent;
    border: 1px solid {c.border};
    border-radius: {r.chip}px;
    color: {c.text_soft};
    padding: 0;
}}
#ToolStatusToggle:hover {{
    background: {c.surface_alt};
}}
#ToolStatusTitle {{
    color: {c.text};
    font-size: 13px;
    font-weight: 800;
}}
#ToolStatusDetail {{
    color: {c.text_muted};
    font-size: {t.meta_size}px;
}}
#ToolCallRow {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: {r.chip}px;
}}
#ToolCallStatus {{
    color: {c.text_soft};
    font-size: {t.meta_size}px;
    font-weight: 800;
}}
#ToolCallStatus[status="running"] {{
    color: {state.running};
}}
#ToolCallStatus[status="success"] {{
    color: {state.success};
}}
#ToolCallStatus[status="error"] {{
    color: {state.error};
}}
#ToolCallLine {{
    color: {c.text_muted};
    font-size: 12px;
}}
#Composer {{
    background: {c.surface};
    border-top: 1px solid {c.border};
    border-bottom-left-radius: {r.pane}px;
    border-bottom-right-radius: {r.pane}px;
}}
#MessageInput, QLineEdit, QPlainTextEdit, QSpinBox {{
    background: {c.input};
    border: 1px solid {c.border};
    border-radius: {r.card}px;
    color: {c.text};
    padding: 10px;
    selection-background-color: {c.selection};
}}
#MessageInput:focus, QLineEdit:focus, QPlainTextEdit:focus {{
    border-color: {c.accent};
}}
#InspectorHeader {{
    background: transparent;
    border: 0;
}}
#InspectorStatus {{
    color: {c.text};
    font-size: 16px;
    font-weight: 800;
}}
QTabWidget::pane {{
    border: 1px solid {c.border};
    background: {c.surface};
    border-radius: {r.card}px;
    top: -1px;
}}
QTabBar::tab {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    color: {c.text_muted};
    padding: 8px 13px;
    margin-right: 3px;
    border-top-left-radius: {r.chip}px;
    border-top-right-radius: {r.chip}px;
}}
QTabBar::tab:selected {{
    color: {c.text};
    background: {c.surface};
    border-bottom-color: {c.surface};
}}
QTreeWidget, QListWidget {{
    background: {c.surface};
    border: 0;
    color: {c.text};
    alternate-background-color: {c.surface_alt};
}}
QHeaderView::section {{
    background: {c.surface_alt};
    border: 0;
    border-bottom: 1px solid {c.border};
    color: {c.text_muted};
    font-weight: 700;
    padding: 7px;
}}
QTreeWidget::item, QListWidget::item {{
    padding: 7px;
    border-bottom: 1px solid {c.border};
}}
QScrollBar:vertical {{
    background: {c.surface_alt};
    width: 12px;
    margin: 0;
    border: 0;
}}
QScrollBar::handle:vertical {{
    background: {c.border_strong};
    border-radius: 5px;
    min-height: 34px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c.text_soft};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    border: 0;
    background: transparent;
}}
QScrollBar:horizontal {{
    background: {c.surface_alt};
    height: 12px;
    margin: 0;
    border: 0;
}}
QScrollBar::handle:horizontal {{
    background: {c.border_strong};
    border-radius: 5px;
    min-width: 34px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    border: 0;
    background: transparent;
}}
#InspectorConfigRow {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: {r.row}px;
}}
#InspectorConfigKey {{
    color: {c.text_muted};
    font-size: {t.meta_size}px;
    font-weight: 800;
}}
#InspectorConfigValue {{
    color: {c.text};
    font-size: 12px;
}}
QDialog {{
    background: {c.bg};
}}
"""
