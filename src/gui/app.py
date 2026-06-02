from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from gui.devtools import install_ui_hit_test_filter
from gui.main_window import MainWindow
from gui.theme import register_ui_fonts, theme_stylesheet


def run_app(
    *,
    workspace_root: str | None = None,
    config_path: str | None = None,
    agent_alias: str | None = None,
    model: str | None = None,
    max_steps: int | None = None,
    smoke: bool = False,
) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Lora")
    register_ui_fonts()
    app.setStyleSheet(theme_stylesheet("day"))
    install_ui_hit_test_filter(app)
    window = MainWindow(
        workspace_root=workspace_root,
        config_path=config_path,
        agent_alias=agent_alias,
        model=model,
        max_steps=max_steps,
    )
    window.resize(1320, 820)
    if smoke:
        window.deleteLater()
        return 0
    window.show()
    return int(app.exec())
