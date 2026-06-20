from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import Request

from lora.config import load_run_config
from lora.schema import RunConfig
from lora.sessions import SessionManager
from lora_api.services.project_state import GuiProjectState, SessionScope


@dataclass(slots=True)
class ApiContext:
    workspace_root: str | None = None
    config_path: str | None = None
    agent_alias: str | None = None
    model: str | None = None
    max_steps: int | None = None
    context_window: int | None = None
    state_path: str | None = None
    _config: RunConfig | None = None
    _manager: SessionManager | None = None
    _project_state: GuiProjectState | None = None
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

    def reload(self, overrides: dict[str, Any] | None = None) -> RunConfig:
        if overrides:
            for key in ("workspace_root", "config_path", "agent_alias", "model", "max_steps", "context_window"):
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
                model_name=self.model,
                max_steps=self.max_steps if self.max_steps is not None else -1,
                context_window=self.context_window,
            )
        return load_run_config(
            workspace_root=scope.workspace_root,
            config_file=self.config_path,
            agent_alias=self.agent_alias,
            model=self.model,
            max_steps=self.max_steps,
            context_window=self.context_window,
        )

    def _load_config(self) -> RunConfig:
        return load_run_config(
            workspace_root=self.workspace_root,
            config_file=self.config_path,
            agent_alias=self.agent_alias,
            model=self.model,
            max_steps=self.max_steps,
            context_window=self.context_window,
        )


def get_api_context(request: Request) -> ApiContext:
    context = getattr(request.app.state, "api_context", None)
    if not isinstance(context, ApiContext):
        context = ApiContext(workspace_root=str(Path.cwd()))
        request.app.state.api_context = context
    return context
