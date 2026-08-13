from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from lora.schema import BashCliPreset

from .common import _hash_json

def _always_enabled(ctx: "PromptRenderContext") -> bool:
    return True


@dataclass(slots=True)
class PromptModule:
    id: str
    phase: Literal["static", "request_system"]
    type: Literal["system", "project", "runtime", "tool", "memory", "policy"]
    cache_scope: Literal["session", "request", "turn", "none"]
    order: int
    render: Callable[["PromptRenderContext"], str | None]
    required: bool = False
    depends_on: tuple[str, ...] = ()
    enabled: Callable[["PromptRenderContext"], bool] = _always_enabled

    @property
    def version_hash(self) -> str:
        return _hash_json(
            {
                "id": self.id,
                "phase": self.phase,
                "type": self.type,
                "cache_scope": self.cache_scope,
                "order": self.order,
                "depends_on": list(self.depends_on),
            }
        )


@dataclass(slots=True)
class RenderedPromptModule:
    id: str
    phase: Literal["static", "request_system"]
    type: str
    order: int
    cache_scope: str
    version_hash: str
    input_hash: str
    content_hash: str
    rendered_at: str
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "phase": self.phase,
            "type": self.type,
            "order": self.order,
            "cache_scope": self.cache_scope,
            "version_hash": self.version_hash,
            "input_hash": self.input_hash,
            "content_hash": self.content_hash,
            "rendered_at": self.rendered_at,
            "skipped_reason": self.skipped_reason,
        }


@dataclass(slots=True)
class PromptRenderContext:
    session_id: str
    workspace_root: Path
    session_dir: Path
    turn_id: str | None
    projection: dict[str, Any]
    tool_names: list[str]
    request_id: str | None = None
    request_type: str = "agent_turn"
    cli_bash_presets: list[BashCliPreset] = field(default_factory=list)
    user_lora_root: Path | None = None
    project_lora_root: Path | None = None
    user_skills_dir: Path | None = None
    project_skills_dir: Path | None = None


@dataclass(slots=True)
class StaticPromptResult:
    text: str
    prompt_hash: str
    modules: list[dict[str, Any]]
    metadata: dict[str, Any]
    created: bool = False


@dataclass(slots=True)
class PromptRequestContext:
    session_id: str
    case_run_id: str | None
    turn_id: str | None
    request_id: str
    request_stage: Literal["before_model_request", "other"]
    request_type: Literal["agent_turn", "case_run", "summary", "evaluation"]
    history_message_count: int
    latest_user_input_hash: str | None
    tool_names: list[str]
    file_state_hash: str | None
    projection_hash: str | None
    runtime_state_hash: str | None
    dynamic_input_hash: str | None


@dataclass(slots=True)
class PromptInjectionDecision:
    inject_dynamic: bool
    module_ids: list[str]
    reason: str
    skipped_module_ids: list[str] = field(default_factory=list)
    request_stage: Literal["before_model_request", "other"] = "before_model_request"
    decision_hash: str = ""

    def __post_init__(self) -> None:
        if not self.decision_hash:
            self.decision_hash = _hash_json(
                {
                    "inject_dynamic": self.inject_dynamic,
                    "module_ids": self.module_ids,
                    "reason": self.reason,
                    "skipped_module_ids": self.skipped_module_ids,
                    "request_stage": self.request_stage,
                }
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "inject_dynamic": self.inject_dynamic,
            "module_ids": self.module_ids,
            "reason": self.reason,
            "skipped_module_ids": self.skipped_module_ids,
            "request_stage": self.request_stage,
            "decision_hash": self.decision_hash,
        }


@dataclass(slots=True)
class ModelRequestPrompt:
    text: str
    static_text: str
    request_system_text: str | None
    prompt_hash: str
    static_prompt_hash: str
    request_system_prompt_hash: str | None
    modules: list[dict[str, Any]]
    injection_decision: PromptInjectionDecision

class PromptContextView(Protocol):
    """Narrow context-manager surface consumed by reminder renderers."""

    workspace_root: Path
    session_dir: Path
    cli_bash_presets: list[Any]
    user_lora_root: Path
    project_lora_root: Path
    user_skills_dir: Path
    project_skills_dir: Path
