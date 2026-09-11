from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class TurnCommand:
    session_id: str
    message: str
    case_id: str = "chat"
    turn_id: str | None = None
    submission_id: str | None = None
    interactive_approvals: bool = True
    message_kind: str = "lora.chat.turn"
    message_data: dict[str, object] = field(default_factory=dict)
    session_title: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must be non-empty")
        if not self.case_id:
            raise ValueError("case_id must be non-empty")
        if not self.message:
            raise ValueError("message must be non-empty")


class TurnState(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    FINALIZING = "finalizing"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"

    @property
    def terminal(self) -> bool:
        return self in {
            TurnState.PASSED,
            TurnState.FAILED,
            TurnState.ERROR,
            TurnState.SKIPPED,
        }


__all__ = ["TurnCommand", "TurnState"]
