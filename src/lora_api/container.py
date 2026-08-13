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
    from lora.runtime.service import LoraRuntimeService


@dataclass(slots=True)
class ApiContext:
    """Application container shared by the HTTP composition root."""

    workspace_root: str | None = None
    config_path: str | None = None
    agent_alias: str | None = None
    max_steps: int | None = None
    context_window: int | None = None
    state_path: str | None = None
    _config: RunConfig | None = None
    _manager: SessionManager | None = None
    _project_state: GuiProjectState | None = None
    _runtime_service: LoraRuntimeService | None = None
    _chat_registry: Any | None = None
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
    def runtime_service(self) -> LoraRuntimeService:
        with self._lock:
            if self._runtime_service is None:
                from lora.runtime.service import LoraRuntimeService

                self._runtime_service = LoraRuntimeService(self.config)
            return self._runtime_service

    @property
    def chat_registry(self) -> Any:
        with self._lock:
            if self._chat_registry is None:
                raise RuntimeError("chat registry has not been configured")
            return self._chat_registry

    def attach_chat_registry(self, registry: Any) -> None:
        with self._lock:
            if self._chat_registry is not None:
                raise RuntimeError("chat registry is already configured")
            self._chat_registry = registry

    async def aclose(self) -> None:
        with self._lock:
            runtime = self._runtime_service
            self._runtime_service = None
            registry = self._chat_registry
            self._chat_registry = None
        if registry is not None:
            await registry.close()
        if runtime is not None:
            await runtime.close(cancel=True)

    async def areload(self, overrides: dict[str, Any] | None = None) -> RunConfig:
        with self._lock:
            runtime = self._runtime_service
            self._runtime_service = None
            registry = self._chat_registry
            self._chat_registry = None
        if registry is not None:
            await registry.close()
        if runtime is not None:
            await runtime.close(cancel=True)
        return self.reload(overrides)

    def reload(self, overrides: dict[str, Any] | None = None) -> RunConfig:
        if overrides:
            for key in ("workspace_root", "config_path", "agent_alias", "max_steps", "context_window"):
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
                max_steps=self.max_steps if self.max_steps is not None else -1,
                context_window=self.context_window,
            )
        return load_run_config(
            workspace_root=scope.workspace_root,
            config_file=self.config_path,
            agent_alias=self.agent_alias,
            max_steps=self.max_steps,
            context_window=self.context_window,
        )

    def _load_config(self) -> RunConfig:
        return load_run_config(
            workspace_root=self.workspace_root,
            config_file=self.config_path,
            agent_alias=self.agent_alias,
            max_steps=self.max_steps,
            context_window=self.context_window,
        )
