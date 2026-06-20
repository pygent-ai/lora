# Grouped Session Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sidebar scope tabs with a grouped, collapsible, top-level-sortable session list that shows conversations and project sessions at the same time.

**Architecture:** Keep `gui.session_model` responsible for persisted GUI state and scope/session view models. Keep `gui.widgets.sessions` responsible for rendering the sidebar, using a `QTreeWidget` for group hierarchy while reusing the existing session row widget. Keep `gui.main_window` responsible for active scope switching, multi-scope group construction, and persistence of group order/collapse state.

**Tech Stack:** Python 3.13, PySide6, Qt offscreen unit tests, existing `SessionManager`, existing `ChatSessionStore`, pytest/unittest.

---

## File Map

- Modify `src/gui/session_model.py`
  - Add `scope_order` and `collapsed_scope_ids` to `GuiProjectState`.
  - Add `remember_scope_order()` and `set_scope_collapsed()` helpers.
  - Apply persisted scope order in `build_session_scopes()`.
  - Add `SessionGroupRecord`.

- Modify `src/gui/widgets/sessions.py`
  - Replace visible `QTabBar + QListWidget` session area with a `QTreeWidget`.
  - Add `_SessionGroupHeader`.
  - Keep `_SessionRow` and its delete menu behavior.
  - Emit `(scope_id, session_id)` from `session_selected`.
  - Emit group order and collapse state changes.

- Modify `src/gui/theme.py`
  - Add styles for `#SessionTree`, `#SessionGroupHeader`, `#SessionGroupTitle`, `#SessionGroupCount`, and drag/selection states.
  - Keep existing `#SessionRow` styles intact.

- Modify `src/gui/main_window.py`
  - Build all session groups from all scopes.
  - Switch active scope when a session from another group is selected.
  - Persist group order and collapsed state.
  - Keep `create_chat()` scoped to the active scope.

- Modify `tests/unit/test_gui_session_model.py`
  - Cover persisted scope order and collapsed scope ids.
  - Cover ordered scope construction.

- Modify `tests/unit/test_gui_session_sidebar.py`
  - Replace scope tab tests with grouped sidebar tests.
  - Keep existing session row tests updated to use `set_session_groups()`.

- Modify `tests/unit/test_gui_main_window.py`
  - Replace direct `scope_tabs` assertions with grouped sidebar assertions.
  - Cover selecting sessions across scopes and project group creation.

---

### Task 1: Persist Group Layout State

**Files:**
- Modify: `src/gui/session_model.py`
- Test: `tests/unit/test_gui_session_model.py`

- [ ] **Step 1: Add failing tests for scope order and collapsed groups**

Append these tests to `GuiSessionModelTests` in `tests/unit/test_gui_session_model.py`:

```python
    def test_project_state_saves_scope_order_and_collapsed_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = str((Path(tmp) / "repo").resolve())
            state = GuiProjectState.load(state_path)

            state.remember_scope_order([f"project:{project}", "conversation"])
            state.set_scope_collapsed("conversation", True)
            state.set_scope_collapsed(f"project:{project}", False)
            restored = GuiProjectState.load(state_path)

            self.assertEqual(restored.scope_order, [f"project:{project}", "conversation"])
            self.assertEqual(restored.collapsed_scope_ids, ["conversation"])

    def test_build_session_scopes_applies_persisted_scope_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            state = GuiProjectState.load(state_path)
            state.remember_project(first)
            state.remember_project(second)
            state.remember_scope_order([f"project:{first.resolve()}", "conversation"])

            scopes = build_session_scopes(state)

            self.assertEqual(
                [scope.scope_id for scope in scopes],
                [f"project:{first.resolve()}", "conversation", f"project:{second.resolve()}"],
            )

    def test_build_session_scopes_ignores_stale_scope_order_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            state = GuiProjectState.load(state_path)
            state.remember_project(project)
            state.remember_scope_order(["project:E:/missing", "conversation"])

            scopes = build_session_scopes(state)

            self.assertEqual([scope.scope_id for scope in scopes], ["conversation", f"project:{project.resolve()}"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/unit/test_gui_session_model.py -v
```

Expected: FAIL because `GuiProjectState` has no `scope_order`, `collapsed_scope_ids`, `remember_scope_order()`, or `set_scope_collapsed()`.

- [ ] **Step 3: Implement persisted layout state**

In `src/gui/session_model.py`, update the dataclass and load/save methods:

```python
@dataclass(slots=True)
class GuiProjectState:
    state_path: Path
    default_project_path: str | None = None
    recent_project_paths: list[str] | None = None
    scope_order: list[str] | None = None
    collapsed_scope_ids: list[str] | None = None
```

Use these helpers in the class:

```python
    def remember_scope_order(self, scope_ids: list[str]) -> None:
        seen: set[str] = set()
        ordered: list[str] = []
        for scope_id in scope_ids:
            normalized = _normalize_scope_id(scope_id)
            if normalized and normalized not in seen:
                ordered.append(normalized)
                seen.add(normalized)
        self.scope_order = ordered
        self.save()

    def set_scope_collapsed(self, scope_id: str, collapsed: bool) -> None:
        normalized = _normalize_scope_id(scope_id)
        if not normalized:
            return
        collapsed_ids = [item for item in self.collapsed_scope_ids or [] if item != normalized]
        if collapsed:
            collapsed_ids.append(normalized)
        self.collapsed_scope_ids = collapsed_ids
        self.save()
```

Update `load()` to read the fields:

```python
        scope_order = [_normalize_scope_id(str(item)) for item in data.get("scope_order") or []]
        collapsed = [_normalize_scope_id(str(item)) for item in data.get("collapsed_scope_ids") or []]
        scope_order = [item for item in scope_order if item]
        collapsed = [item for item in collapsed if item]
        return cls(
            state_path=path,
            default_project_path=resolved_default,
            recent_project_paths=recent,
            scope_order=_unique(scope_order),
            collapsed_scope_ids=_unique(collapsed),
        )
```

Update the missing-file return:

```python
        if not path.exists():
            return cls(state_path=path, recent_project_paths=[], scope_order=[], collapsed_scope_ids=[])
```

Update `save()`:

```python
                {
                    "default_project_path": self.default_project_path,
                    "recent_project_paths": self.recent_project_paths or [],
                    "scope_order": self.scope_order or [],
                    "collapsed_scope_ids": self.collapsed_scope_ids or [],
                },
```

Add module helpers:

```python
def _normalize_scope_id(scope_id: str) -> str:
    value = str(scope_id).strip()
    if not value:
        return ""
    if value == "conversation":
        return value
    if value.startswith("project:"):
        project = value.removeprefix("project:")
        if not project.strip():
            return ""
        return f"project:{Path(project).expanduser().resolve()}"
    return ""


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
```

- [ ] **Step 4: Apply order in `build_session_scopes()`**

Replace the end of `build_session_scopes()` so it returns ordered scopes:

```python
    scope_by_id = {scope.scope_id: scope for scope in scopes}
    ordered: list[SessionScope] = []
    for scope_id in state.scope_order or []:
        normalized = _normalize_scope_id(scope_id)
        scope = scope_by_id.get(normalized)
        if scope is not None:
            ordered.append(scope)
    ordered_ids = {scope.scope_id for scope in ordered}
    ordered.extend(scope for scope in scopes if scope.scope_id not in ordered_ids)
    return ordered
```

- [ ] **Step 5: Run model tests**

Run:

```bash
pytest tests/unit/test_gui_session_model.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/gui/session_model.py tests/unit/test_gui_session_model.py
git commit -m "feat(gui): persist session group layout"
```

---

### Task 2: Add Group View Model

**Files:**
- Modify: `src/gui/session_model.py`
- Test: `tests/unit/test_gui_session_model.py`

- [ ] **Step 1: Add a focused import test for `SessionGroupRecord`**

Update the import in `tests/unit/test_gui_session_model.py`:

```python
from gui.session_model import (
    ChatSessionRecord,
    ChatSessionStore,
    GuiProjectState,
    SessionGroupRecord,
    build_session_scopes,
)
```

Append this test:

```python
    def test_session_group_record_carries_scope_records_and_collapsed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = GuiProjectState.load(Path(tmp) / "state.json")
            scope = build_session_scopes(state)[0]
            record = ChatSessionRecord(
                session_id="chat-one",
                session_dir="sessions/chat-one",
                case_id="chat",
                mode="chat",
                created_at="1",
                updated_at="2",
                title="Chat one",
            )

            group = SessionGroupRecord(scope=scope, records=[record], collapsed=True)

            self.assertEqual(group.scope.scope_id, "conversation")
            self.assertEqual(group.records, [record])
            self.assertTrue(group.collapsed)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/unit/test_gui_session_model.py::GuiSessionModelTests::test_session_group_record_carries_scope_records_and_collapsed_state -v
```

Expected: FAIL with import error for `SessionGroupRecord`.

- [ ] **Step 3: Add `SessionGroupRecord`**

In `src/gui/session_model.py`, place this dataclass after `ChatSessionRecord`:

```python
@dataclass(frozen=True, slots=True)
class SessionGroupRecord:
    scope: SessionScope
    records: list[ChatSessionRecord]
    collapsed: bool = False
```

- [ ] **Step 4: Run model tests**

Run:

```bash
pytest tests/unit/test_gui_session_model.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/gui/session_model.py tests/unit/test_gui_session_model.py
git commit -m "feat(gui): add grouped session view model"
```

---

### Task 3: Render Grouped Sidebar

**Files:**
- Modify: `src/gui/widgets/sessions.py`
- Test: `tests/unit/test_gui_session_sidebar.py`

- [ ] **Step 1: Update sidebar test imports**

In `tests/unit/test_gui_session_sidebar.py`, change imports to include tree widgets and model types:

```python
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolButton, QTreeWidgetItem

from gui.session_model import ChatSessionRecord, SessionGroupRecord, SessionScope
from gui.widgets.sessions import SessionSidebar
```

Add these helpers near the bottom of the test file:

```python
def _scope(scope_id: str, label: str, tooltip: str | None = None) -> SessionScope:
    return SessionScope(
        scope_id=scope_id,
        label=label,
        tooltip=tooltip or label,
        workspace_root=None if scope_id == "conversation" else tooltip or label,
        lora_root=f"/tmp/{label}/.lora",
        runtime_workspace_root=f"/tmp/{label}",
    )


def _record(session_id: str, title: str, updated_at: str = "2", status: str = "ready") -> ChatSessionRecord:
    return ChatSessionRecord(
        session_id=session_id,
        session_dir=f"sessions/{session_id}",
        case_id="chat",
        mode="chat",
        created_at="1",
        updated_at=updated_at,
        title=title,
        runtime_status=status,
    )


def _groups() -> list[SessionGroupRecord]:
    return [
        SessionGroupRecord(scope=_scope("conversation", "对话"), records=[_record("chat-one", "Chat one")]),
        SessionGroupRecord(
            scope=_scope("project:E:/Projects/lora", "lora", "E:/Projects/lora"),
            records=[_record("chat-two", "Chat two")],
        ),
    ]
```

- [ ] **Step 2: Replace old `set_sessions()` calls in existing tests**

For tests that render a single session, replace:

```python
sidebar.set_sessions([record])
row = sidebar.sessions.itemWidget(sidebar.sessions.item(0))
```

with:

```python
sidebar.set_session_groups(
    [SessionGroupRecord(scope=_scope("conversation", "对话"), records=[record])],
    active_scope_id="conversation",
    active_session_id=record.session_id,
)
row = sidebar.session_tree.itemWidget(sidebar.session_tree.topLevelItem(0).child(0), 0)
```

For tests that render two sessions in one group, replace:

```python
sidebar.set_sessions([first, second])
row = sidebar.sessions.itemWidget(sidebar.sessions.item(1))
```

with:

```python
sidebar.set_session_groups(
    [SessionGroupRecord(scope=_scope("conversation", "对话"), records=[first, second])],
    active_scope_id="conversation",
    active_session_id=first.session_id,
)
row = sidebar.session_tree.itemWidget(sidebar.session_tree.topLevelItem(0).child(1), 0)
```

- [ ] **Step 3: Add failing grouped sidebar tests**

Replace `test_sidebar_shows_scope_tabs_and_emits_selected_scope` with:

```python
    def test_sidebar_renders_group_headers_and_session_children(self) -> None:
        sidebar = SessionSidebar()

        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id="chat-one")

        self.assertEqual(sidebar.session_tree.topLevelItemCount(), 2)
        first_header = sidebar.session_tree.itemWidget(sidebar.session_tree.topLevelItem(0), 0)
        second_header = sidebar.session_tree.itemWidget(sidebar.session_tree.topLevelItem(1), 0)
        assert first_header is not None
        assert second_header is not None
        self.assertIsNotNone(first_header.findChild(QLabel, "SessionGroupTitle"))
        self.assertEqual(first_header.findChild(QLabel, "SessionGroupTitle").text(), "对话")
        self.assertEqual(second_header.findChild(QLabel, "SessionGroupTitle").text(), "lora")
        self.assertEqual(sidebar.session_tree.topLevelItem(1).toolTip(0), "E:/Projects/lora")
        self.assertEqual(sidebar.session_tree.topLevelItem(0).childCount(), 1)
        self.assertEqual(sidebar.session_tree.topLevelItem(1).childCount(), 1)

    def test_collapsed_group_starts_collapsed(self) -> None:
        sidebar = SessionSidebar()
        group = SessionGroupRecord(scope=_scope("conversation", "对话"), records=[_record("chat-one", "Chat one")], collapsed=True)

        sidebar.set_session_groups([group], active_scope_id="conversation", active_session_id=None)

        self.assertFalse(sidebar.session_tree.topLevelItem(0).isExpanded())

    def test_clicking_session_emits_scope_and_session_id(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id=None)
        emitted: list[tuple[str, str]] = []
        sidebar.session_selected.connect(lambda scope_id, session_id: emitted.append((scope_id, session_id)))

        child = sidebar.session_tree.topLevelItem(1).child(0)
        sidebar._emit_selected(child, 0)

        self.assertEqual(emitted, [("project:E:/Projects/lora", "chat-two")])
```

- [ ] **Step 4: Run sidebar tests to verify they fail**

Run:

```bash
pytest tests/unit/test_gui_session_sidebar.py -v
```

Expected: FAIL because `SessionSidebar` has no `session_tree` or `set_session_groups()`.

- [ ] **Step 5: Implement tree imports and signals**

In `src/gui/widgets/sessions.py`, update imports:

```python
from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStyle,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.session_model import ChatSessionRecord, SessionGroupRecord
```

Change the signals:

```python
    session_selected = Signal(str, str)
    group_order_changed = Signal(list)
    group_collapsed_changed = Signal(str, bool)
```

- [ ] **Step 6: Replace session list construction**

In `SessionSidebar.__init__`, remove `self.scope_tabs`, `_scope_ids`, and `self.sessions`. Add:

```python
        self.session_tree = QTreeWidget()
        self.session_tree.setObjectName("SessionTree")
        self.session_tree.setHeaderHidden(True)
        self.session_tree.setColumnCount(1)
        self.session_tree.setFocusPolicy(Qt.NoFocus)
        self.session_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.session_tree.setIndentation(14)
        self.session_tree.setRootIsDecorated(False)
        self.session_tree.setExpandsOnDoubleClick(False)
        self.session_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.session_tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.session_tree.itemClicked.connect(self._emit_selected)
        self.session_tree.itemExpanded.connect(lambda item: self._emit_group_collapsed(item, False))
        self.session_tree.itemCollapsed.connect(lambda item: self._emit_group_collapsed(item, True))
```

Replace layout additions:

```python
        layout.addWidget(recent)
        layout.addWidget(self.session_tree, 1)
```

Keep `recent = QLabel("Sessions")` instead of `"Recent Sessions"`.

- [ ] **Step 7: Add grouped rendering methods**

Replace `set_scopes()`, `set_sessions()`, `select_session()`, and `selected_session_id()` with:

```python
    def set_session_groups(
        self,
        groups: list[SessionGroupRecord],
        *,
        active_scope_id: str,
        active_session_id: str | None,
    ) -> None:
        with QSignalBlocker(self.session_tree):
            self.session_tree.clear()
            self._rows.clear()
            for group in groups:
                group_item = QTreeWidgetItem()
                group_item.setData(0, Qt.UserRole, "group")
                group_item.setData(0, Qt.UserRole + 1, group.scope.scope_id)
                group_item.setToolTip(0, group.scope.tooltip)
                group_item.setFlags(
                    Qt.ItemIsEnabled
                    | Qt.ItemIsSelectable
                    | Qt.ItemIsDragEnabled
                    | Qt.ItemIsDropEnabled
                )
                self.session_tree.addTopLevelItem(group_item)
                header = _SessionGroupHeader(group.scope.label, len(group.records), active=group.scope.scope_id == active_scope_id)
                self.session_tree.setItemWidget(group_item, 0, header)
                group_item.setSizeHint(0, QSize(header.sizeHint().width(), 34))
                for record in group.records:
                    item = QTreeWidgetItem(group_item)
                    item.setData(0, Qt.UserRole, "session")
                    item.setData(0, Qt.UserRole + 1, group.scope.scope_id)
                    item.setData(0, Qt.UserRole + 2, record.session_id)
                    item.setToolTip(0, record.session_id)
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    row = _SessionRow(record, self.session_delete_requested.emit)
                    item.setSizeHint(0, QSize(row.sizeHint().width(), 58))
                    self.session_tree.setItemWidget(item, 0, row)
                    self._rows[f"{group.scope.scope_id}\n{record.session_id}"] = row
                group_item.setExpanded(not group.collapsed)
        if active_session_id is not None:
            self.select_session(active_scope_id, active_session_id)

    def select_session(self, scope_id: str, session_id: str) -> None:
        for group_index in range(self.session_tree.topLevelItemCount()):
            group = self.session_tree.topLevelItem(group_index)
            for child_index in range(group.childCount()):
                item = group.child(child_index)
                item_scope_id = str(item.data(0, Qt.UserRole + 1))
                item_session_id = str(item.data(0, Qt.UserRole + 2))
                selected = item_scope_id == scope_id and item_session_id == session_id
                if selected:
                    self.session_tree.setCurrentItem(item)
                    group.setExpanded(True)
                row = self._rows.get(f"{item_scope_id}\n{item_session_id}")
                if row is not None:
                    row.set_selected(selected)

    def selected_session_id(self) -> str | None:
        item = self.session_tree.currentItem()
        if item is None or item.data(0, Qt.UserRole) != "session":
            return None
        return str(item.data(0, Qt.UserRole + 2))
```

- [ ] **Step 8: Add event helpers and group header**

Add these methods to `SessionSidebar`:

```python
    def _emit_selected(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        if item.data(0, Qt.UserRole) != "session":
            item.setExpanded(not item.isExpanded())
            return
        scope_id = str(item.data(0, Qt.UserRole + 1))
        session_id = str(item.data(0, Qt.UserRole + 2))
        self.select_session(scope_id, session_id)
        self.session_selected.emit(scope_id, session_id)

    def _emit_group_collapsed(self, item: QTreeWidgetItem, collapsed: bool) -> None:
        if item.data(0, Qt.UserRole) == "group":
            self.group_collapsed_changed.emit(str(item.data(0, Qt.UserRole + 1)), collapsed)

    def group_order(self) -> list[str]:
        return [
            str(self.session_tree.topLevelItem(index).data(0, Qt.UserRole + 1))
            for index in range(self.session_tree.topLevelItemCount())
        ]
```

Add this widget above `_SessionRow`:

```python
class _SessionGroupHeader(QWidget):
    def __init__(self, label: str, count: int, *, active: bool):
        super().__init__()
        self.setObjectName("SessionGroupHeader")
        self.setProperty("active", active)
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        arrow = QLabel("▾")
        arrow.setObjectName("SessionGroupArrow")
        arrow.setFixedWidth(14)
        arrow.setAlignment(Qt.AlignCenter)

        title = QLabel(label)
        title.setObjectName("SessionGroupTitle")
        title.setMinimumWidth(0)
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        counter = QLabel(str(count))
        counter.setObjectName("SessionGroupCount")
        counter.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(arrow)
        layout.addWidget(title, 1)
        layout.addWidget(counter)
```

- [ ] **Step 9: Run sidebar tests**

Run:

```bash
pytest tests/unit/test_gui_session_sidebar.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```bash
git add src/gui/widgets/sessions.py tests/unit/test_gui_session_sidebar.py
git commit -m "feat(gui): render grouped session sidebar"
```

---

### Task 4: Enforce Group-Only Reordering

**Files:**
- Modify: `src/gui/widgets/sessions.py`
- Test: `tests/unit/test_gui_session_sidebar.py`

- [ ] **Step 1: Add failing reorder tests**

Append these tests to `GuiSessionSidebarTests`:

```python
    def test_group_order_changed_emits_top_level_scope_order(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id=None)
        emitted: list[list[str]] = []
        sidebar.group_order_changed.connect(emitted.append)

        moved = sidebar.session_tree.takeTopLevelItem(1)
        sidebar.session_tree.insertTopLevelItem(0, moved)
        sidebar._emit_group_order_changed()

        self.assertEqual(emitted, [["project:E:/Projects/lora", "conversation"]])

    def test_session_items_are_not_drag_enabled(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id=None)

        session_item = sidebar.session_tree.topLevelItem(0).child(0)

        self.assertFalse(bool(session_item.flags() & Qt.ItemIsDragEnabled))
        self.assertFalse(bool(session_item.flags() & Qt.ItemIsDropEnabled))
```

Add `Qt` to the test imports:

```python
from PySide6.QtCore import Qt
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/unit/test_gui_session_sidebar.py::GuiSessionSidebarTests::test_group_order_changed_emits_top_level_scope_order tests/unit/test_gui_session_sidebar.py::GuiSessionSidebarTests::test_session_items_are_not_drag_enabled -v
```

Expected: FAIL because `_emit_group_order_changed()` is not defined.

- [ ] **Step 3: Emit top-level order after internal moves**

In `SessionSidebar.__init__`, connect the tree model rows moved signal after creating `self.session_tree`:

```python
        self.session_tree.model().rowsMoved.connect(lambda *_args: self._emit_group_order_changed())
```

Add:

```python
    def _emit_group_order_changed(self) -> None:
        self.group_order_changed.emit(self.group_order())
```

- [ ] **Step 4: Run sidebar tests**

Run:

```bash
pytest tests/unit/test_gui_session_sidebar.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/gui/widgets/sessions.py tests/unit/test_gui_session_sidebar.py
git commit -m "feat(gui): persistable session group ordering signal"
```

---

### Task 5: Integrate Groups Into Main Window

**Files:**
- Modify: `src/gui/main_window.py`
- Test: `tests/unit/test_gui_main_window.py`

- [ ] **Step 1: Update failing main window tests**

Replace `test_without_project_path_starts_in_conversation_scope` assertions:

```python
            self.assertEqual(window._active_scope_id, "conversation")
            first_group = window.sidebar.session_tree.topLevelItem(0)
            first_header = window.sidebar.session_tree.itemWidget(first_group, 0)
            assert first_header is not None
            self.assertEqual(first_header.findChild(QLabel, "SessionGroupTitle").text(), "对话")
            self.assertIsNone(window.current_scope.workspace_root)
```

Replace `test_set_project_path_remembers_default_and_switches_scope` final assertion:

```python
            self.assertEqual(window._active_scope_id, f"project:{project.resolve()}")
            self.assertEqual(window.config.workspace_root, str(project.resolve()))
            labels = []
            for index in range(window.sidebar.session_tree.topLevelItemCount()):
                header = window.sidebar.session_tree.itemWidget(window.sidebar.session_tree.topLevelItem(index), 0)
                assert header is not None
                labels.append(header.findChild(QLabel, "SessionGroupTitle").text())
            self.assertIn("repo", labels)
```

Append:

```python
    def test_sidebar_initial_render_includes_conversation_and_project_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            project.mkdir()
            state = json.loads("{}")
            state["default_project_path"] = str(project.resolve())
            state["recent_project_paths"] = [str(project.resolve())]
            state_path.write_text(json.dumps(state), encoding="utf-8")

            window = MainWindow(workspace_root=None, state_path=state_path)

            labels: list[str] = []
            for index in range(window.sidebar.session_tree.topLevelItemCount()):
                header = window.sidebar.session_tree.itemWidget(window.sidebar.session_tree.topLevelItem(index), 0)
                assert header is not None
                labels.append(header.findChild(QLabel, "SessionGroupTitle").text())
            self.assertEqual(labels, ["对话", "repo"])

    def test_select_project_session_switches_active_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            project.mkdir()
            window = MainWindow(workspace_root=None, state_path=state_path)
            window.set_project_path(str(project))
            project_session = window.store.create_chat_session().session_id
            window.select_scope("conversation")

            window.select_session(f"project:{project.resolve()}", project_session)

            self.assertEqual(window._active_scope_id, f"project:{project.resolve()}")
            self.assertEqual(window._active_session_id, project_session)
            self.assertEqual(window.config.workspace_root, str(project.resolve()))

    def test_group_order_and_collapse_state_are_saved_from_sidebar_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            project = Path(tmp) / "repo"
            project.mkdir()
            window = MainWindow(workspace_root=None, state_path=state_path)
            window.set_project_path(str(project))

            window._remember_group_order([f"project:{project.resolve()}", "conversation"])
            window._remember_group_collapsed("conversation", True)
            restored = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(restored["scope_order"], [f"project:{project.resolve()}", "conversation"])
            self.assertEqual(restored["collapsed_scope_ids"], ["conversation"])
```

- [ ] **Step 2: Run main window tests to verify failure**

Run:

```bash
pytest tests/unit/test_gui_main_window.py -v
```

Expected: FAIL because `SessionSidebar` no longer has `scope_tabs`, `MainWindow.select_session()` still accepts only one argument, and grouped render helpers are missing.

- [ ] **Step 3: Update imports and signal wiring**

In `src/gui/main_window.py`, update import:

```python
from gui.session_model import ChatSessionStore, GuiProjectState, SessionGroupRecord, SessionScope, build_session_scopes
```

Update signal connections:

```python
        self.sidebar.session_selected.connect(self.select_session)
        self.sidebar.group_order_changed.connect(self._remember_group_order)
        self.sidebar.group_collapsed_changed.connect(self._remember_group_collapsed)
```

Remove the old `self.sidebar.scope_selected.connect(self.select_scope)` connection.

- [ ] **Step 4: Update `create_chat()` and `select_session()`**

Replace `create_chat()`:

```python
    def create_chat(self) -> None:
        record = self.store.create_chat_session()
        self.project_state.set_scope_collapsed(self._active_scope_id, False)
        self.refresh_sessions()
        self.select_session(self._active_scope_id, record.session_id)
```

Replace `select_session()` with a two-argument method:

```python
    def select_session(self, scope_id: str, session_id: str | None = None) -> None:
        if session_id is None:
            session_id = scope_id
            scope_id = self._active_scope_id
        if scope_id != self._active_scope_id:
            self._switch_active_scope(scope_id)
        self._active_session_id = session_id
        try:
            session = self.manager.load(session_id)
        except Exception:  # noqa: BLE001 - GUI boundary recovers from external file changes.
            self._active_session_id = None
            self.chat.clear()
            self.chat.set_session_title("Select or create a chat session")
            self.inspector.reset_context()
            self.refresh_sessions()
            return
        self.sidebar.select_session(scope_id, session_id)
        self.chat.set_session_context(session_id, agent=self.config.agent_alias, model=self.config.model_name)
        self.chat.render_history(session.history)
        self.inspector.reset_context(session_id=session_id)
        self._load_last_run_trace(session_id, session.metadata)
        self._turn_index = _next_turn_index(session.history)
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            self._render_running_session(active_run)
        else:
            self.chat.set_running(False)
```

Add:

```python
    def _switch_active_scope(self, scope_id: str) -> None:
        self._active_scope_id = scope_id
        self.current_scope = self._scope_by_id(scope_id)
        self.config = self._config_for_scope(self.current_scope)
        self.manager = SessionManager(self.config)
        self.store = ChatSessionStore(self.manager)
        self._refresh_static_ui()
```

- [ ] **Step 5: Render all session groups**

Replace `refresh_sessions()`:

```python
    def refresh_sessions(self) -> None:
        self.sidebar.set_session_groups(
            self._build_session_groups(),
            active_scope_id=self._active_scope_id,
            active_session_id=self._active_session_id,
        )
```

Add:

```python
    def _build_session_groups(self) -> list[SessionGroupRecord]:
        groups: list[SessionGroupRecord] = []
        collapsed_ids = set(self.project_state.collapsed_scope_ids or [])
        for scope in self.scopes:
            store = ChatSessionStore(SessionManager(self._config_for_scope(scope)))
            records = []
            for record in store.list_chat_sessions():
                active_run = self._running_sessions.get(record.session_id)
                if active_run is None:
                    records.append(record)
                else:
                    records.append(replace(record, runtime_status=active_run.status))
            groups.append(
                SessionGroupRecord(
                    scope=scope,
                    records=records,
                    collapsed=scope.scope_id in collapsed_ids,
                )
            )
        return groups
```

- [ ] **Step 6: Adjust scope-changing methods**

In `select_scope()`, keep the public method for tests and internal callers, but use `_switch_active_scope()`:

```python
    def select_scope(self, scope_id: str) -> None:
        if scope_id == self._active_scope_id:
            return
        if self._has_running_sessions():
            self._show_warning("Runs in progress", "Wait for running sessions to finish before changing projects.")
            self._refresh_static_ui()
            return
        self._switch_active_scope(scope_id)
        self._active_session_id = None
        self._turn_index = 1
        self.chat.clear()
        self.chat.set_session_title("Select or create a chat session")
        self.inspector.reset_context()
        self.refresh_sessions()
        self._select_initial_session()
```

Update `_select_initial_session()`:

```python
    def _select_initial_session(self) -> None:
        records = self.store.list_chat_sessions()
        if records:
            self.select_session(self._active_scope_id, records[0].session_id)
```

Update `set_project_path()` after rebuilding scopes:

```python
        self.scopes = build_session_scopes(self.project_state)
        self.select_scope(f"project:{resolved}")
```

- [ ] **Step 7: Persist order and collapse**

Add:

```python
    def _remember_group_order(self, scope_ids: list[str]) -> None:
        self.project_state.remember_scope_order(scope_ids)
        self.scopes = build_session_scopes(self.project_state)

    def _remember_group_collapsed(self, scope_id: str, collapsed: bool) -> None:
        self.project_state.set_scope_collapsed(scope_id, collapsed)
```

- [ ] **Step 8: Remove scope tab updates from `_refresh_static_ui()`**

Delete the `self.sidebar.set_scopes(...)` call. Keep workspace, agent, theme, and inspector config updates:

```python
    def _refresh_static_ui(self) -> None:
        workspace_label = "对话" if self.current_scope.workspace_root is None else _short_path(self.config.workspace_root)
        self.sidebar.set_workspace(workspace_label)
        self.sidebar.set_agent_status(self.config.agent_alias, self.config.model_name)
        self.sidebar.set_theme_name(self._theme)
        self.inspector.set_config(
            {
                "workspace": self.config.workspace_root,
                "scope": self.current_scope.label,
                "lora_root": self.config.lora_root,
                "agent": self.config.agent_alias,
                "model": self.config.model_name,
                "api_key_source": self.config.api_key_source,
                "base_url": self.config.base_url,
                "max_steps": self.config.max_steps,
            }
        )
```

- [ ] **Step 9: Run main window tests**

Run:

```bash
pytest tests/unit/test_gui_main_window.py -v
```

Expected: PASS.

- [ ] **Step 10: Run all GUI unit tests**

Run:

```bash
pytest tests/unit/test_gui_session_model.py tests/unit/test_gui_session_sidebar.py tests/unit/test_gui_main_window.py -v
```

Expected: PASS.

- [ ] **Step 11: Commit**

Run:

```bash
git add src/gui/main_window.py tests/unit/test_gui_main_window.py
git commit -m "feat(gui): integrate grouped session sidebar"
```

---

### Task 6: Style Grouped Sidebar

**Files:**
- Modify: `src/gui/theme.py`
- Test: `tests/unit/test_gui_theme.py`

- [ ] **Step 1: Add failing stylesheet coverage**

Append this test inside `GuiThemeTests` in `tests/unit/test_gui_theme.py`:

```python
    def test_session_group_styles_are_defined(self) -> None:
        css = theme_stylesheet("day")

        self.assertIn("#SessionTree", css)
        self.assertIn("#SessionGroupHeader", css)
        self.assertIn("#SessionGroupTitle", css)
        self.assertIn("#SessionGroupCount", css)
```

- [ ] **Step 2: Run theme test to verify failure**

Run:

```bash
pytest tests/unit/test_gui_theme.py -v
```

Expected: FAIL because group selectors are not in the stylesheet.

- [ ] **Step 3: Add styles**

In `src/gui/theme.py`, add these selectors near the existing `#SessionList` and `#SessionRow` styles:

```python
#SessionTree {{
    border: 0;
    outline: none;
    background: transparent;
}}
#SessionTree::item {{
    padding: 0;
    margin: 0;
    border: 0;
}}
#SessionTree::item:selected {{
    background: transparent;
}}
#SessionGroupHeader {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
}}
#SessionGroupHeader[active="true"] {{
    background: {pill_fill};
    border-color: {c.border};
}}
#SessionGroupHeader:hover {{
    background: {list_hover};
    border-color: {c.border_strong};
}}
#SessionGroupArrow {{
    color: {c.text_soft};
    font-size: {t.meta_size + 1}px;
    font-weight: 700;
}}
#SessionGroupTitle {{
    color: {c.text_muted};
    font-size: {t.body_size}px;
    font-weight: 700;
}}
#SessionGroupCount {{
    color: {c.text_soft};
    font-size: {t.meta_size}px;
    font-weight: 600;
}}
```

Keep the existing `#SessionRow` styles unchanged.

- [ ] **Step 4: Run theme tests**

Run:

```bash
pytest tests/unit/test_gui_theme.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/gui/theme.py tests/unit/test_gui_theme.py
git commit -m "style(gui): add session group sidebar styles"
```

---

### Task 7: Full Verification

**Files:**
- No source edits unless verification exposes a defect.

- [ ] **Step 1: Run targeted GUI tests**

Run:

```bash
pytest tests/unit/test_gui_session_model.py tests/unit/test_gui_session_sidebar.py tests/unit/test_gui_main_window.py tests/unit/test_gui_theme.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full unit suite**

Run:

```bash
pytest tests/unit -v
```

Expected: PASS.

- [ ] **Step 3: Launch the GUI for manual smoke**

Run:

```bash
lora-gui
```

Expected:

- The left sidebar shows `对话` and any remembered project directories at the same time.
- `New Chat` creates a session under the active group.
- Clicking a session in a project group loads that project session.
- Group collapse and expand work.
- Dragging top-level groups changes their order.
- Restarting the GUI preserves group order and collapsed groups.
- Session rows keep the existing title, timestamp, status, icon, hover, selected state, and delete menu style.

- [ ] **Step 4: Commit verification fixes if needed**

If a verification-only defect required a fix, run:

```bash
git add src/gui tests/unit
git commit -m "fix(gui): polish grouped session sidebar verification"
```

If no fix was needed, do not create an empty commit.

---

## Self-Review Notes

Spec coverage:

- Grouped all-scope list: Tasks 3 and 5.
- Collapsible groups: Tasks 1, 3, and 5.
- Top-level group order persistence: Tasks 1, 4, and 5.
- Session order remains metadata-driven: Task 5 keeps `ChatSessionStore.list_chat_sessions()`.
- Existing visual style: Tasks 3 and 6 preserve `_SessionRow` and add narrow group styles.
- Main window active scope switching: Task 5.
- Error recovery for missing selected sessions: Task 5.
- Tests and manual verification: Tasks 1 through 7.

Type consistency:

- `SessionGroupRecord` is defined in Task 2 and used by sidebar/main-window tasks.
- `session_selected` consistently emits `(scope_id, session_id)`.
- `group_order_changed` emits `list[str]`.
- `group_collapsed_changed` emits `(scope_id, collapsed)`.

Scope check:

- The plan implements one feature: the grouped session sidebar. It does not include custom folders, session moving, group renaming, or session pinning.
