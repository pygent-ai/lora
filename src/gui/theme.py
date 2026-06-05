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
    pane_margin: int = 18
    gutter: int = 18
    pane_padding: int = 20
    control_pad_y: int = 9
    control_pad_x: int = 12


@dataclass(frozen=True, slots=True)
class ThemeRadius:
    chip: int = 6
    card: int = 8
    row: int = 8
    pane: int = 16


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
            surface_alt="#f7f9fc",
            surface_elevated="#eef2f7",
            border="#dfe5ee",
            border_strong="#c9d4e2",
            text="#1d1d1f",
            text_muted="#68707c",
            text_soft="#8e8e93",
            accent="#007aff",
            accent_hover="#0066d6",
            accent_text="#ffffff",
            selection="#e7f1ff",
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
            surface="#202125",
            surface_alt="#292b31",
            surface_elevated="#343741",
            border="#3d414c",
            border_strong="#545a68",
            text="#f5f5f7",
            text_muted="#b4bac5",
            text_soft="#8e8e93",
            accent="#0a84ff",
            accent_hover="#36a3ff",
            accent_text="#ffffff",
            selection="#183b64",
            input="#191a1e",
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
    if palette.name == "day":
        app_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f9fbff, stop:0.42 #f5f5f7, stop:1 #eef7f3)"
        pane_fill = "rgba(255,255,255,0.64)"
        pane_alt_fill = "rgba(255,255,255,0.82)"
        toolbar_fill = "rgba(255,255,255,0.72)"
        button_fill = "rgba(255,255,255,0.70)"
        field_fill = "rgba(255,255,255,0.88)"
        accent_fill = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0a84ff, stop:1 #32d3ff)"
        selected_fill = "rgba(10,132,255,0.11)"
        status_bg = "rgba(10,132,255,0.10)"
        error_bg = "rgba(255,69,58,0.10)"
        success_bg = "rgba(52,199,89,0.12)"
        separator = "rgba(120,130,150,0.22)"
        tooltip_bg = "rgba(28,28,30,0.92)"
        tooltip_text = "#ffffff"
    else:
        app_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #111318, stop:0.48 #1c1d24, stop:1 #12263a)"
        pane_fill = "rgba(28,28,30,0.82)"
        pane_alt_fill = "rgba(38,40,48,0.88)"
        toolbar_fill = "rgba(28,28,30,0.74)"
        button_fill = "rgba(62,66,78,0.58)"
        field_fill = "rgba(22,23,28,0.88)"
        accent_fill = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0a84ff, stop:1 #54e4ff)"
        selected_fill = "rgba(10,132,255,0.16)"
        status_bg = "rgba(10,132,255,0.16)"
        error_bg = "rgba(255,69,58,0.14)"
        success_bg = "rgba(48,209,88,0.14)"
        separator = "rgba(210,220,235,0.16)"
        tooltip_bg = "rgba(245,245,247,0.94)"
        tooltip_text = "#1d1d1f"
    return f"""
QWidget {{
    font-family: "Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "PingFang SC", "DengXian", sans-serif;
    font-size: {t.body_size}px;
    color: {c.text};
}}
QMainWindow, #CentralShell {{
    background: {app_bg};
}}
#CentralShell {{
    border: 0;
}}
#SessionSidebar, #ChatPane, #TraceInspector {{
    background: {pane_fill};
    border: 1px solid {separator};
    border-radius: {r.pane}px;
}}
#Brand {{
    color: {c.text};
    font-size: {t.title_size + 1}px;
    font-weight: 700;
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
    background: {accent_fill};
    border: 1px solid rgba(255,255,255,0.34);
    border-radius: {r.card}px;
    color: {c.accent_text};
    font-weight: 700;
    padding: {s.control_pad_y + 1}px {s.control_pad_x}px;
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
    border: 1px solid {separator};
    border-radius: {r.card}px;
    color: {c.text};
    padding: {s.control_pad_y}px {s.control_pad_x}px;
}}
QPushButton:hover {{
    border-color: {c.border_strong};
    background: {pane_alt_fill};
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
#SessionScopeTabs {{
    background: {button_fill};
    border: 1px solid {separator};
    border-radius: {r.card}px;
}}
#SessionScopeTabs::tab {{
    background: transparent;
    border: 1px solid transparent;
    color: {c.text_muted};
    padding: 7px 10px;
    margin: 2px;
    border-radius: {r.chip}px;
}}
#SessionScopeTabs::tab:selected {{
    background: {pane_alt_fill};
    border-color: {separator};
    color: {c.text};
}}
#SessionRow {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r.row}px;
}}
#SessionRow:hover {{
    background: {pane_alt_fill};
    border-color: {separator};
}}
#SessionRow[selected="true"] {{
    background: {selected_fill};
    border: 1px solid rgba(10,132,255,0.26);
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
    color: {c.accent};
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
    background: {button_fill};
    border: 1px solid {separator};
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
    background: {pane_alt_fill};
    color: {c.text};
    border: 1px solid {separator};
}}
#ProjectPickerButton {{
    background: {button_fill};
    border: 1px solid {separator};
    border-radius: {r.card}px;
    color: {c.text};
    padding: {s.control_pad_y}px {s.control_pad_x}px;
    text-align: left;
}}
#ProjectPickerButton:hover {{
    background: {pane_alt_fill};
    border-color: {c.border_strong};
}}
#SettingsButton {{
    padding: {s.control_pad_y}px {s.control_pad_x}px;
}}
#ChatHeader {{
    background: {toolbar_fill};
    border-bottom: 1px solid {separator};
    border-top-left-radius: {r.pane}px;
    border-top-right-radius: {r.pane}px;
}}
#ChatHeaderTitle {{
    color: {c.text};
    font-size: 16px;
    font-weight: 650;
}}
#ChatRunStatus, #InspectorStatusIndicator {{
    background: {button_fill};
    border: 1px solid {separator};
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
    background: {accent_fill};
    color: {c.accent_text};
    border: 1px solid rgba(255,255,255,0.28);
}}
#AssistantBubble {{
    background: {pane_alt_fill};
    color: {c.text};
    border: 1px solid {separator};
}}
#AssistantBubble[format="json"] {{
    background: {toolbar_fill};
    color: {c.text};
    border: 1px solid {c.border_strong};
    font-family: "Cascadia Mono", "Consolas", "Courier New", monospace;
    font-size: {max(t.message_size - 1, 12)}px;
}}
#UserAvatar, #AssistantAvatar {{
    background: {button_fill};
    border: 1px solid {separator};
    border-radius: {r.card}px;
    color: {c.text_muted};
    font-weight: 650;
}}
#ToolStatusRow {{
    background: {toolbar_fill};
    border: 1px solid {separator};
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
    background: {pane_alt_fill};
    border: 1px solid {separator};
    border-radius: {r.chip}px;
    color: {c.text_soft};
    font-size: 12px;
    font-weight: 650;
    padding: 0;
}}
#ToolStatusToggle {{
    background: transparent;
    border: 1px solid {separator};
    border-radius: {r.chip}px;
    color: {c.text_soft};
    padding: 0;
}}
#ToolStatusToggle:hover {{
    background: {pane_alt_fill};
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
    background: {pane_alt_fill};
    border: 1px solid {separator};
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
    border-top: 1px solid {separator};
    border-bottom-left-radius: {r.pane}px;
    border-bottom-right-radius: {r.pane}px;
}}
#MessageInput, QLineEdit, QPlainTextEdit, QSpinBox {{
    background: {field_fill};
    border: 1px solid {separator};
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
    border: 1px solid {separator};
    background: {pane_alt_fill};
    border-radius: {r.card}px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    border: 1px solid transparent;
    color: {c.text_muted};
    padding: 7px 13px;
    margin: 0;
    border-radius: {r.chip}px;
}}
QTabBar::tab:selected {{
    color: {c.text};
    background: {pane_alt_fill};
    border: 1px solid {separator};
}}
QTreeWidget, QListWidget {{
    background: transparent;
    border: 0;
    color: {c.text};
    alternate-background-color: {c.surface_alt};
}}
QHeaderView::section {{
    background: {pane_alt_fill};
    border: 0;
    border-bottom: 1px solid {c.border};
    color: {c.text_muted};
    font-weight: 650;
    padding: 7px;
}}
QTreeWidget::item, QListWidget::item {{
    padding: 7px;
    border-bottom: 1px solid {separator};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
    border: 0;
}}
QScrollBar::handle:vertical {{
    background: {c.border_strong};
    border-radius: 4px;
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
    background: transparent;
    height: 10px;
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
    background: {pane_alt_fill};
    border: 1px solid {separator};
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
    background: {app_bg};
}}
QToolTip {{
    background: {tooltip_bg};
    color: {tooltip_text};
    border: 1px solid {separator};
    border-radius: {r.chip}px;
    padding: 6px 8px;
}}
"""
