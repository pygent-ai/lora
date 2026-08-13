from __future__ import annotations

from dataclasses import replace

from pygent import AIMessage, UserMessage
from pygent.runtime.codec import context_from_dict, context_to_dict
from pygent.runtime.context_codec import ContextCodecRegistry

from lora.runtime.context import LORA_CONTEXT_CODEC, LoraContext
from lora.runtime.file_effect_models import DeferredFileEffectJob


def test_lora_context_keeps_model_projection_and_full_history_distinct() -> None:
    old = UserMessage(content="old")
    current = UserMessage(content="current")
    context = LoraContext(
        session_id="session-1",
        messages=(current,),
        full_history=(old, current),
    )

    projected = replace(context, messages=(current, AIMessage(content="answer")))

    assert isinstance(projected, LoraContext)
    assert projected.full_history == (old, current)
    assert [item["content"] for item in projected.history] == ["old", "current"]


def test_lora_context_codec_round_trips_all_portable_state() -> None:
    context = LoraContext(
        session_id="session-1",
        session_status="compacted",
        case_id="chat",
        case_run_id="run-1",
        run_dir="C:/tmp/run-1",
        turn_id="turn-1",
        messages=(UserMessage(content="summary"),),
        full_history=(UserMessage(content="original"),),
        model_context_compacted=True,
    )
    registry = ContextCodecRegistry((LORA_CONTEXT_CODEC,))

    restored = context_from_dict(context_to_dict(context, registry=registry), registry=registry)

    assert restored == context
    assert isinstance(restored, LoraContext)


def test_lora_context_owns_and_drains_pending_file_effects() -> None:
    job = DeferredFileEffectJob(
        tool_call_id="call-1",
        tool_name="bash",
        args={"command": "echo hi"},
        turn_id="turn-1",
        declared=[],
    )
    context = LoraContext(
        session_id="session-1",
        case_id="chat",
        case_run_id="run-1",
        run_dir="C:/tmp/run-1",
        turn_id="turn-1",
    ).append_file_effects(job)
    registry = ContextCodecRegistry((LORA_CONTEXT_CODEC,))

    restored = context_from_dict(context_to_dict(context, registry=registry), registry=registry)
    jobs, drained = restored.drain_file_effects()

    assert jobs == (job,)
    assert drained.pending_file_effects == ()
