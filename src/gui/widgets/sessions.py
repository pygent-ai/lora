from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from gui.icons import icon
from gui.session_model import ChatSessionRecord


class SessionSidebar(QWidget):
    new_chat_requested = Signal()
    session_selected = Signal(str)
    session_delete_requested = Signal(str)
    settings_requested = Signal()
    theme_toggle_requested = Signal()
    theme_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SessionSidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._rows: dict[str, _SessionRow] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 20, 18, 18)
        layout.setSpacing(12)

        title = QLabel("Lora")
        title.setObjectName("Brand")
        self.workspace = QLabel("")
        self.workspace.setObjectName("SidebarMeta")
        self.new_button = QPushButton("New Chat")
        self.new_button.setObjectName("AccentButton")
        self.new_button.setIcon(icon("new-chat"))
        self.new_button.setToolTip("Create a new chat session")
        self.new_button.clicked.connect(self.new_chat_requested.emit)

        recent = QLabel("Recent Sessions")
        recent.setObjectName("SectionLabel")
        self.sessions = QListWidget()
        self.sessions.setObjectName("SessionList")
        self.sessions.setFocusPolicy(Qt.NoFocus)
        self.sessions.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sessions.itemClicked.connect(self._emit_selected)

        self.agent = QLabel("")
        self.agent.setObjectName("SidebarMeta")
        self.agent.setWordWrap(True)
        theme_row = QWidget()
        theme_row.setObjectName("ThemeSegment")
        theme_row.setAttribute(Qt.WA_StyledBackground, True)
        theme_layout = QHBoxLayout(theme_row)
        theme_layout.setContentsMargins(0, 0, 0, 0)
        theme_layout.setSpacing(0)
        self.day_button = QPushButton("Day")
        self.day_button.setObjectName("ThemeDayButton")
        self.day_button.setIcon(icon("day"))
        self.day_button.setToolTip("Use day theme")
        self.night_button = QPushButton("Night")
        self.night_button.setObjectName("ThemeNightButton")
        self.night_button.setIcon(icon("night"))
        self.night_button.setToolTip("Use night theme")
        self.day_button.setCheckable(True)
        self.night_button.setCheckable(True)
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_group.addButton(self.day_button)
        self.theme_group.addButton(self.night_button)
        self.day_button.clicked.connect(lambda: self.theme_selected.emit("day"))
        self.night_button.clicked.connect(lambda: self.theme_selected.emit("night"))
        theme_layout.addWidget(self.day_button)
        theme_layout.addWidget(self.night_button)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("SettingsButton")
        self.settings_button.setIcon(icon("settings"))
        self.settings_button.setToolTip("Open settings")
        self.settings_button.clicked.connect(self.settings_requested.emit)

        layout.addWidget(title)
        layout.addWidget(self.workspace)
        layout.addWidget(self.new_button)
        layout.addWidget(recent)
        layout.addWidget(self.sessions, 1)
        layout.addWidget(self.agent)
        layout.addWidget(theme_row)
        layout.addWidget(self.settings_button)

    def set_workspace(self, workspace: str) -> None:
        self.workspace.setText(workspace)

    def set_agent_status(self, alias: str, model_name: str | None) -> None:
        self.agent.setText(f"Agent: {alias}\nModel: {model_name or 'default'}")

    def set_sessions(self, records: list[ChatSessionRecord]) -> None:
        self.sessions.clear()
        self._rows.clear()
        for record in records:
            item = QListWidgetItem()
            item.setData(256, record.session_id)
            item.setToolTip(record.session_id)
            row = _SessionRow(record, self.session_delete_requested.emit)
            item.setSizeHint(QSize(row.sizeHint().width(), 58))
            self.sessions.addItem(item)
            self.sessions.setItemWidget(item, row)
            self._rows[record.session_id] = row

    def select_session(self, session_id: str) -> None:
        for index in range(self.sessions.count()):
            item = self.sessions.item(index)
            if item.data(256) == session_id:
                self.sessions.setCurrentItem(item)
            row = self._rows.get(str(item.data(256)))
            if row is not None:
                row.set_selected(str(item.data(256)) == session_id)

    def selected_session_id(self) -> str | None:
        item = self.sessions.currentItem()
        if item is None:
            return None
        return str(item.data(256))

    def set_theme_name(self, theme: str) -> None:
        self.day_button.setChecked(theme == "day")
        self.night_button.setChecked(theme == "night")

    def _emit_selected(self, item: QListWidgetItem) -> None:
        session_id = str(item.data(256))
        self.select_session(session_id)
        self.session_selected.emit(session_id)


class _SessionRow(QWidget):
    def __init__(self, record: ChatSessionRecord, on_delete):
        super().__init__()
        self.setObjectName("SessionRow")
        self.setProperty("dev_id", record.session_id)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumHeight(56)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.rail = QLabel("")
        self.rail.setObjectName("SessionRowRail")
        self.rail.setProperty("selected", False)
        self.rail.setFixedWidth(4)
        self.rail.setMinimumHeight(34)

        text_stack = QVBoxLayout()
        text_stack.setContentsMargins(0, 0, 0, 0)
        text_stack.setSpacing(2)
        label = QLabel(record.title)
        label.setObjectName("SessionRowTitle")
        label.setProperty("dev_id", record.session_id)
        label.setToolTip(record.session_id)
        label.setMinimumWidth(0)
        label.setMaximumWidth(260)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        metadata = QLabel(_session_metadata(record))
        metadata.setObjectName("SessionRowMeta")
        metadata.setMinimumWidth(0)
        metadata.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text_stack.addWidget(label)
        text_stack.addWidget(metadata)

        delete_button = QPushButton()
        delete_button.setObjectName("SessionRowDeleteButton")
        delete_button.setProperty("dev_id", f"delete:{record.session_id}")
        delete_button.setToolTip("Delete session")
        delete_button.setCursor(Qt.PointingHandCursor)
        delete_button.setIcon(icon("delete") or self.style().standardIcon(QStyle.SP_TrashIcon))
        delete_button.setFixedSize(24, 24)
        delete_button.clicked.connect(lambda: on_delete(record.session_id))

        layout.addWidget(self.rail)
        layout.addLayout(text_stack, 1)
        layout.addWidget(delete_button)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.rail.setProperty("selected", selected)
        for widget in (self, self.rail):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()


def _session_metadata(record: ChatSessionRecord) -> str:
    timestamp = record.updated_at or record.created_at
    if timestamp:
        return str(timestamp)[:19]
    return record.session_id[:12]
