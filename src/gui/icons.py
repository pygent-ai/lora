from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QIcon

_ICON_DIR = Path(__file__).resolve().parent / "assets" / "icons"


@lru_cache(maxsize=32)
def icon(name: str) -> QIcon:
    path = _ICON_DIR / f"{name}.svg"
    return QIcon(str(path)) if path.exists() else QIcon()
