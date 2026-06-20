# Layered History Drawer Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the layered GUI shell where session history is a collapsible background navigation layer and chat plus trace inspector are the foreground workbench.

**Architecture:** Add a small `_LayeredShell` widget in `src/gui/main_window.py` to own geometry for `HistoryLayer` and `WorkbenchLayer`. Extend `SessionSidebar` with an expanded/collapsed presentation API, and add QSS selectors for the new visual layers while preserving existing signals and object names.

**Tech Stack:** Python 3.13, PySide6 widgets, Qt offscreen unit tests, existing QSS token generation in `src/gui/theme.py`, `unittest`.

---

## File Structure

- Modify `src/gui/main_window.py`
  - Add `_LayeredShell`, constants for history/workbench geometry, and `MainWindow._set_history_collapsed`.
  - Replace the direct top-level `QHBoxLayout` with `_LayeredShell`.
  - Preserve all existing signal wiring.

- Modify `src/gui/widgets/sessions.py`
  - Add `history_collapsed_changed = Signal(bool)`.
  - Add `collapse_button`, `set_collapsed`, `is_collapsed`, and `_toggle_history_collapsed`.
  - Keep existing expanded sidebar controls and session group behavior intact.

- Modify `src/gui/theme.py`
  - Add QSS selectors for `#HistoryLayer`, `#WorkbenchLayer`, `#SidebarCollapseButton`, and collapsed sidebar states.
  - Keep current day/night palette tokens and existing selectors stable.

- Modify `tests/unit/test_gui_main_window.py`
  - Replace the old direct layout spacing test with layered shell tests.
  - Add a resize/collapse geometry test.

- Modify `tests/unit/test_gui_session_sidebar.py`
  - Add collapse API and compact action reachability tests.

- Modify `tests/unit/test_gui_theme.py`
  - Add stylesheet selector coverage for the new layer and collapsed state selectors.

---

### Task 1: Sidebar Collapse API Tests

**Files:**
- Modify: `tests/unit/test_gui_session_sidebar.py`

- [ ] **Step 1: Write failing sidebar collapse tests**

Add these tests inside `GuiSessionSidebarTests`:

```python
    def test_sidebar_can_switch_between_expanded_and_collapsed_modes(self) -> None:
        sidebar = SessionSidebar()
        sidebar.set_session_groups(_groups(), active_scope_id="conversation", active_session_id="chat-one")

        sidebar.set_collapsed(True)

        self.assertTrue(sidebar.is_collapsed())
        self.assertEqual(sidebar.property("collapsed"), True)
        self.assertTrue(sidebar.session_tree.isHidden())
        self.assertEqual(sidebar.new_button.text(), "")
        self.assertEqual(sidebar.settings_button.text(), "")

        sidebar.set_collapsed(False)

        self.assertFalse(sidebar.is_collapsed())
        self.assertEqual(sidebar.property("collapsed"), False)
        self.assertFalse(sidebar.session_tree.isHidden())
        self.assertEqual(sidebar.new_button.text(), "New Chat")
        self.assertEqual(sidebar.settings_button.text(), "Settings")

    def test_sidebar_collapse_button_emits_collapsed_state(self) -> None:
        sidebar = SessionSidebar()
        emitted: list[bool] = []
        sidebar.history_collapsed_changed.connect(emitted.append)

        sidebar.collapse_button.click()
        sidebar.collapse_button.click()

        self.assertEqual(emitted, [True, False])
        self.assertFalse(sidebar.is_collapsed())

    def test_collapsed_sidebar_keeps_primary_actions_reachable(self) -> None:
        sidebar = SessionSidebar()

        sidebar.set_collapsed(True)

        self.assertFalse(sidebar.new_button.isHidden())
        self.assertFalse(sidebar.settings_button.isHidden())
        self.assertFalse(sidebar.project_button.isHidden())
        self.assertFalse(sidebar.day_button.isHidden())
        self.assertFalse(sidebar.night_button.isHidden())
        self.assertFalse(sidebar.new_button.icon().isNull())
        self.assertFalse(sidebar.settings_button.icon().isNull())
        self.assertEqual(sidebar.new_button.toolTip(), "Create a new chat session")
        self.assertEqual(sidebar.settings_button.toolTip(), "Open settings")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_gui_session_sidebar.py::GuiSessionSidebarTests::test_sidebar_can_switch_between_expanded_and_collapsed_modes tests/unit/test_gui_session_sidebar.py::GuiSessionSidebarTests::test_sidebar_collapse_button_emits_collapsed_state tests/unit/test_gui_session_sidebar.py::GuiSessionSidebarTests::test_collapsed_sidebar_keeps_primary_actions_reachable -q
```

Expected: FAIL because `SessionSidebar` does not yet expose `set_collapsed`, `is_collapsed`, `collapse_button`, or `history_collapsed_changed`.

- [ ] **Step 3: Implement minimal sidebar collapse API**

In `src/gui/widgets/sessions.py`, add the signal to `SessionSidebar`:

```python
    history_collapsed_changed = Signal(bool)
```

In `SessionSidebar.__init__`, add state before layout creation:

```python
        self._history_collapsed = False
```

Replace the current brand card title block:

```python
        title = QLabel("Lora")
        title.setObjectName("Brand")
        self.workspace = QLabel("")
        self.workspace.setObjectName("SidebarMeta")
        self.workspace.setWordWrap(True)
        brand_layout.addWidget(title)
        brand_layout.addWidget(self.workspace)
```

with:

```python
        brand_row = QWidget()
        brand_row.setObjectName("SidebarBrandRow")
        brand_row_layout = QHBoxLayout(brand_row)
        brand_row_layout.setContentsMargins(0, 0, 0, 0)
        brand_row_layout.setSpacing(8)
        self.brand_title = QLabel("Lora")
        self.brand_title.setObjectName("Brand")
        self.collapse_button = QToolButton()
        self.collapse_button.setObjectName("SidebarCollapseButton")
        self.collapse_button.setToolTip("Collapse history")
        self.collapse_button.setText("<")
        self.collapse_button.setAutoRaise(True)
        self.collapse_button.setCursor(Qt.PointingHandCursor)
        self.collapse_button.clicked.connect(self._toggle_history_collapsed)
        brand_row_layout.addWidget(self.brand_title, 1)
        brand_row_layout.addWidget(self.collapse_button)
        self.workspace = QLabel("")
        self.workspace.setObjectName("SidebarMeta")
        self.workspace.setWordWrap(True)
        brand_layout.addWidget(brand_row)
        brand_layout.addWidget(self.workspace)
```

Store widgets that need collapsed visibility as attributes:

```python
        self.brand_card = brand_card
        self.section_label = recent
        self.info_card = info_card
        self.tools_card = tools_card
```

Add these methods to `SessionSidebar` before `set_workspace`:

```python
    def is_collapsed(self) -> bool:
        return self._history_collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        self._history_collapsed = collapsed
        self.setProperty("collapsed", collapsed)
        self.workspace.setVisible(not collapsed)
        self.section_label.setVisible(not collapsed)
        self.session_tree.setVisible(not collapsed)
        self.info_card.setVisible(not collapsed)
        self.brand_title.setText("L" if collapsed else "Lora")
        self.collapse_button.setText(">" if collapsed else "<")
        self.collapse_button.setToolTip("Expand history" if collapsed else "Collapse history")
        self.new_button.setText("" if collapsed else "New Chat")
        self.project_button.setText("" if collapsed else "Choose Project")
        self.settings_button.setText("" if collapsed else "Settings")
        self.day_button.setText("" if collapsed else "Day")
        self.night_button.setText("" if collapsed else "Night")
        for button in (self.new_button, self.project_button, self.settings_button, self.day_button, self.night_button):
            if collapsed:
                button.setFixedSize(40, 40)
            else:
                button.setMinimumSize(0, 0)
                button.setMaximumSize(16777215, 16777215)
            button.updateGeometry()
        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(8, 12, 8, 12) if collapsed else layout.setContentsMargins(20, 20, 20, 18)
            layout.setSpacing(8 if collapsed else 10)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _toggle_history_collapsed(self) -> None:
        self.set_collapsed(not self._history_collapsed)
        self.history_collapsed_changed.emit(self._history_collapsed)
```

- [ ] **Step 4: Run tests to verify sidebar API passes**

Run:

```bash
uv run pytest tests/unit/test_gui_session_sidebar.py::GuiSessionSidebarTests::test_sidebar_can_switch_between_expanded_and_collapsed_modes tests/unit/test_gui_session_sidebar.py::GuiSessionSidebarTests::test_sidebar_collapse_button_emits_collapsed_state tests/unit/test_gui_session_sidebar.py::GuiSessionSidebarTests::test_collapsed_sidebar_keeps_primary_actions_reachable -q
```

Expected: PASS.

---

### Task 2: Layered Main Window Shell Tests

**Files:**
- Modify: `tests/unit/test_gui_main_window.py`

- [ ] **Step 1: Replace old direct layout test and add shell collapse geometry test**

Replace `test_main_layout_uses_tighter_three_pane_spacing_and_margins` with:

```python
    def test_main_layout_uses_layered_history_and_workbench_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)

            shell = window.centralWidget()
            history = shell.findChild(QWidget, "HistoryLayer")
            workbench = shell.findChild(QWidget, "WorkbenchLayer")

            self.assertEqual(shell.objectName(), "CentralShell")
            self.assertIsNotNone(history)
            self.assertIsNotNone(workbench)
            assert history is not None
            assert workbench is not None
            self.assertIs(window.sidebar.parentWidget(), history)
            self.assertIs(window.chat.parentWidget(), workbench)
            self.assertIs(window.inspector.parentWidget(), workbench)

    def test_collapsing_history_moves_workbench_left_and_keeps_chat_connected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(workspace_root=tmp)
            window.resize(1400, 820)
            window.show()
            self.app.processEvents()

            shell = window.centralWidget()
            history = shell.findChild(QWidget, "HistoryLayer")
            workbench = shell.findChild(QWidget, "WorkbenchLayer")
            assert history is not None
            assert workbench is not None
            expanded_left = workbench.geometry().left()

            window._set_history_collapsed(True)
            self.app.processEvents()

            self.assertTrue(window.sidebar.is_collapsed())
            self.assertEqual(history.geometry().width(), 64)
            self.assertLess(workbench.geometry().left(), expanded_left)
            self.assertIsNotNone(window.chat.message_submitted)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_main_layout_uses_layered_history_and_workbench_shell tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_collapsing_history_moves_workbench_left_and_keeps_chat_connected -q
```

Expected: FAIL because `HistoryLayer`, `WorkbenchLayer`, and `_set_history_collapsed` do not exist yet.

- [ ] **Step 3: Implement `_LayeredShell`**

In `src/gui/main_window.py`, update imports:

```python
from PySide6.QtCore import QRect, QThread, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QWidget
```

Add constants near the `_ActiveRun` class:

```python
_SHELL_MARGIN = 16
_HISTORY_EXPANDED_WIDTH = 320
_HISTORY_COLLAPSED_WIDTH = 64
_WORKBENCH_GAP = 14
_INSPECTOR_TARGET_WIDTH = 420
_INSPECTOR_MIN_WIDTH = 340
_CHAT_MIN_WIDTH = 560
```

Add `_LayeredShell` before `MainWindow`:

```python
class _LayeredShell(QWidget):
    def __init__(self, sidebar: SessionSidebar, chat: ChatPane, inspector: TraceInspector, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("CentralShell")
        self._history_collapsed = False

        self.history_layer = QFrame(self)
        self.history_layer.setObjectName("HistoryLayer")
        self.history_layer.setAttribute(Qt.WA_StyledBackground, True)
        history_layout = QHBoxLayout(self.history_layer)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(0)
        history_layout.addWidget(sidebar)

        self.workbench_layer = QFrame(self)
        self.workbench_layer.setObjectName("WorkbenchLayer")
        self.workbench_layer.setAttribute(Qt.WA_StyledBackground, True)
        workbench_layout = QHBoxLayout(self.workbench_layer)
        workbench_layout.setContentsMargins(0, 0, 0, 0)
        workbench_layout.setSpacing(_WORKBENCH_GAP)
        workbench_layout.addWidget(chat, 1)
        workbench_layout.addWidget(inspector)

        inspector.setMinimumWidth(_INSPECTOR_MIN_WIDTH)
        inspector.setMaximumWidth(_INSPECTOR_TARGET_WIDTH)
        self.inspector = inspector
        self._apply_layer_geometry()

    def set_history_collapsed(self, collapsed: bool) -> None:
        self._history_collapsed = collapsed
        self._apply_layer_geometry()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        super().resizeEvent(event)
        self._apply_layer_geometry()

    def _apply_layer_geometry(self) -> None:
        rect = self.rect()
        height = max(0, rect.height() - (_SHELL_MARGIN * 2))
        history_width = _HISTORY_COLLAPSED_WIDTH if self._history_collapsed else _HISTORY_EXPANDED_WIDTH
        history_rect = QRect(_SHELL_MARGIN, _SHELL_MARGIN, history_width, height)
        self.history_layer.setGeometry(history_rect)

        workbench_left = _SHELL_MARGIN + history_width + _WORKBENCH_GAP
        workbench_width = max(0, rect.width() - workbench_left - _SHELL_MARGIN)
        self.workbench_layer.setGeometry(QRect(workbench_left, _SHELL_MARGIN, workbench_width, height))

        available_for_inspector = workbench_width - _CHAT_MIN_WIDTH - _WORKBENCH_GAP
        inspector_width = max(_INSPECTOR_MIN_WIDTH, min(_INSPECTOR_TARGET_WIDTH, available_for_inspector))
        self.inspector.setFixedWidth(inspector_width)
        self.history_layer.lower()
        self.workbench_layer.raise_()
```

Also add `Qt` to the `PySide6.QtCore` import if not already included:

```python
from PySide6.QtCore import QRect, Qt, QThread, QTimer
```

- [ ] **Step 4: Replace direct shell construction in `MainWindow.__init__`**

Replace:

```python
        shell = QWidget()
        shell.setObjectName("CentralShell")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        self.sidebar = SessionSidebar()
        self.chat = ChatPane()
        self.inspector = TraceInspector()
        self.sidebar.setFixedWidth(360)
        self.inspector.setFixedWidth(420)
        layout.addWidget(self.sidebar)
        layout.addWidget(self.chat, 1)
        layout.addWidget(self.inspector)
        self.setCentralWidget(shell)
```

with:

```python
        self.sidebar = SessionSidebar()
        self.chat = ChatPane()
        self.inspector = TraceInspector()
        self.shell = _LayeredShell(self.sidebar, self.chat, self.inspector)
        self.setCentralWidget(self.shell)
```

After sidebar signal wiring, add:

```python
        self.sidebar.history_collapsed_changed.connect(self._set_history_collapsed)
```

Add the method to `MainWindow` near `set_theme`:

```python
    def _set_history_collapsed(self, collapsed: bool) -> None:
        self.sidebar.set_collapsed(collapsed)
        self.shell.set_history_collapsed(collapsed)
```

- [ ] **Step 5: Run tests to verify shell behavior passes**

Run:

```bash
uv run pytest tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_main_layout_uses_layered_history_and_workbench_shell tests/unit/test_gui_main_window.py::GuiMainWindowTests::test_collapsing_history_moves_workbench_left_and_keeps_chat_connected -q
```

Expected: PASS.

---

### Task 3: Theme Selector Tests And QSS

**Files:**
- Modify: `tests/unit/test_gui_theme.py`
- Modify: `src/gui/theme.py`

- [ ] **Step 1: Write failing theme selector test**

Add this test inside `GuiThemeTests`:

```python
    def test_layered_history_drawer_styles_are_defined(self) -> None:
        css = theme_stylesheet("day")

        self.assertIn("#HistoryLayer", css)
        self.assertIn("#WorkbenchLayer", css)
        self.assertIn("#SidebarCollapseButton", css)
        self.assertIn('#SessionSidebar[collapsed="true"]', css)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/unit/test_gui_theme.py::GuiThemeTests::test_layered_history_drawer_styles_are_defined -q
```

Expected: FAIL because the new selectors are absent.

- [ ] **Step 3: Add layered QSS selectors**

In `src/gui/theme.py`, near the existing shell and pane selectors, replace:

```css
#SessionSidebar, #ChatPane, #TraceInspector {
    background: {c.glass};
    border: 1px solid {c.border};
    border-radius: {r.pane}px;
}
#SessionSidebar {{
    background: {c.glass_strong};
}}
```

with:

```css
#HistoryLayer {{
    background: {c.glass};
    border: 1px solid {c.border};
    border-radius: {r.pane}px;
}}
#WorkbenchLayer {{
    background: {c.glass_strong};
    border: 1px solid {c.border_strong};
    border-radius: {r.pane}px;
}}
#SessionSidebar, #ChatPane, #TraceInspector {{
    background: {c.glass};
    border: 1px solid {c.border};
    border-radius: {r.pane}px;
}}
#HistoryLayer #SessionSidebar {{
    background: transparent;
    border: 0;
    border-radius: {r.pane}px;
}}
#SessionSidebar[collapsed="true"] {{
    background: transparent;
    border: 0;
}}
#WorkbenchLayer #ChatPane, #WorkbenchLayer #TraceInspector {{
    background: {c.glass};
    border: 1px solid {c.border};
    border-radius: {r.pane}px;
}}
```

Add collapse button styling near the sidebar button selectors:

```css
#SidebarCollapseButton {{
    background: {pill_fill};
    border: 1px solid {c.border};
    border-radius: {r.chip}px;
    color: {c.text_muted};
    font-weight: 700;
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    padding: 0;
}}
#SidebarCollapseButton:hover {{
    background: {hover_fill};
    color: {c.text};
    border-color: {c.border_strong};
}}
#SessionSidebar[collapsed="true"] #Brand {{
    font-size: {_font_size_css(t.title_size - 4)};
}}
```

- [ ] **Step 4: Run theme selector test**

Run:

```bash
uv run pytest tests/unit/test_gui_theme.py::GuiThemeTests::test_layered_history_drawer_styles_are_defined -q
```

Expected: PASS.

---

### Task 4: Focused Regression Test Pass

**Files:**
- No new files.

- [ ] **Step 1: Run focused GUI layout/sidebar/theme tests**

Run:

```bash
uv run pytest tests/unit/test_gui_main_window.py tests/unit/test_gui_session_sidebar.py tests/unit/test_gui_theme.py -q
```

Expected: PASS.

- [ ] **Step 2: Fix regressions if focused tests fail**

If `test_session_list_stays_within_viewport_width` fails, ensure `SessionSidebar.set_collapsed(False)` restores margins and calls `_update_scroll_content()`:

```python
        self._update_scroll_content()
```

If geometry tests fail because `_LayeredShell` has not received a non-zero size before assertions, call `_apply_layer_geometry()` after `self.setCentralWidget(self.shell)` through:

```python
        QTimer.singleShot(0, self.shell._apply_layer_geometry)
```

Then rerun the focused command:

```bash
uv run pytest tests/unit/test_gui_main_window.py tests/unit/test_gui_session_sidebar.py tests/unit/test_gui_theme.py -q
```

Expected: PASS.

---

### Task 5: Broader GUI Verification

**Files:**
- No new files.

- [ ] **Step 1: Run broader GUI unit tests**

Run:

```bash
uv run pytest tests/unit/test_gui_main_window.py tests/unit/test_gui_session_sidebar.py tests/unit/test_gui_theme.py tests/unit/test_gui_inspector.py tests/unit/test_gui_chat_widget.py -q
```

Expected: PASS.

- [ ] **Step 2: Inspect final diff**

Run:

```bash
git diff -- src/gui/main_window.py src/gui/widgets/sessions.py src/gui/theme.py tests/unit/test_gui_main_window.py tests/unit/test_gui_session_sidebar.py tests/unit/test_gui_theme.py docs/planning/superpowers/plans/2026-06-16-layered-history-drawer-layout.md
```

Expected: Diff only covers the layered history drawer plan, implementation, and focused tests.

---

## Self-Review Checklist

- Spec coverage:
  - Background history layer: Task 2 and Task 3.
  - Collapsible history rail: Task 1 and Task 2.
  - Foreground chat plus inspector workbench: Task 2 and Task 3.
  - Existing session and inspector behavior preserved: Task 4 and Task 5.
  - Theme selectors and visual hierarchy: Task 3.

- Red-flag scan:
  - No task uses reserved marker words or unspecified follow-up work.

- Type consistency:
  - `SessionSidebar.set_collapsed(bool)` and `SessionSidebar.is_collapsed()` are introduced in Task 1 and used in Task 2.
  - `SessionSidebar.history_collapsed_changed` emits `bool` and connects to `MainWindow._set_history_collapsed`.
  - `_LayeredShell.set_history_collapsed(bool)` receives the same boolean state.
