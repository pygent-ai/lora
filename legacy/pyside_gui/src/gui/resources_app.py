from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from gui.resources_window import ResourcesWindow
from gui.theme import apply_ui_font, register_ui_fonts, theme_stylesheet


def run_resources_app(*, theme: str = "day", smoke: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Lora Resources")
    register_ui_fonts()
    apply_ui_font(app)
    app.setStyleSheet(theme_stylesheet(theme))
    window = ResourcesWindow()
    window.resize(1220, 760)
    if smoke:
        window.deleteLater()
        return 0
    window.show()
    return int(app.exec())


def main() -> None:
    raise SystemExit(run_resources_app())
