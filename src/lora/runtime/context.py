from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from pygent import Context, ContextCodec, FrozenJsonObject, Message, freeze_json_object, thaw_json
from pygent.runtime.codec import message_to_dict

from lora.schema import CaseRunRef
from .file_effect_models import DeferredFileEffectJob


@dataclass(frozen=True, slots=True)
class LoraContext(Context):
    """Portable Lora agent state carried through one Pygent execution."""

    context_schema: ClassVar[str] = "lora.agent-context"
    context_schema_version: ClassVar[int] = 3

    session_id: str = ""
    session_status: str = "normal"
    case_id: str = ""
    case_run_id: str = ""
    run_dir: str = ""
    turn_id: str | None = None
    full_history: tuple[Message, ...] = ()
    model_context_compacted: bool = False
    eternal_memory_enabled: bool = False
    memory_covered_through: int = 0
    memory_projection: FrozenJsonObject = field(default_factory=lambda: freeze_json_object({}))
    raw_history_location: str = ""
    pending_file_effects: tuple[FrozenJsonObject, ...] = ()

    @property
    def history(self) -> list[dict[str, Any]]:
        """Return this execution's portable history segment in storage shape."""

        return [message_to_dict(message) for message in self.full_history]

    def append_history(self, *messages: Message) -> LoraContext:
        """Append messages to durable history without changing model projection."""

        return replace(self, full_history=(*self.full_history, *messages))

    @property
    def case_run_ref(self) -> CaseRunRef:
        """Rebuild the domain reference from portable execution facts."""

        return CaseRunRef(
            session_id=self.session_id,
            case_id=self.case_id,
            case_run_id=self.case_run_id,
            run_dir=self.run_dir,
        )

    def append_file_effects(self, *jobs: DeferredFileEffectJob) -> LoraContext:
        """Append deferred effects as strict portable JSON values."""

        encoded = tuple(freeze_json_object(job.to_dict()) for job in jobs)
        return replace(self, pending_file_effects=(*self.pending_file_effects, *encoded))

    def drain_file_effects(self) -> tuple[tuple[DeferredFileEffectJob, ...], LoraContext]:
        """Return pending effects and a context with the queue cleared."""

        jobs = tuple(
            DeferredFileEffectJob.from_dict(dict(thaw_json(value)))
            for value in self.pending_file_effects
        )
        return jobs, replace(self, pending_file_effects=())


LORA_CONTEXT_CODEC = ContextCodec.dataclass(LoraContext)
