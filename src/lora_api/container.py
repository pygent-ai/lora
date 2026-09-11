from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

from lora.config import load_run_config
from lora.schema import RunConfig
from lora.sessions import SessionManager

from .project_state import GuiProjectState, SessionScope

if TYPE_CHECKING:
    from lora.automations import AutomationScheduler, AutomationService, AutomationStore
    from lora.orchestration import (
        RuntimeLease,
        SessionCollaborationService,
        SessionExecutionCoordinator,
        SessionTurnService,
        WorkspaceRuntimePool,
    )
    from lora.runtime.reminders import ReminderService


@dataclass(slots=True)
class ApiContext:
    """Application container shared by the HTTP composition root."""

    workspace_root: str | None = None
    agent_alias: str | None = None
    max_steps: int | None = None
    context_window: int | None = None
    state_path: str | None = None
    _config: RunConfig | None = None
    _manager: SessionManager | None = None
    _project_state: GuiProjectState | None = None
    _reminders: ReminderService | None = None
    _chat_registry: Any | None = None
    _session_coordinator: SessionExecutionCoordinator | None = None
    _session_turns: SessionTurnService | None = None
    _session_collaboration: SessionCollaborationService | None = None
    _runtime_pool: WorkspaceRuntimePool | None = None
    _terminal_service: Any | None = None
    _automation_store: AutomationStore | None = None
    _automation_service: AutomationService | None = None
    _automation_scheduler: AutomationScheduler | None = None
    _lock: RLock = field(default_factory=RLock)

    @property
    def config(self) -> RunConfig:
        with self._lock:
            if self._config is None:
                self._config = self._load_config()
            return self._config

    @property
    def manager(self) -> SessionManager:
        with self._lock:
            if self._manager is None:
                self._manager = SessionManager(self.config)
            return self._manager

    @property
    def project_state(self) -> GuiProjectState:
        with self._lock:
            if self._project_state is None:
                self._project_state = GuiProjectState.load(self.state_path)
            return self._project_state

    @property
    def reminders(self) -> ReminderService:
        with self._lock:
            if self._reminders is None:
                from lora.runtime.reminders import ReminderService

                self._reminders = ReminderService(self.config)
            return self._reminders

    @property
    def chat_registry(self) -> Any:
        with self._lock:
            if self._chat_registry is None:
                raise RuntimeError("chat registry has not been configured")
            return self._chat_registry

    @property
    def session_coordinator(self) -> SessionExecutionCoordinator:
        with self._lock:
            if self._session_coordinator is None:
                from lora.orchestration import SessionExecutionCoordinator

                self._session_coordinator = SessionExecutionCoordinator()
            return self._session_coordinator

    @property
    def runtime_pool(self) -> WorkspaceRuntimePool:
        with self._lock:
            if self._runtime_pool is None:
                from lora.orchestration import WorkspaceRuntimePool

                self._runtime_pool = WorkspaceRuntimePool()
            return self._runtime_pool

    @property
    def session_collaboration(self) -> SessionCollaborationService:
        with self._lock:
            if self._session_collaboration is None:
                from lora.orchestration import (
                    SessionCollaborationService,
                    SessionTurnService,
                )

                self._session_turns = SessionTurnService(
                    coordinator=self.session_coordinator,
                    acquire_runtime=self.acquire_runtime,
                )
                self._session_collaboration = SessionCollaborationService(
                    turns=self._session_turns
                )
                self.runtime_pool.attach_collaboration(self._session_collaboration)
            return self._session_collaboration

    async def acquire_runtime(
        self,
        *,
        config: RunConfig | None = None,
        manager: SessionManager | None = None,
    ) -> RuntimeLease:
        _ = self.session_collaboration
        effective_config = config or self.config
        return await self.runtime_pool.acquire(
            config=effective_config,
            manager=manager,
        )

    @property
    def terminal_service(self) -> Any:
        with self._lock:
            if self._terminal_service is None:
                from lora_api.services.terminal_service import TerminalService

                self._terminal_service = TerminalService()
            return self._terminal_service

    @property
    def automation_store(self) -> AutomationStore:
        with self._lock:
            if self._automation_store is None:
                from lora.automations import AutomationStore

                self._automation_store = AutomationStore(
                    Path(self.config.user_lora_root) / "automations-v1.sqlite3"
                )
            return self._automation_store

    @property
    def automation_service(self) -> AutomationService:
        with self._lock:
            if self._automation_service is None:
                from lora.automations import AutomationService

                self._automation_service = AutomationService(self.automation_store)
            return self._automation_service

    @property
    def automation_scheduler(self) -> AutomationScheduler:
        with self._lock:
            if self._automation_scheduler is None:
                from lora.automations import AutomationScheduler

                self._automation_scheduler = AutomationScheduler(
                    store=self.automation_store,
                    coordinator=self.session_coordinator,
                    acquire_runtime=self.acquire_runtime,
                    config_factory=self._automation_config,
                )
            return self._automation_scheduler

    def start_automation_scheduler(self) -> None:
        self.automation_scheduler.start()

    def _automation_config(self, workspace_root: str) -> RunConfig:
        return load_run_config(
            workspace_root=workspace_root,
            agent_alias=self.agent_alias,
            max_steps=self.max_steps,
            context_window=self.context_window,
        )

    def attach_chat_registry(self, registry: Any) -> None:
        with self._lock:
            if self._chat_registry is not None:
                raise RuntimeError("chat registry is already configured")
            self._chat_registry = registry

    async def aclose(self) -> None:
        with self._lock:
            automation_scheduler = self._automation_scheduler
            self._automation_scheduler = None
            reminders = self._reminders
            self._reminders = None
            registry = self._chat_registry
            self._chat_registry = None
            coordinator = self._session_coordinator
            self._session_coordinator = None
            collaboration = self._session_collaboration
            self._session_collaboration = None
            self._session_turns = None
            runtime_pool = self._runtime_pool
            self._runtime_pool = None
            terminal_service = self._terminal_service
            self._terminal_service = None
        if automation_scheduler is not None:
            await automation_scheduler.close()
        if terminal_service is not None:
            terminal_service.close()
        if runtime_pool is not None:
            await runtime_pool.stop_accepting()
        if collaboration is not None:
            await collaboration.aclose()
        if registry is not None:
            await registry.close()
            coordinator = None
        if coordinator is not None:
            await coordinator.close()
        if runtime_pool is not None:
            await runtime_pool.close(cancel=True)
        if reminders is not None:
            await reminders.close()

    async def areload(self, overrides: dict[str, Any] | None = None) -> RunConfig:
        with self._lock:
            old_config = self.config
            reminders = self._reminders
            self._reminders = None
            runtime_pool = self._runtime_pool
        if reminders is not None:
            await reminders.close()
        config = self.reload(overrides)
        if runtime_pool is not None:
            from lora.orchestration import RuntimeScopeKey

            await runtime_pool.retire_scope(RuntimeScopeKey.from_config(old_config))
        return config

    def reload(self, overrides: dict[str, Any] | None = None) -> RunConfig:
        if overrides:
            for key in ("workspace_root", "agent_alias", "max_steps", "context_window"):
                if key in overrides:
                    setattr(self, key, overrides[key])
        with self._lock:
            self._config = self._load_config()
            self._manager = SessionManager(self._config)
            return self._config

    def remember_project(self, project_path: str | Path) -> None:
        self.project_state.remember_project(project_path)

    def config_for_scope(self, scope: SessionScope) -> RunConfig:
        if scope.workspace_root is None:
            return RunConfig(
                workspace_root=scope.runtime_workspace_root,
                lora_root=scope.lora_root,
                agent_alias=self.agent_alias or "default",
                resolved_agent=self.config.resolved_agent,
                runtime_approvals=self.config.runtime_approvals,
                max_steps=self.max_steps if self.max_steps is not None else -1,
                context_window=self.context_window,
            )
        return load_run_config(
            workspace_root=scope.workspace_root,
            agent_alias=self.agent_alias,
            max_steps=self.max_steps,
            context_window=self.context_window,
        )

    def _load_config(self) -> RunConfig:
        return load_run_config(
            workspace_root=self.workspace_root,
            agent_alias=self.agent_alias,
            max_steps=self.max_steps,
            context_window=self.context_window,
        )
