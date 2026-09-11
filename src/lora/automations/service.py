from __future__ import annotations

from pathlib import Path
from typing import Any

from lora.config import load_run_config
from lora.sessions import SessionManager

from .models import Automation, AutomationDestination, AutomationStatus
from .store import AutomationStore


class AutomationService:
    """Validated application operations shared by CLI and HTTP adapters."""

    def __init__(self, store: AutomationStore) -> None:
        self.store = store

    def create(self, **values: Any) -> Automation:
        self._validate_target(
            str(values.get("workspace_root") or ""),
            str(values.get("destination") or ""),
            values.get("target_session_id"),
        )
        return self.store.create(**values)

    def update(self, automation_id: str, **changes: Any) -> Automation:
        current = self.store.get(automation_id)
        workspace = str(changes.get("workspace_root") or current.workspace_root)
        destination = str(changes.get("destination") or current.destination)
        target = changes.get("target_session_id", current.target_session_id)
        self._validate_target(workspace, destination, target)
        return self.store.update(automation_id, **changes)

    def pause(self, automation_id: str) -> Automation:
        return self.store.set_status(automation_id, AutomationStatus.PAUSED.value)

    def resume(self, automation_id: str) -> Automation:
        return self.store.set_status(automation_id, AutomationStatus.ACTIVE.value)

    @staticmethod
    def _validate_target(
        workspace_root: str, destination: str, target_session_id: object
    ) -> None:
        workspace = Path(workspace_root).expanduser().resolve()
        if not workspace.is_dir():
            raise FileNotFoundError(f"workspace does not exist: {workspace}")
        mode = AutomationDestination(destination)
        if mode is AutomationDestination.HEARTBEAT:
            session_id = str(target_session_id or "").strip()
            if not session_id:
                raise ValueError("heartbeat automation requires target_session_id")
            config = load_run_config(workspace_root=str(workspace))
            SessionManager(config).load(session_id)


__all__ = ["AutomationService"]
