from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QWidget

from gui.session_model import ChatSessionStore, GuiProjectState, SessionGroupRecord, SessionScope, build_session_scopes
from gui.theme import theme_stylesheet
from gui.widgets.chat import ChatPane
from gui.widgets.inspector import TraceInspector
from gui.widgets.sessions import SessionSidebar
from gui.widgets.settings import SettingsDialog
from gui.workers import ChatTurnWorker, InspectorEvent
from lora.config import load_run_config
from lora.schema import RunConfig
from lora.session import SessionManager


@dataclass(slots=True)
class _LiveUpdate:
    kind: Literal["assistant_delta", "runtime_event"]
    payload: str | InspectorEvent


@dataclass(slots=True)
class _ActiveRun:
    session_id: str
    user_input: str
    turn_index: int
    thread: QThread | None
    worker: ChatTurnWorker | None
    updates: list[_LiveUpdate] = field(default_factory=list)
    run_ref: object | None = None
    status: str = "running"
    error: str | None = None


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        workspace_root: str | None = None,
        config_path: str | None = None,
        agent_alias: str | None = None,
        model: str | None = None,
        max_steps: int | None = None,
        state_path: str | Path | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Lora")
        self._running_sessions: dict[str, _ActiveRun] = {}
        self._active_session_id: str | None = None
        self._theme = "day"
        self._turn_index = 1
        self._config_path = config_path
        self._agent_alias_override = agent_alias
        self._model_override = model
        self._max_steps_override = max_steps
        self.project_state = GuiProjectState.load(state_path)
        if workspace_root is not None:
            self._remember_project_path(workspace_root, persist=state_path is not None)
        self.scopes = build_session_scopes(self.project_state)
        self._active_scope_id = self._initial_scope_id(workspace_root)
        self.current_scope = self._scope_by_id(self._active_scope_id)
        self.config = self._config_for_scope(self.current_scope)
        self.manager = SessionManager(self.config)
        self.store = ChatSessionStore(self.manager)

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

        self.sidebar.new_chat_requested.connect(self.create_chat)
        self.sidebar.session_selected.connect(self.select_session)
        self.sidebar.session_delete_requested.connect(self.delete_session)
        self.sidebar.settings_requested.connect(self.open_settings)
        self.sidebar.group_order_changed.connect(self._remember_group_order)
        self.sidebar.group_collapsed_changed.connect(self._remember_group_collapsed)
        self.sidebar.project_remove_requested.connect(self.remove_project)
        self.sidebar.project_select_requested.connect(self.choose_project_path)
        self.sidebar.theme_toggle_requested.connect(self.toggle_theme)
        self.sidebar.theme_selected.connect(self.set_theme)
        self.chat.message_submitted.connect(self.send_message)

        self._project_state_save_timer = QTimer(self)
        self._project_state_save_timer.setSingleShot(True)
        self._project_state_save_timer.setInterval(400)
        self._project_state_save_timer.timeout.connect(self.project_state.save)

        self._refresh_static_ui()
        self.refresh_sessions()
        self._select_initial_session()

    def create_chat(self) -> None:
        record = self.store.create_chat_session()
        self.project_state.set_scope_collapsed(self._active_scope_id, False)
        self.refresh_sessions()
        self.select_session(self._active_scope_id, record.session_id)

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

    def refresh_sessions(self) -> None:
        self.sidebar.set_session_groups(
            self._build_session_groups(),
            active_scope_id=self._active_scope_id,
            active_session_id=self._active_session_id,
        )

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

    def choose_project_path(self) -> None:
        if self._has_running_sessions():
            self._show_warning("Runs in progress", "Wait for running sessions to finish before changing projects.")
            return
        selected = QFileDialog.getExistingDirectory(self, "Choose project folder", self.config.workspace_root)
        if selected:
            self.set_project_path(selected)

    def set_project_path(self, project_path: str) -> None:
        if self._has_running_sessions():
            self._show_warning("Runs in progress", "Wait for running sessions to finish before changing projects.")
            return
        resolved = str(Path(project_path).expanduser().resolve())
        self.project_state.remember_project(resolved)
        self.scopes = build_session_scopes(self.project_state)
        self.select_scope(f"project:{resolved}")

    def remove_project(self, scope_id: str, *, confirm: bool = True) -> None:
        if not scope_id.startswith("project:"):
            return
        project_path = scope_id.removeprefix("project:")
        project_scope = self._scope_by_id(scope_id)
        if project_scope.workspace_root is None:
            return
        project_store = ChatSessionStore(SessionManager(self._config_for_scope(project_scope)))
        project_session_ids = {record.session_id for record in project_store.list_chat_sessions()}
        if project_session_ids & self._running_sessions.keys():
            self._show_warning(
                "Runs in progress",
                "Wait for running sessions in this project to finish before removing it.",
            )
            return
        if confirm and not self._confirm_remove_project(project_path):
            return
        was_active = self._active_scope_id == scope_id
        self.project_state.forget_project(project_path)
        self.scopes = build_session_scopes(self.project_state)
        if was_active:
            self._switch_active_scope(self._scope_after_project_removed())
            self._active_session_id = None
            self._turn_index = 1
            self.chat.clear()
            self.chat.set_session_title("Select or create a chat session")
            self.inspector.reset_context()
        self.refresh_sessions()
        if was_active:
            self._select_initial_session()

    def _scope_after_project_removed(self) -> str:
        if self.project_state.default_project_path:
            return f"project:{Path(self.project_state.default_project_path).expanduser().resolve()}"
        return "conversation"

    def _select_initial_session(self) -> None:
        records = self.store.list_chat_sessions()
        if records:
            self.select_session(self._active_scope_id, records[0].session_id)

    def delete_session(self, scope_id: str, session_id: str | None = None, *, confirm: bool = True) -> None:
        if session_id is None:
            session_id = scope_id
            scope_id = self._active_scope_id
        if session_id in self._running_sessions:
            self._show_warning(
                "Session is running",
                "This session is still running. Wait for it to finish before deleting it.",
            )
            return
        if confirm and not self._confirm_delete_session(session_id):
            return
        was_active = session_id == self._active_session_id
        store = ChatSessionStore(SessionManager(self._config_for_scope(self._scope_by_id(scope_id))))
        store.delete_chat_session(session_id)
        if was_active:
            self._active_session_id = None
            self._turn_index = 1
            self.chat.clear()
            self.chat.set_session_title("Select or create a chat session")
            self.inspector.reset_context()
        self.refresh_sessions()

    def toggle_theme(self) -> None:
        self.set_theme("night" if self._theme == "day" else "day")

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme_stylesheet(self._theme))
        self.sidebar.set_theme_name(self._theme)

    def send_message(self, message: str) -> None:
        if self._active_session_id is None:
            self.create_chat()
        assert self._active_session_id is not None
        session_id = self._active_session_id
        if session_id in self._running_sessions:
            self.chat.set_run_status("running")
            self.inspector.set_status("Running")
            return
        self.chat.add_message("user", message)
        self.chat.start_assistant_message()
        self.chat.set_running(True)
        self.inspector.set_status("Running")
        self._start_chat_worker(session_id, message, self._turn_index)
        self.refresh_sessions()

    def _start_chat_worker(self, session_id: str, message: str, turn_index: int) -> _ActiveRun:
        thread = QThread(self)
        worker = ChatTurnWorker(
            config=self.config,
            manager=self.manager,
            session_id=session_id,
            user_input=message,
            turn_index=turn_index,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.run_started.connect(self._on_run_started)
        worker.assistant_delta.connect(self._on_assistant_delta)
        worker.runtime_event.connect(self._on_runtime_event)
        worker.completed.connect(self._on_turn_completed)
        worker.failed.connect(self._on_turn_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda session_id=session_id: self._clear_worker_refs(session_id))
        active_run = self._new_active_run(
            session_id=session_id,
            user_input=message,
            turn_index=turn_index,
            thread=thread,
            worker=worker,
        )
        self._running_sessions[session_id] = active_run
        thread.start()
        return active_run

    def open_settings(self) -> None:
        if self._has_running_sessions():
            self._show_warning("Runs in progress", "Wait for running sessions to finish before changing runtime settings.")
            return
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() != dialog.Accepted:
            return
        values = dialog.values()
        try:
            self.config = self._load_config(
                values.workspace_root or None,
                values.config_path or None,
                values.agent_alias or None,
                values.model or None,
                values.max_steps,
            )
        except Exception as exc:  # noqa: BLE001 - GUI boundary shows readable errors.
            self._show_critical("Settings error", str(exc))
            return
        self.manager = SessionManager(self.config)
        self.store = ChatSessionStore(self.manager)
        self._active_session_id = None
        self.chat.clear()
        self.chat.set_session_title("Select or create a chat session")
        self.inspector.reset_context()
        self._refresh_static_ui()
        self.refresh_sessions()

    def _load_config(
        self,
        workspace_root: str | None,
        config_path: str | None,
        agent_alias: str | None,
        model: str | None,
        max_steps: int | None,
    ) -> RunConfig:
        return load_run_config(
            workspace_root=workspace_root,
            config_file=config_path,
            agent_alias=agent_alias,
            model=model,
            max_steps=max_steps,
        )

    def _config_for_scope(self, scope: SessionScope) -> RunConfig:
        if scope.workspace_root is None:
            return RunConfig(
                workspace_root=scope.runtime_workspace_root,
                lora_root=scope.lora_root,
                agent_alias=self._agent_alias_override or "default",
                model=self._model_override,
                max_steps=self._max_steps_override if self._max_steps_override is not None else -1,
            )
        return self._load_config(
            scope.workspace_root,
            self._config_path,
            self._agent_alias_override,
            self._model_override,
            self._max_steps_override,
        )

    def _switch_active_scope(self, scope_id: str) -> None:
        self._active_scope_id = scope_id
        self.current_scope = self._scope_by_id(scope_id)
        self.config = self._config_for_scope(self.current_scope)
        self.manager = SessionManager(self.config)
        self.store = ChatSessionStore(self.manager)
        self._refresh_static_ui()

    def _remember_group_order(self, scope_ids: list[str]) -> None:
        self.project_state.remember_scope_order(scope_ids, persist=False)
        self.scopes = build_session_scopes(self.project_state)
        self.sidebar.apply_group_order(scope_ids)
        self._schedule_project_state_save()

    def _remember_group_collapsed(self, scope_id: str, collapsed: bool) -> None:
        self.project_state.set_scope_collapsed(scope_id, collapsed, persist=False)
        self._schedule_project_state_save()

    def _schedule_project_state_save(self) -> None:
        self._project_state_save_timer.start()

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

    def _remember_project_path(self, project_path: str | Path, *, persist: bool) -> None:
        resolved = str(Path(project_path).expanduser().resolve())
        if persist:
            self.project_state.remember_project(resolved)
            return
        recent = [resolved]
        for item in self.project_state.recent_project_paths or []:
            normalized = str(Path(item).expanduser().resolve())
            if normalized != resolved:
                recent.append(normalized)
        self.project_state.default_project_path = resolved
        self.project_state.recent_project_paths = recent[:12]

    def _initial_scope_id(self, workspace_root: str | None) -> str:
        if workspace_root is not None:
            return f"project:{Path(workspace_root).expanduser().resolve()}"
        if self.project_state.default_project_path:
            return f"project:{Path(self.project_state.default_project_path).expanduser().resolve()}"
        return "conversation"

    def _scope_by_id(self, scope_id: str) -> SessionScope:
        for scope in self.scopes:
            if scope.scope_id == scope_id:
                return scope
        return self.scopes[0]

    def _new_active_run(
        self,
        *,
        session_id: str,
        user_input: str,
        turn_index: int,
        thread: QThread | None,
        worker: ChatTurnWorker | None,
    ) -> _ActiveRun:
        return _ActiveRun(
            session_id=session_id,
            user_input=user_input,
            turn_index=turn_index,
            thread=thread,
            worker=worker,
        )

    def _render_running_session(self, active_run: _ActiveRun) -> None:
        case_run_id = getattr(active_run.run_ref, "case_run_id", "") if active_run.run_ref is not None else ""
        self.inspector.reset_context(session_id=active_run.session_id, case_run_id=case_run_id)
        self.chat.add_message("user", active_run.user_input)
        self.chat.start_assistant_message()
        for update in active_run.updates:
            if update.kind == "assistant_delta":
                self.chat.append_assistant_delta(str(update.payload))
            elif isinstance(update.payload, InspectorEvent):
                self.inspector.add_event(update.payload)
                self.chat.add_runtime_event(update.payload)
        self.chat.set_running(True)
        self.chat.set_run_status(active_run.status)
        self.inspector.set_status(active_run.status)

    def _on_run_started(self, session_id: str, run_ref: object) -> None:
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            active_run.run_ref = run_ref
            active_run.status = "running"
        if session_id == self._active_session_id:
            case_run_id = getattr(run_ref, "case_run_id", "")
            self.inspector.reset_context(session_id=session_id, case_run_id=case_run_id)
            self.inspector.set_status("Running")
        self.refresh_sessions()

    def _on_assistant_delta(self, session_id: str, delta: str) -> None:
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            active_run.updates.append(_LiveUpdate("assistant_delta", delta))
        if session_id == self._active_session_id:
            self.chat.append_assistant_delta(delta)

    def _on_runtime_event(self, session_id: str, event: InspectorEvent) -> None:
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            active_run.updates.append(_LiveUpdate("runtime_event", event))
        if session_id == self._active_session_id:
            self.inspector.add_event(event)
            self.chat.add_runtime_event(event)

    def _on_turn_completed(self, session_id: str, result: object) -> None:
        payload = result if isinstance(result, dict) else {}
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            active_run.status = str(payload.get("status") or "Finished")
        if session_id == self._active_session_id:
            self.chat.finish_assistant_message(str(payload.get("final_answer") or ""))
            self.chat.set_running(False)
            self.chat.set_run_status(str(payload.get("status") or "Finished"))
            self.inspector.set_status(str(payload.get("status") or "Finished"))
            run_dir = payload.get("run_dir")
            if run_dir:
                self.inspector.load_trace_events_from_run_dir(str(run_dir))
            self._turn_index += 1
        self._running_sessions.pop(session_id, None)
        self.refresh_sessions()

    def _on_turn_failed(self, session_id: str, error: str) -> None:
        active_run = self._running_sessions.get(session_id)
        if active_run is not None:
            active_run.status = "error"
            active_run.error = error
        if session_id == self._active_session_id:
            self.chat.finish_assistant_message()
            self.chat.show_error(error)
            self.chat.set_running(False)
            self.inspector.set_status("Error")
            self.inspector.add_event(InspectorEvent(kind="event", title="Runtime error", detail=error, tone="error"))
        self._running_sessions.pop(session_id, None)
        self.refresh_sessions()

    def _clear_worker_refs(self, session_id: str) -> None:
        active_run = self._running_sessions.get(session_id)
        if active_run is None:
            return
        active_run.thread = None
        active_run.worker = None

    def _has_running_sessions(self) -> bool:
        return bool(self._running_sessions)

    def _show_warning(self, title: str, message: str) -> None:
        dialog = self._build_debug_message_box("warning", title, message, buttons=("ok",), default="ok")
        dialog.exec()

    def _show_critical(self, title: str, message: str) -> None:
        dialog = self._build_debug_message_box("critical", title, message, buttons=("ok",), default="ok")
        dialog.exec()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        if not self._has_running_sessions():
            super().closeEvent(event)
            return
        result = self._build_debug_message_box(
            "question",
            "Runs in progress",
            "There are running chat sessions. Closing now will stop them. Keep Lora open?",
            buttons=("yes", "no"),
            default="yes",
        )
        result = result.exec()
        if result == QMessageBox.Yes:
            event.ignore()
            return
        super().closeEvent(event)

    def _load_last_run_trace(self, session_id: str, metadata: dict[str, object]) -> None:
        case_run_id = metadata.get("last_case_run_id")
        if not isinstance(case_run_id, str) or not case_run_id:
            return
        try:
            run_ref = self.manager.find_case_run(session_id, case_run_id)
        except ValueError:
            return
        self.inspector.reset_context(session_id=session_id, case_run_id=run_ref.case_run_id)
        self.inspector.load_trace_events_from_run_dir(run_ref.run_dir)

    def _confirm_delete_session(self, session_id: str) -> bool:
        dialog = self._build_debug_message_box(
            "question",
            "Delete session",
            f"Delete session {session_id}?\n\nThis removes the saved chat and local run artifacts.",
            buttons=("yes", "no"),
            default="no",
        )
        result = dialog.exec()
        return result == QMessageBox.Yes

    def _confirm_remove_project(self, project_path: str) -> bool:
        label = Path(project_path).name or project_path
        dialog = self._build_debug_message_box(
            "question",
            "Remove project",
            f"Remove {label} from the sidebar?\n\nProject files are kept on disk. Choose the folder again to add it back.",
            buttons=("yes", "no"),
            default="no",
        )
        result = dialog.exec()
        return result == QMessageBox.Yes

    def _build_debug_message_box(
        self,
        kind: Literal["warning", "critical", "question"],
        title: str,
        message: str,
        *,
        buttons: tuple[str, ...],
        default: str,
    ) -> QMessageBox:
        icon_map = {
            "warning": QMessageBox.Warning,
            "critical": QMessageBox.Critical,
            "question": QMessageBox.Question,
        }
        button_map = {
            "ok": QMessageBox.Ok,
            "yes": QMessageBox.Yes,
            "no": QMessageBox.No,
            "cancel": QMessageBox.Cancel,
        }
        prefix_map = {
            "warning": "WarningDialog",
            "critical": "CriticalDialog",
            "question": "QuestionDialog",
        }
        dialog = QMessageBox(self)
        dialog.setIcon(icon_map[kind])
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setObjectName(prefix_map[kind])
        standard_buttons = QMessageBox.StandardButton.NoButton
        for button in buttons:
            standard_buttons |= button_map[button]
        dialog.setStandardButtons(standard_buttons)
        dialog.setDefaultButton(button_map[default])
        self._apply_message_box_debug_names(dialog, prefix_map[kind], buttons)
        return dialog

    def _apply_message_box_debug_names(self, dialog: QMessageBox, prefix: str, buttons: tuple[str, ...]) -> None:
        for label in dialog.findChildren(QLabel):
            if label.text() == dialog.text():
                label.setObjectName(f"{prefix}Text")
            elif label.text() == dialog.informativeText():
                label.setObjectName(f"{prefix}InformativeText")
        button_map = {
            "ok": QMessageBox.Ok,
            "yes": QMessageBox.Yes,
            "no": QMessageBox.No,
            "cancel": QMessageBox.Cancel,
        }
        for button in buttons:
            widget = dialog.button(button_map[button])
            if widget is not None:
                widget.setObjectName(f"{prefix}{button.capitalize()}Button")


def _short_path(path: str) -> str:
    resolved = Path(path)
    return str(resolved) if len(str(resolved)) <= 34 else f"...{str(resolved)[-31:]}"


def _next_turn_index(history: list[dict[str, object]]) -> int:
    user_count = sum(1 for message in history if message.get("role") == "user")
    return user_count + 1
