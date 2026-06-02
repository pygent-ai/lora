from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMainWindow, QMessageBox, QWidget

from gui.session_model import ChatSessionStore
from gui.theme import theme_stylesheet
from gui.widgets.chat import ChatPane
from gui.widgets.inspector import TraceInspector
from gui.widgets.sessions import SessionSidebar
from gui.widgets.settings import SettingsDialog
from gui.workers import ChatTurnWorker, InspectorEvent
from lora.config import load_run_config
from lora.schema import RunConfig
from lora.session import SessionManager


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        workspace_root: str | None = None,
        config_path: str | None = None,
        agent_alias: str | None = None,
        model: str | None = None,
        max_steps: int | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Lora")
        self._thread: QThread | None = None
        self._worker: ChatTurnWorker | None = None
        self._active_session_id: str | None = None
        self._theme = "day"
        self._turn_index = 1
        self._config_path = config_path
        self.config = self._load_config(workspace_root, config_path, agent_alias, model, max_steps)
        self.manager = SessionManager(self.config)
        self.store = ChatSessionStore(self.manager)

        shell = QWidget()
        shell.setObjectName("CentralShell")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        self.sidebar = SessionSidebar()
        self.chat = ChatPane()
        self.inspector = TraceInspector()
        self.sidebar.setFixedWidth(350)
        self.inspector.setFixedWidth(390)
        layout.addWidget(self.sidebar)
        layout.addWidget(self.chat, 1)
        layout.addWidget(self.inspector)
        self.setCentralWidget(shell)

        self.sidebar.new_chat_requested.connect(self.create_chat)
        self.sidebar.session_selected.connect(self.select_session)
        self.sidebar.session_delete_requested.connect(self.delete_session)
        self.sidebar.settings_requested.connect(self.open_settings)
        self.sidebar.theme_toggle_requested.connect(self.toggle_theme)
        self.sidebar.theme_selected.connect(self.set_theme)
        self.chat.message_submitted.connect(self.send_message)

        self._refresh_static_ui()
        self.refresh_sessions()
        self._select_initial_session()

    def create_chat(self) -> None:
        record = self.store.create_chat_session()
        self.refresh_sessions()
        self.select_session(record.session_id)

    def select_session(self, session_id: str) -> None:
        self._active_session_id = session_id
        session = self.manager.load(session_id)
        self.sidebar.select_session(session_id)
        self.chat.set_session_context(session_id, agent=self.config.agent_alias, model=self.config.model_name)
        self.chat.render_history(session.history)
        self.inspector.reset_context(session_id=session_id)
        self._load_last_run_trace(session_id, session.metadata)
        self._turn_index = _next_turn_index(session.history)

    def refresh_sessions(self) -> None:
        self.sidebar.set_sessions(self.store.list_chat_sessions())

    def _select_initial_session(self) -> None:
        records = self.store.list_chat_sessions()
        if records:
            self.select_session(records[0].session_id)

    def delete_session(self, session_id: str, *, confirm: bool = True) -> None:
        if confirm and not self._confirm_delete_session(session_id):
            return
        was_active = session_id == self._active_session_id
        self.store.delete_chat_session(session_id)
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
        self.chat.add_message("user", message)
        self.chat.start_assistant_message()
        self.chat.set_running(True)
        self.inspector.set_status("Running")

        thread = QThread(self)
        worker = ChatTurnWorker(
            config=self.config,
            manager=self.manager,
            session_id=self._active_session_id,
            user_input=message,
            turn_index=self._turn_index,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.run_started.connect(self._on_run_started)
        worker.assistant_delta.connect(self.chat.append_assistant_delta)
        worker.runtime_event.connect(self._on_runtime_event)
        worker.completed.connect(self._on_turn_completed)
        worker.failed.connect(self._on_turn_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_worker_refs)
        self._thread = thread
        self._worker = worker
        thread.start()

    def open_settings(self) -> None:
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
            QMessageBox.critical(self, "Settings error", str(exc))
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

    def _refresh_static_ui(self) -> None:
        self.sidebar.set_workspace(_short_path(self.config.workspace_root))
        self.sidebar.set_agent_status(self.config.agent_alias, self.config.model_name)
        self.sidebar.set_theme_name(self._theme)
        self.inspector.set_config(
            {
                "workspace": self.config.workspace_root,
                "lora_root": self.config.lora_root,
                "agent": self.config.agent_alias,
                "model": self.config.model_name,
                "api_key_source": self.config.api_key_source,
                "base_url": self.config.base_url,
                "max_steps": self.config.max_steps,
            }
        )

    def _on_run_started(self, run_ref: object) -> None:
        case_run_id = getattr(run_ref, "case_run_id", "")
        self.inspector.reset_context(session_id=self._active_session_id, case_run_id=case_run_id)
        self.inspector.set_status("Running")

    def _on_runtime_event(self, event: InspectorEvent) -> None:
        self.inspector.add_event(event)
        self.chat.add_runtime_event(event)

    def _on_turn_completed(self, result: object) -> None:
        payload = result if isinstance(result, dict) else {}
        self.chat.finish_assistant_message(str(payload.get("final_answer") or ""))
        self.chat.set_running(False)
        self.chat.set_run_status(str(payload.get("status") or "Finished"))
        self.inspector.set_status(str(payload.get("status") or "Finished"))
        run_dir = payload.get("run_dir")
        if run_dir:
            self.inspector.load_trace_events_from_run_dir(str(run_dir))
        self.refresh_sessions()
        self._turn_index += 1

    def _on_turn_failed(self, error: str) -> None:
        self.chat.finish_assistant_message()
        self.chat.show_error(error)
        self.chat.set_running(False)
        self.inspector.set_status("Error")
        self.inspector.add_event(InspectorEvent(kind="event", title="Runtime error", detail=error, tone="error"))

    def _clear_worker_refs(self) -> None:
        self._thread = None
        self._worker = None

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
        result = QMessageBox.question(
            self,
            "Delete session",
            f"Delete session {session_id}?\n\nThis removes the saved chat and local run artifacts.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return result == QMessageBox.Yes


def _short_path(path: str) -> str:
    resolved = Path(path)
    return str(resolved) if len(str(resolved)) <= 34 else f"...{str(resolved)[-31:]}"


def _next_turn_index(history: list[dict[str, object]]) -> int:
    user_count = sum(1 for message in history if message.get("role") == "user")
    return user_count + 1
