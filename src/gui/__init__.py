"""Compatibility package for the archived PySide GUI."""

from __future__ import annotations

from pathlib import Path

_LEGACY_GUI_PACKAGE = Path(__file__).resolve().parents[2] / "legacy" / "pyside_gui" / "src" / "gui"

if _LEGACY_GUI_PACKAGE.is_dir():
    __path__.append(str(_LEGACY_GUI_PACKAGE))
