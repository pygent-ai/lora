from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QDrag, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.icons import icon
from gui.session_model import ChatSessionRecord, SessionGroupRecord

_SCOPE_MIME = "application/x-lora-scope-id"


class _GroupsContainer(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

    def sizeHint(self):
        layout = self.layout()
        if layout is None:
            return super().sizeHint()
        return layout.minimumSize()

    def minimumSizeHint(self):
        return self.sizeHint()


class _SessionGroupHeader(QWidget):
    toggle_requested = Signal()

    def __init__(
        self,
        label: str,
        count: int,
        *,
        active: bool,
        expanded: bool,
        scope_id: str,
        on_remove: Callable[[str], None] | None = None,
        draggable: bool = False,
    ):
        super().__init__()
        self.setObjectName("SessionGroupHeader")
        self.setProperty("active", active)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._scope_id = scope_id
        self._draggable = draggable
        self._drag_start: QPoint | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self.arrow = QLabel("▾" if expanded else "▸")
        self.arrow.setObjectName("SessionGroupArrow")
        self.arrow.setFixedWidth(14)
        self.arrow.setAlignment(Qt.AlignCenter)
        self.arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        title = QLabel(label)
        title.setObjectName("SessionGroupTitle")
        title.setMinimumWidth(0)
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        title.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        counter = QLabel(str(count))
        counter.setObjectName("SessionGroupCount")
        counter.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        counter.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        layout.addWidget(self.arrow)
        layout.addWidget(title, 1)
        layout.addWidget(counter)
        if on_remove is not None:
            delete_button = QToolButton()
            delete_button.setObjectName("SessionGroupDeleteButton")
            delete_button.setProperty("dev_id", f"remove:{scope_id}")
            delete_button.setIcon(icon("delete"))
            delete_button.setToolTip("Remove project from sidebar")
            delete_button.setAutoRaise(True)
            delete_button.setCursor(Qt.PointingHandCursor)
            delete_button.setFixedSize(22, 22)
            delete_button.clicked.connect(lambda: on_remove(scope_id))
            layout.addWidget(delete_button)

    def set_expanded(self, expanded: bool) -> None:
        self.arrow.setText("▾" if expanded else "▸")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._child_button_at(event.position().toPoint()) is None:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._draggable
            and self._drag_start is not None
            and (event.position().toPoint() - self._drag_start).manhattanLength() >= 12
        ):
            self._drag_start = None
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(_SCOPE_MIME, self._scope_id.encode("utf-8"))
            drag.setMimeData(mime)
            drag.exec(Qt.MoveAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self._drag_start is not None
            and (event.position().toPoint() - self._drag_start).manhattanLength() < 12
        ):
            self.toggle_requested.emit()
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def _child_button_at(self, point) -> QToolButton | None:
        child = self.childAt(point)
        while child is not None:
            if isinstance(child, QToolButton):
                return child
            child = child.parentWidget()
        return None


class _SessionGroupSection(QWidget):
    collapsed_changed = Signal(str, bool)
    session_selected = Signal(str, str)
    session_delete_requested = Signal(str, str)
    reorder_requested = Signal(str, str)

    def __init__(
        self,
        group: SessionGroupRecord,
        *,
        active_scope_id: str,
        on_remove: Callable[[str], None] | None,
    ):
        super().__init__()
        self.scope_id = group.scope.scope_id
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        self._rows: dict[str, _SessionRow] = {}
        self._selected_session_id: str | None = None
        self._collapsed = group.collapsed

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.header = _SessionGroupHeader(
            group.scope.label,
            len(group.records),
            active=group.scope.scope_id == active_scope_id,
            expanded=not self._collapsed,
            scope_id=group.scope.scope_id,
            on_remove=on_remove,
            draggable=True,
        )
        self.header.setToolTip(group.scope.tooltip)
        self.header.toggle_requested.connect(self._toggle_collapsed)

        self.sessions_body = QWidget()
        self.sessions_body.setObjectName("SessionList")
        self.sessions_body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._sessions_layout = QVBoxLayout(self.sessions_body)
        self._sessions_layout.setContentsMargins(0, 0, 0, 0)
        self._sessions_layout.setSpacing(6)

        layout.addWidget(self.header)
        layout.addWidget(self.sessions_body)

        self.set_records(group.records)
        self.set_collapsed(self._collapsed)

    def set_records(self, records: list[ChatSessionRecord]) -> None:
        while self._sessions_layout.count():
            item = self._sessions_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows.clear()
        self._selected_session_id = None

        for record in records:
            row = _SessionRow(
                record,
                self._emit_delete_requested,
                on_select=lambda session_id=record.session_id: self._on_row_selected(session_id),
            )
            self._sessions_layout.addWidget(row)
            self._rows[record.session_id] = row

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.sessions_body.setVisible(not collapsed)
        self.header.set_expanded(not collapsed)

    def session_count(self) -> int:
        return len(self._rows)

    def session_row(self, index: int) -> _SessionRow | None:
        item = self._sessions_layout.itemAt(index)
        widget = item.widget() if item is not None else None
        return widget if isinstance(widget, _SessionRow) else None

    def selected_session_id(self) -> str | None:
        return self._selected_session_id

    def select_session(self, session_id: str) -> None:
        self.set_collapsed(False)
        self._selected_session_id = session_id
        for sid, row in self._rows.items():
            row.set_selected(sid == session_id)

    def clear_selection(self) -> None:
        self._selected_session_id = None
        for row in self._rows.values():
            row.set_selected(False)

    def _toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)
        self.collapsed_changed.emit(self.scope_id, self._collapsed)

    def _on_row_selected(self, session_id: str) -> None:
        self.select_session(session_id)
        self.session_selected.emit(self.scope_id, session_id)

    def _emit_delete_requested(self, session_id: str) -> None:
        self.session_delete_requested.emit(self.scope_id, session_id)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(_SCOPE_MIME):
            dragged = event.mimeData().data(_SCOPE_MIME).data().decode("utf-8")
            if dragged != self.scope_id:
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(_SCOPE_MIME):
            dragged = event.mimeData().data(_SCOPE_MIME).data().decode("utf-8")
            if dragged != self.scope_id:
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(_SCOPE_MIME):
            event.ignore()
            return
        dragged = event.mimeData().data(_SCOPE_MIME).data().decode("utf-8")
        if dragged == self.scope_id:
            event.ignore()
            return
        self.reorder_requested.emit(dragged, self.scope_id)
        event.acceptProposedAction()


class SessionSidebar(QWidget):
    new_chat_requested = Signal()
    session_selected = Signal(str, str)
    session_delete_requested = Signal(str, str)
    settings_requested = Signal()
    project_select_requested = Signal()
    theme_toggle_requested = Signal()
    theme_selected = Signal(str)
    group_order_changed = Signal(list)
    group_collapsed_changed = Signal(str, bool)
    project_remove_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SessionSidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._group_sections: list[_SessionGroupSection] = []
        self._scroll_content_timer = QTimer(self)
        self._scroll_content_timer.setSingleShot(True)
        self._scroll_content_timer.timeout.connect(self._apply_scroll_content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(10)

        brand_card = QFrame()
        brand_card.setObjectName("SidebarInfoCard")
        brand_layout = QVBoxLayout(brand_card)
        brand_layout.setContentsMargins(14, 14, 14, 14)
        brand_layout.setSpacing(4)
        title = QLabel("Lora")
        title.setObjectName("Brand")
        self.workspace = QLabel("")
        self.workspace.setObjectName("SidebarMeta")
        self.workspace.setWordWrap(True)
        brand_layout.addWidget(title)
        brand_layout.addWidget(self.workspace)

        self.new_button = QPushButton("New Chat")
        self.new_button.setObjectName("AccentButton")
        self.new_button.setIcon(icon("new-chat"))
        self.new_button.setToolTip("Create a new chat session")
        self.new_button.clicked.connect(self.new_chat_requested.emit)

        recent = QLabel("Sessions")
        recent.setObjectName("SectionLabel")
        self.session_tree = QScrollArea()
        self.session_tree.setObjectName("SessionTree")
        self.session_tree.setWidgetResizable(False)
        self.session_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.session_tree.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.session_tree.setFrameShape(QFrame.NoFrame)
        self.session_tree.viewport().installEventFilter(self)
        self._groups_container = _GroupsContainer()
        self._groups_layout = QVBoxLayout(self._groups_container)
        self._groups_layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout.setSpacing(8)
        self.session_tree.setWidget(self._groups_container)

        self.agent = QLabel("")
        self.agent.setObjectName("SidebarInfoValue")
        self.agent.setWordWrap(True)
        self.agent_title = QLabel("Runtime")
        self.agent_title.setObjectName("SidebarInfoTitle")
        info_card = QFrame()
        info_card.setObjectName("SidebarInfoCard")
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(4)
        info_layout.addWidget(self.agent_title)
        info_layout.addWidget(self.agent)

        theme_row = QWidget()
        theme_row.setObjectName("ThemeSegment")
        theme_row.setAttribute(Qt.WA_StyledBackground, True)
        theme_layout = QHBoxLayout(theme_row)
        theme_layout.setContentsMargins(4, 4, 4, 4)
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
        self.project_button = QPushButton("Choose Project")
        self.project_button.setObjectName("ProjectPickerButton")
        self.project_button.setToolTip("Choose a project folder")
        self.project_button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.project_button.clicked.connect(self.project_select_requested.emit)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("SettingsButton")
        self.settings_button.setIcon(icon("settings"))
        self.settings_button.setToolTip("Open settings")
        self.settings_button.clicked.connect(self.settings_requested.emit)

        tools_card = QFrame()
        tools_card.setObjectName("SidebarToolsCard")
        tools_layout = QVBoxLayout(tools_card)
        tools_layout.setContentsMargins(10, 10, 10, 10)
        tools_layout.setSpacing(8)
        tools_layout.addWidget(self.project_button)
        tools_layout.addWidget(theme_row)
        tools_layout.addWidget(self.settings_button)

        layout.addWidget(brand_card)
        layout.addWidget(self.new_button)
        layout.addWidget(recent)
        layout.addWidget(self.session_tree, 1)
        layout.addWidget(info_card)
        layout.addWidget(tools_card)

    def set_workspace(self, workspace: str) -> None:
        metrics = QFontMetrics(self.workspace.font())
        self.workspace.setText(metrics.elidedText(workspace, Qt.ElideMiddle, 250))
        self.workspace.setToolTip(workspace)

    def set_agent_status(self, alias: str, model_name: str | None) -> None:
        self.agent.setText(f"Agent  {alias}\nModel  {model_name or 'default'}")

    def set_session_groups(
        self,
        groups: list[SessionGroupRecord],
        *,
        active_scope_id: str,
        active_session_id: str | None,
    ) -> None:
        while self._groups_layout.count():
            item = self._groups_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._group_sections.clear()

        for group in groups:
            section = _SessionGroupSection(
                group,
                active_scope_id=active_scope_id,
                on_remove=self._emit_project_remove_requested if group.scope.workspace_root is not None else None,
            )
            section.collapsed_changed.connect(self._on_group_collapsed_changed)
            section.session_selected.connect(self.session_selected.emit)
            section.session_delete_requested.connect(self.session_delete_requested.emit)
            section.reorder_requested.connect(self._reorder_groups)
            self._groups_layout.addWidget(section)
            self._group_sections.append(section)

        if active_session_id is not None:
            self.select_session(active_scope_id, active_session_id)
        self._update_scroll_content()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scroll_content()

    def eventFilter(self, watched, event):
        if watched is self.session_tree.viewport() and event.type() == event.Type.Resize:
            self._update_scroll_content()
        return super().eventFilter(watched, event)

    def _update_scroll_content(self) -> None:
        self._scroll_content_timer.start(0)

    def _apply_scroll_content(self) -> None:
        viewport_width = self.session_tree.viewport().width()
        if viewport_width <= 0:
            return
        content_height = self._groups_layout.sizeHint().height()
        target = QSize(viewport_width, max(content_height, 0))
        if self._groups_container.size() == target:
            return
        self._groups_container.setFixedWidth(viewport_width)
        self._groups_container.setFixedHeight(max(content_height, 0))

    def _on_group_collapsed_changed(self, scope_id: str, collapsed: bool) -> None:
        self._update_scroll_content()
        self.group_collapsed_changed.emit(scope_id, collapsed)

    def select_session(self, scope_id: str, session_id: str) -> None:
        for section in self._group_sections:
            if section.scope_id == scope_id:
                section.select_session(session_id)
            else:
                section.clear_selection()

    def selected_session_id(self) -> str | None:
        for section in self._group_sections:
            selected = section.selected_session_id()
            if selected is not None:
                return selected
        return None

    def set_theme_name(self, theme: str) -> None:
        self.day_button.setChecked(theme == "day")
        self.night_button.setChecked(theme == "night")

    def group_order(self) -> list[str]:
        return [section.scope_id for section in self._group_sections]

    def _reorder_groups(self, dragged_scope_id: str, before_scope_id: str) -> None:
        order = self.group_order()
        if dragged_scope_id not in order or before_scope_id not in order:
            return
        order.remove(dragged_scope_id)
        order.insert(order.index(before_scope_id), dragged_scope_id)
        self.apply_group_order(order)
        self.group_order_changed.emit(order)

    def apply_group_order(self, scope_ids: list[str]) -> None:
        section_by_id = {section.scope_id: section for section in self._group_sections}
        ordered_sections = [section_by_id[scope_id] for scope_id in scope_ids if scope_id in section_by_id]
        if len(ordered_sections) != len(self._group_sections):
            return
        while self._groups_layout.count():
            item = self._groups_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        self._group_sections = ordered_sections
        for section in ordered_sections:
            self._groups_layout.addWidget(section)
        self._update_scroll_content()

    def _emit_project_remove_requested(self, scope_id: str) -> None:
        self.project_remove_requested.emit(scope_id)


class _SessionRow(QWidget):
    def __init__(
        self,
        record: ChatSessionRecord,
        on_delete,
        *,
        on_select: Callable[[], None],
    ):
        super().__init__()
        self.setObjectName("SessionRow")
        self.setProperty("dev_id", record.session_id)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._full_title = record.title
        self._metadata_text = _session_metadata(record)
        self._on_select = on_select
        self._menu_button: QToolButton | None = None
        self._title_label: QLabel | None = None
        self._metadata_label: QLabel | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 12, 8)
        layout.setSpacing(10)

        self.rail = QLabel("")
        self.rail.setObjectName("SessionRowRail")
        self.rail.setProperty("selected", False)
        self.rail.setFixedWidth(0)
        self.rail.setMinimumHeight(0)

        session_icon = QLabel("")
        session_icon.setObjectName("SessionRowIcon")
        session_icon.setFixedSize(18, 18)
        session_icon.setAlignment(Qt.AlignCenter)
        session_icon.setPixmap((icon("trace") or self.style().standardIcon(QStyle.SP_FileDialogDetailedView)).pixmap(14, 14))

        text_stack = QVBoxLayout()
        text_stack.setContentsMargins(0, 0, 0, 0)
        text_stack.setSpacing(5)
        label = QLabel(record.title)
        label.setObjectName("SessionRowTitle")
        label.setProperty("dev_id", record.session_id)
        label.setToolTip(record.session_id)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._title_label = label
        metadata = QLabel(self._metadata_text)
        metadata.setObjectName("SessionRowMeta")
        metadata.setMinimumWidth(0)
        metadata.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        metadata.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._metadata_label = metadata
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(8)
        text_stack.addWidget(label)
        meta_row.addWidget(metadata, 1)

        status = QLabel(_runtime_status_label(record.runtime_status))
        status.setObjectName("SessionRowStatus")
        status.setProperty("status", _runtime_status_key(record.runtime_status))
        status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        menu_button = QToolButton()
        menu_button.setObjectName("SessionRowMenuButton")
        menu_button.setProperty("dev_id", f"menu:{record.session_id}")
        menu_button.setToolTip("Session actions")
        menu_button.setCursor(Qt.PointingHandCursor)
        menu_button.setText("⋯")
        menu_button.setPopupMode(QToolButton.InstantPopup)
        menu_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        menu_button.setAutoRaise(True)
        self._menu_button = menu_button

        menu = QMenu(menu_button)
        menu.setObjectName("SessionRowMenu")
        delete_action = QAction("Delete", menu)
        delete_action.setObjectName("SessionRowDeleteAction")
        delete_action.setProperty("dev_id", f"delete:{record.session_id}")
        delete_action.triggered.connect(lambda: on_delete(record.session_id))
        menu.addAction(delete_action)
        menu_button.setMenu(menu)
        menu_button.addAction(delete_action)

        meta_row.addWidget(status)
        meta_row.addWidget(menu_button)
        text_stack.addLayout(meta_row)

        layout.addWidget(self.rail)
        layout.addWidget(session_icon, 0, Qt.AlignTop)
        layout.addLayout(text_stack, 1)
        self._refresh_title_elision()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_title_elision()

    def _refresh_title_elision(self) -> None:
        if self._title_label is not None:
            available = self._title_label.width()
            if available > 0:
                metrics = QFontMetrics(self._title_label.font())
                self._title_label.setText(metrics.elidedText(self._full_title, Qt.ElideRight, available))
        if self._metadata_label is not None:
            available = self._metadata_label.width()
            if available > 0:
                metrics = QFontMetrics(self._metadata_label.font())
                self._metadata_label.setText(metrics.elidedText(self._metadata_text, Qt.ElideRight, available))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and not self._clicked_menu_button(event.position().toPoint()):
            self._on_select()
        super().mouseReleaseEvent(event)

    def _clicked_menu_button(self, point) -> bool:
        if self._menu_button is None:
            return False
        local = self._menu_button.mapFrom(self, point)
        return self._menu_button.rect().contains(local)

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


def _runtime_status_key(status: str) -> str:
    value = status.lower()
    if "run" in value:
        return "running"
    if "error" in value or "fail" in value:
        return "error"
    if "done" in value or "finish" in value or "pass" in value or "success" in value:
        return "success"
    return "ready"


def _runtime_status_label(status: str) -> str:
    return {
        "running": "Running",
        "error": "Error",
        "success": "Done",
        "ready": "Ready",
    }[_runtime_status_key(status)]
