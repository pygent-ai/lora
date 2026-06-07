from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QFontDatabase

THEMES = ("day", "night")


@dataclass(frozen=True, slots=True)
class ThemeColors:
    bg: str
    bg_secondary: str
    surface: str
    surface_alt: str
    surface_elevated: str
    glass: str
    glass_strong: str
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
    title_size: int = 24
    section_size: int = 11


@dataclass(frozen=True, slots=True)
class ThemeSpacing:
    pane_margin: int = 16
    gutter: int = 14
    pane_padding: int = 16
    control_pad_y: int = 9
    control_pad_x: int = 12


@dataclass(frozen=True, slots=True)
class ThemeRadius:
    chip: int = 7
    card: int = 10
    row: int = 11
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
            bg="#f7f5f1",
            bg_secondary="#eef4fb",
            surface="#ffffff",
            surface_alt="#f8f9fb",
            surface_elevated="#edf2f8",
            glass="rgba(255,255,255,0.68)",
            glass_strong="rgba(255,255,255,0.84)",
            border="rgba(107,124,147,0.18)",
            border_strong="rgba(107,124,147,0.30)",
            text="#1f2329",
            text_muted="#66707f",
            text_soft="#8d96a3",
            accent="#147efb",
            accent_hover="#0d6fe6",
            accent_text="#ffffff",
            selection="#e8f2ff",
            input="#fbfdff",
        ),
        typography=ThemeTypography(),
        spacing=ThemeSpacing(),
        radius=ThemeRadius(),
        state_colors=ThemeStateColors(
            success="#3eb37b",
            warning="#e3a449",
            error="#e35d58",
            running="#4e9ef7",
        ),
    ),
    "night": ThemePalette(
        name="night",
        colors=ThemeColors(
            bg="#171a20",
            bg_secondary="#202833",
            surface="#20242b",
            surface_alt="#252b35",
            surface_elevated="#2d3541",
            glass="rgba(33,37,45,0.82)",
            glass_strong="rgba(37,42,52,0.92)",
            border="rgba(205,214,228,0.12)",
            border_strong="rgba(205,214,228,0.22)",
            text="#f5f7fb",
            text_muted="#b2bac7",
            text_soft="#88919f",
            accent="#4a98ff",
            accent_hover="#6eacff",
            accent_text="#ffffff",
            selection="#213753",
            input="#1a1f26",
        ),
        typography=ThemeTypography(),
        spacing=ThemeSpacing(),
        radius=ThemeRadius(),
        state_colors=ThemeStateColors(
            success="#43bf83",
            warning="#e7a754",
            error="#ea6f6a",
            running="#67b0ff",
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
        fonts_dir / "consola.ttf",
        fonts_dir / "consolab.ttf",
        fonts_dir / "cour.ttf",
        fonts_dir / "DejaVuSansMono.ttf",
        fonts_dir / "LiberationMono-Regular.ttf",
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
        app_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #fbf8f3, stop:0.38 #f5f4f2, stop:0.72 #eef3f9, stop:1 #eaf4ee)"
        accent_fill = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0f7aff, stop:1 #4ea8ff)"
        pill_fill = "rgba(255,255,255,0.72)"
        success_bg = "rgba(62,179,123,0.14)"
        running_bg = "rgba(20,126,251,0.13)"
        error_bg = "rgba(227,93,88,0.13)"
        hover_fill = "rgba(255,255,255,0.88)"
        list_hover = "rgba(255,255,255,0.70)"
        shadow_color = "rgba(82,97,115,0.11)"
        scrollbar_track = "rgba(130,144,161,0.08)"
        scrollbar_thumb = "rgba(117,130,147,0.32)"
    else:
        app_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #14181e, stop:0.42 #1a1f27, stop:0.72 #1e2833, stop:1 #1a2330)"
        accent_fill = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3e90ff, stop:1 #68bcff)"
        pill_fill = "rgba(44,49,59,0.84)"
        success_bg = "rgba(67,191,131,0.16)"
        running_bg = "rgba(74,152,255,0.18)"
        error_bg = "rgba(234,111,106,0.16)"
        hover_fill = "rgba(49,56,67,0.95)"
        list_hover = "rgba(45,51,61,0.82)"
        shadow_color = "rgba(0,0,0,0.24)"
        scrollbar_track = "rgba(205,214,228,0.04)"
        scrollbar_thumb = "rgba(205,214,228,0.20)"
    selected_fill = c.selection
    return f"""
QWidget {{
    font-family: "Microsoft YaHei UI", "Segoe UI Variable Text", "SF Pro Text", "PingFang SC", "Segoe UI", sans-serif;
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
    background: {c.glass};
    border: 1px solid {c.border};
    border-radius: {r.pane}px;
}}
#SessionSidebar {{
    background: {c.glass_strong};
}}
#ChatPane {{
    background: {c.glass};
    border: 1px solid {c.border};
    border-radius: {r.pane}px;
}}
#ChatMainCard {{
    background: transparent;
    border: 0;
    border-radius: 0;
}}
#ChatHeaderShell, #ChatScrollShell {{
    background: transparent;
}}
#Brand {{
    color: {c.text};
    font-size: {t.title_size}px;
    font-weight: 700;
    letter-spacing: 0.4px;
}}
#SidebarMeta, #SessionRowMeta, #InspectorMeta, #ChatHeaderMeta, #InspectorConfigKey {{
    color: {c.text_muted};
    font-size: {t.meta_size}px;
}}
#SectionLabel {{
    color: {c.text_soft};
    font-size: {t.section_size}px;
    font-weight: 700;
    text-transform: uppercase;
    padding: 6px 2px 2px 2px;
}}
#AccentButton, #PrimaryButton {{
    background: {accent_fill};
    border: 1px solid rgba(255,255,255,0.28);
    border-radius: {r.card}px;
    color: {c.accent_text};
    font-weight: 700;
    padding: {s.control_pad_y + 1}px {s.control_pad_x + 2}px;
}}
#AccentButton:hover, #PrimaryButton:hover {{
    background: {c.accent_hover};
}}
#AccentButton:pressed, #PrimaryButton:pressed {{
    padding-top: {s.control_pad_y + 2}px;
}}
#PrimaryButton:disabled, #AccentButton:disabled {{
    background: {c.surface_elevated};
    color: {c.text_muted};
    border-color: {c.border};
}}
QPushButton {{
    background: {pill_fill};
    border: 1px solid {c.border};
    border-radius: {r.card - 2}px;
    color: {c.text};
    padding: {s.control_pad_y}px {s.control_pad_x}px;
}}
QPushButton:hover {{
    background: {hover_fill};
    border-color: {c.border_strong};
}}
QPushButton:pressed {{
    background: {c.surface_elevated};
}}
QPushButton:checked {{
    background: {selected_fill};
    border-color: rgba(20,126,251,0.26);
    color: {c.text};
}}
QLineEdit, QPlainTextEdit, QTextEdit, QListWidget, QTreeWidget, QTabWidget::pane {{
    background: transparent;
}}
#ChatHeader, #InspectorHeader {{
    background: transparent;
    border-bottom: 1px solid {c.border};
}}
#ChatHeaderTitle {{
    font-size: {t.title_size - 4}px;
    font-weight: 700;
    color: {c.text};
}}
#ChatHeaderMeta {{
    padding-top: 2px;
}}
#ChatRunStatus, #InspectorStatusIndicator, #ToolStatusIcon {{
    background: {pill_fill};
    border: 1px solid {c.border};
    border-radius: {r.chip}px;
    color: {c.text_muted};
    font-size: {t.meta_size}px;
    font-weight: 700;
    padding: 4px 10px;
}}
#ChatRunStatus[status="running"], #InspectorStatusIndicator[status="running"], #SessionRowStatus[status="running"], #ToolStatusIcon[status="running"] {{
    background: {running_bg};
    border-color: rgba(20,126,251,0.18);
    color: {state.running};
}}
#ChatRunStatus[status="success"], #InspectorStatusIndicator[status="success"], #SessionRowStatus[status="success"], #ToolStatusIcon[status="success"] {{
    background: {success_bg};
    border-color: rgba(62,179,123,0.18);
    color: {state.success};
}}
#ChatRunStatus[status="error"], #InspectorStatusIndicator[status="error"], #SessionRowStatus[status="error"], #ToolStatusIcon[status="error"] {{
    background: {error_bg};
    border-color: rgba(227,93,88,0.18);
    color: {state.error};
}}
#SessionScopeTabs, #InspectorTabs, QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r.chip}px;
    color: {c.text_muted};
    padding: 8px 14px;
    margin-right: 4px;
    font-weight: 600;
}}
QTabBar::tab:selected {{
    background: {pill_fill};
    border-color: {c.border};
    color: {c.text};
}}
QTabBar::tab:hover {{
    background: {list_hover};
}}
#SessionList, #TraceInspector QListWidget, #TraceInspector QTreeWidget, #ChatScroll {{
    border: 0;
    outline: none;
}}
#SessionList::item {{
    padding: 0;
    margin: 0;
}}
#SessionRow {{
    background: transparent;
    border: 1px solid {c.border};
    border-radius: 9px;
}}
#SessionRow[selected="true"] {{
    background: {selected_fill};
    border-color: rgba(20,126,251,0.22);
}}
#SessionRow:hover {{
    background: {list_hover};
    border-color: {c.border_strong};
}}
#SessionRowRail {{
    background: transparent;
    border-radius: 2px;
}}
#SessionRowRail[selected="true"] {{
    background: transparent;
}}
#SessionRowIcon {{
    background: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: 5px;
}}
#SessionRowTitle {{
    font-size: {t.body_size}px;
    font-weight: 600;
    color: {c.text_muted};
}}
#SessionRowMeta, #SessionRowStatus {{
    color: {c.text_soft};
    font-size: {t.meta_size + 1}px;
}}
#SessionRowStatus {{
    background: transparent;
    border: 0;
    padding: 0;
    font-weight: 500;
}}
#SessionRowStatus[status="running"] {{
    color: {state.running};
}}
#SessionRowStatus[status="success"], #SessionRowStatus[status="ready"] {{
    color: {c.text_soft};
}}
#SessionRowStatus[status="error"] {{
    color: {state.error};
}}
#SessionRowMenuButton {{
    background: transparent;
    border: 0;
    color: {c.text_soft};
    padding: 0 2px 2px 2px;
    font-size: 18px;
    font-weight: 600;
    min-width: 14px;
}}
#SessionRowMenuButton:hover {{
    background: transparent;
    color: {c.text_muted};
}}
#SessionRowMenuButton::menu-indicator {{
    image: none;
}}
#ThemeSegment, #SidebarInfoCard, #SidebarToolsCard, #InspectorStatusCard {{
    background: {pill_fill};
    border: 1px solid {c.border};
    border-radius: {r.card}px;
}}
#SidebarInfoCard, #SidebarToolsCard, #InspectorStatusCard {{
    background: transparent;
    border-color: transparent;
}}
#ThemeDayButton, #ThemeNightButton {{
    border: 0;
    border-radius: {r.card - 4}px;
    background: transparent;
    padding: {s.control_pad_y}px {s.control_pad_x}px;
}}
#ThemeDayButton:checked, #ThemeNightButton:checked {{
    background: {c.surface};
    border: 1px solid rgba(20,126,251,0.12);
}}
#ProjectPickerButton, #SettingsButton {{
    text-align: left;
    padding-left: 14px;
}}
#SidebarInfoTitle, #InspectorStatus, #ThinkingStatusTitle {{
    font-size: {t.body_size}px;
    font-weight: 700;
    color: {c.text};
}}
#InspectorStatus {{
    font-size: {t.body_size + 1}px;
}}
#SidebarInfoValue, #InspectorConfigValue {{
    color: {c.text_muted};
    font-size: {t.meta_size + 1}px;
}}
#ChatScrollBody {{
    background: transparent;
}}
#UserBubble {{
    font-size: {t.message_size}px;
    line-height: 1.55;
}}
#UserBubble {{
    background: {pill_fill};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: 18px;
    padding: 3px 5px;
}}
#DocumentBlockList {{
    background: transparent;
    border: 0;
}}
#ChatHeadingBlock, #ChatParagraphText, #ChatQuoteBlock QLabel, #ChatListItem QLabel {{
    color: {c.text};
    font-size: {t.message_size}px;
}}
#ChatHeadingBlock {{
    color: {c.text};
    font-size: {t.message_size + 7}px;
    font-weight: 700;
    padding: 2px 0 4px 0;
}}
#ChatParagraphText {{
    color: {c.text};
    font-size: {t.message_size}px;
}}
#ChatInlineCodeSpan {{
    color: {"#1e5f9e" if palette.name == "day" else "#d7ebff"};
    background: {"#e8f2ff" if palette.name == "day" else "#24364a"};
    border: 1px solid {"#cfe0f5" if palette.name == "day" else "#34506c"};
    border-radius: {r.chip}px;
    font-family: "Consolas", "Courier New", monospace;
    padding: 2px 6px;
}}
#ChatQuoteBlock {{
    background: {"rgba(233,239,248,0.82)" if palette.name == "day" else "rgba(40,49,61,0.92)"};
    border: 1px solid {"#dce4ef" if palette.name == "day" else "#364454"};
    border-radius: {r.card + 2}px;
}}
#ChatListBlock {{
    background: transparent;
    border: 0;
}}
#ChatListItem {{
    background: transparent;
    border: 0;
}}
#ChatCodeBlock {{
    background: {"#1c2430" if palette.name == "day" else "#11161d"};
    border: 1px solid {"#243244" if palette.name == "day" else "#2b3b4d"};
    border-radius: {r.pane - 2}px;
}}
#ChatCodeBlockLanguage {{
    color: {"#9fc3ea" if palette.name == "day" else "#a9cfff"};
    font-family: "Consolas", "Courier New", monospace;
    font-size: {t.meta_size}px;
    font-weight: 700;
    padding: 0 0 2px 0;
}}
#ChatCodeBlockText {{
    color: {"#eaf2ff" if palette.name == "day" else "#edf4ff"};
    font-family: "Consolas", "Courier New", monospace;
    font-size: {t.message_size - 1}px;
}}
#Composer {{
    background: {c.surface};
    border: 1px solid {c.border};
    border-radius: {r.pane - 2}px;
}}
#MessageInput {{
    background: transparent;
    border: 0;
    border-radius: 0;
    padding: 0;
    selection-background-color: {c.selection};
}}
#MessageInput:focus {{
    background: transparent;
}}
#ChatScroll {{
    background: transparent;
}}
#ToolStatusRow, #ToolCallCard, #ToolResultCard, #InspectorConfigRow, #ToolExpandedCard, #ToolStatusSummary {{
    background: transparent;
    border: 0;
    border-radius: 0;
}}
#ToolStatusRow {{
    background: transparent;
    border: 0;
}}
#ToolStatusSummary {{
    background: transparent;
    border: 0;
    border-radius: 0;
}}
#ToolStatusSummary[status="running"] {{
    background: transparent;
    border: 0;
}}
#ToolStatusSummary[status="running"][blinkPhase="alt"] {{
    background: transparent;
    border: 0;
}}
#ToolStatusSummary[status="success"] {{
    background: transparent;
    border: 0;
}}
#ToolStatusSummary[status="error"] {{
    background: transparent;
    border: 0;
}}
#ToolStatusTitle {{
    font-size: {t.meta_size + 1}px;
    font-weight: 600;
    color: {c.text_muted};
    padding: 0;
}}
#ToolStatusTitle[status="running"] {{
    color: {c.text_muted};
}}
#ToolStatusTitle[status="running"][blinkPhase="alt"] {{
    color: {state.running};
}}
#ToolStatusTitle[status="success"] {{
    color: {state.success};
}}
#ToolStatusTitle[status="error"] {{
    color: {state.error};
}}
#ToolStatusToggle {{
    background: transparent;
    border: 0;
    color: {c.text_muted};
    font-size: {t.meta_size + 2}px;
    font-weight: 600;
    padding: 0;
    min-width: 0;
}}
#ToolStatusToggle[status="running"] {{
    color: {c.text_muted};
}}
#ToolStatusToggle[status="running"][blinkPhase="alt"] {{
    color: {state.running};
}}
#ToolStatusToggle[status="error"] {{
    color: {state.error};
}}
#ToolStatusToggle[status="success"] {{
    color: {state.success};
}}
#ToolExpandedPanel {{
    background: transparent;
}}
#ToolTimeline {{
    background: transparent;
}}
#ToolTimelineDot {{
    background: {c.surface};
    border: 2px solid {c.border_strong};
    border-radius: 5px;
}}
#ToolTimelineDot[status="running"] {{
    background: rgba(20,126,251,0.14);
    border-color: {state.running};
}}
#ToolTimelineDot[status="success"] {{
    background: rgba(62,179,123,0.16);
    border-color: {state.success};
}}
#ToolTimelineDot[status="error"] {{
    background: rgba(227,93,88,0.16);
    border-color: {state.error};
}}
#ToolTimelineLine {{
    background: transparent;
    border: 0;
}}
#ToolExpandedCard {{
    background: transparent;
    border: 0;
}}
#ToolCallRow {{
    background: transparent;
    border: 0;
    border-bottom: 0;
}}
#ToolCallLine {{
    font-size: {t.body_size}px;
    font-weight: 600;
    color: {c.text_muted};
}}
#ToolCallStatus {{
    font-size: {t.meta_size}px;
    font-weight: 600;
}}
#ToolCallStatus[status="running"] {{
    color: {c.text_muted};
}}
#ToolCallStatus[status="success"] {{
    color: {c.text_muted};
}}
#ToolCallStatus[status="error"] {{
    color: {c.text_muted};
}}
#ToolCallStartTime {{
    color: {c.text_muted};
    font-size: {t.meta_size}px;
}}
#ToolCallDuration {{
    color: {c.text_muted};
    font-size: {t.meta_size}px;
    font-weight: 600;
    background: transparent;
    border: 0;
    padding: 0;
}}
#ToolCallTitle, #ToolResultTitle {{
    font-weight: 700;
    color: {c.text};
}}
#ToolCallMeta, #ToolResultMeta {{
    color: {c.text_muted};
    font-size: {t.meta_size}px;
}}
#TraceInspector QHeaderView::section {{
    background: transparent;
    border: 0;
    border-bottom: 1px solid {c.border};
    color: {c.text_soft};
    padding: 8px 6px;
    font-size: {t.meta_size}px;
    font-weight: 700;
}}
#TraceInspector QTreeWidget::item, #TraceInspector QListWidget::item {{
    padding: 10px 8px;
    border-bottom: 1px solid {c.border};
}}
#TraceInspector QTreeWidget::item:selected, #TraceInspector QListWidget::item:selected {{
    background: {selected_fill};
    color: {c.text};
}}
QScrollBar:vertical {{
    background: {scrollbar_track};
    width: 10px;
    margin: 6px 2px 6px 2px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background: {scrollbar_thumb};
    min-height: 28px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c.text_soft};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar:horizontal, QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal, QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    border: 0;
    background: transparent;
    height: 0;
    width: 0;
}}
QToolTip {{
    background: {c.surface};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: {r.chip}px;
    padding: 6px 8px;
}}
"""
