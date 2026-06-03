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
            bg="#f5f5f7",
            surface="#ffffff",
            surface_alt="#fbfbfd",
            surface_elevated="#f0f0f3",
            border="#dadde3",
            border_strong="#d2d2d7",
            text="#1d1d1f",
            text_muted="#6e6e73",
            text_soft="#8e8e93",
            accent="#007aff",
            accent_hover="#0a84ff",
            accent_text="#ffffff",
            selection="#eaf3ff",
            input="#ffffff",
        ),
        typography=ThemeTypography(),
        spacing=ThemeSpacing(control_pad_y=8),
        radius=ThemeRadius(),
        state_colors=ThemeStateColors(
            success="#34c759",
            warning="#ff9f0a",
            error="#ff453a",
            running="#64d2ff",
        ),
    ),
    "night": ThemePalette(
        name="night",
        colors=ThemeColors(
            bg="#1c1c1e",
            surface="#242426",
            surface_alt="#2c2c2e",
            surface_elevated="#3a3a3c",
            border="#3a3a3c",
            border_strong="#48484a",
            text="#f5f5f7",
            text_muted="#a1a1a6",
            text_soft="#8e8e93",
            accent="#0a84ff",
            accent_hover="#409cff",
            accent_text="#ffffff",
            selection="#163a5f",
            input="#1f1f21",
        ),
        typography=ThemeTypography(),
        spacing=ThemeSpacing(control_pad_y=8),
        radius=ThemeRadius(),
        state_colors=ThemeStateColors(
            success="#30d158",
            warning="#ff9f0a",
            error="#ff453a",
            running="#64d2ff",
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
    pane_fill = "rgba(255,255,255,0.72)" if palette.name == "day" else "rgba(36,36,38,0.88)"
    toolbar_fill = "rgba(255,255,255,0.86)" if palette.name == "day" else "rgba(44,44,46,0.92)"
    button_fill = "rgba(255,255,255,0.78)" if palette.name == "day" else "rgba(58,58,60,0.74)"
    status_bg = "#f2f8ff" if palette.name == "day" else "#17324f"
    error_bg = "#fff1f0" if palette.name == "day" else "#4a1f1d"
    success_bg = "#eefbf2" if palette.name == "day" else "#173a23"
    return f"""
QWidget {{
    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "DengXian", "PingFang SC", sans-serif;
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
    background: {pane_fill};
    border: 1px solid {c.border};
    border-radius: {r.pane}px;
}}
#Brand {{
    color: {c.text};
    font-size: {t.title_size}px;
    font-weight: 650;
}}
#SidebarMeta, #SessionRowMeta, #InspectorMeta, #ChatHeaderMeta {{
    color: {c.text_muted};
    font-size: {t.meta_size}px;
}}
#SectionLabel {{
    color: {c.text_soft};
    font-size: {t.section_size}px;
    font-weight: 650;
    letter-spacing: 0px;
    padding-top: 6px;
}}
#AccentButton, #PrimaryButton {{
    background: {c.accent};
    border: 1px solid {c.accent};
    border-radius: {r.card}px;
    color: {c.accent_text};
    font-weight: 650;
    padding: {s.control_pad_y}px {s.control_pad_x}px;
}}
#AccentButton:hover, #PrimaryButton:hover {{
    background: {c.accent_hover};
}}
#PrimaryButton:disabled {{
    background: {c.surface_elevated};
    color: {c.text_muted};
    border-color: {c.border};
}}
QPushButton {{
    background: {button_fill};
    border: 1px solid {c.border};
    border-radius: {r.card}px;
    color: {c.text};
    padding: {s.control_pad_y}px {s.control_pad_x}px;
}}
QPushButton:hover {{
    border-color: {c.border_strong};
    background: {c.surface_alt};
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
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r.row}px;
}}
#SessionRow:hover {{
    background: {c.surface_alt};
    border-color: {c.border};
}}
#SessionRow[selected="true"] {{
    background: {c.selection};
    border: 1px solid {c.border_strong};
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
    font-weight: 650;
}}
#SessionRowStatus {{
    background: transparent;
    border-radius: {r.chip}px;
    color: {c.text_muted};
    font-size: 11px;
    font-weight: 650;
    padding: 3px 6px;
}}
#SessionRowStatus[status="running"] {{
    background: {status_bg};
    color: {state.running};
}}
#SessionRowStatus[status="error"] {{
    background: {error_bg};
    color: {state.error};
}}
#SessionRowStatus[status="success"] {{
    background: {success_bg};
    color: {state.success};
}}
#SessionRowDeleteButton {{
    background: transparent;
    border: 0;
    border-radius: {r.chip}px;
    color: {c.text_muted};
    padding: 2px;
}}
#SessionRowDeleteButton:hover {{
    background: {c.surface_elevated};
    color: {c.text};
}}
#ThemeSegment {{
    background: {c.surface_elevated};
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
    border: 1px solid {c.border};
}}
#SettingsButton {{
    padding: {s.control_pad_y}px {s.control_pad_x}px;
}}
#ChatHeader {{
    background: {toolbar_fill};
    border-bottom: 1px solid {c.border};
    border-top-left-radius: {r.pane}px;
    border-top-right-radius: {r.pane}px;
}}
#ChatHeaderTitle {{
    color: {c.text};
    font-size: 16px;
    font-weight: 650;
}}
#ChatRunStatus, #InspectorStatusIndicator {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: {r.chip}px;
    color: {c.text_muted};
    font-size: {t.meta_size}px;
    font-weight: 650;
    padding: 3px 7px;
}}
#ChatRunStatus[status="running"], #InspectorStatusIndicator[status="running"] {{
    background: {status_bg};
    border-color: {state.running};
    color: {c.text};
}}
#ChatRunStatus[status="error"], #InspectorStatusIndicator[status="error"] {{
    background: {error_bg};
    border-color: {state.error};
    color: {state.error};
}}
#ChatRunStatus[status="success"], #InspectorStatusIndicator[status="success"] {{
    background: {success_bg};
    border-color: {state.success};
    color: {state.success};
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
    background: {c.accent};
    color: {c.accent_text};
    border: 1px solid {c.accent};
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
    font-weight: 650;
}}
#ToolStatusRow {{
    background: {toolbar_fill};
    border: 1px solid {c.border};
    border-radius: {r.card}px;
}}
#ToolStatusRow[status="running"] {{
    border-color: {state.running};
    background: {status_bg};
}}
#ToolStatusRow[status="success"] {{
    border-color: {state.success};
    background: {success_bg};
}}
#ToolStatusRow[status="error"] {{
    border-color: {state.error};
    background: {error_bg};
}}
#ToolStatusIcon {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: {r.chip}px;
    color: {c.text_soft};
    font-size: 12px;
    font-weight: 650;
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
    font-weight: 650;
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
    font-weight: 650;
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
    background: {toolbar_fill};
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
    font-weight: 650;
}}
QTabWidget::pane {{
    border: 1px solid {c.border};
    background: {c.surface};
    border-radius: {r.card}px;
    top: -1px;
}}
QTabBar::tab {{
    background: {c.surface_elevated};
    border: 1px solid transparent;
    color: {c.text_muted};
    padding: 7px 13px;
    margin: 0;
    border-radius: {r.chip}px;
}}
QTabBar::tab:selected {{
    color: {c.text};
    background: {c.surface};
    border: 1px solid {c.border};
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
    font-weight: 650;
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
    font-weight: 650;
}}
#InspectorConfigValue {{
    color: {c.text};
    font-size: 12px;
}}
QDialog {{
    background: {c.bg};
}}
"""
