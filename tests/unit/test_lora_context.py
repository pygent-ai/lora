from __future__ import annotations

from dataclasses import replace

from pygent import AIMessage, UserMessage
from pygent.runtime.codec import context_from_dict, context_to_dict, message_to_dict
from pygent.runtime.context_codec import ContextCodecRegistry

from lora.runtime.context import LORA_CONTEXT_CODEC, LORA_CONTEXT_CODECS, LoraContext
from lora.runtime.agent import _initial_lora_context
from lora.runtime.file_effect_models import DeferredFileEffectJob


def test_lora_context_keeps_model_projection_and_native_commits_distinct() -> None:
    current = UserMessage(content="current")
    context = LoraContext(
        session_id="session-1",
        messages=(current,),
        committed_messages=(current,),
    )

    projected = replace(context, messages=(current, AIMessage(content="answer")))

    assert isinstance(projected, LoraContext)
    assert projected.committed_messages == (current,)
    assert [item["content"] for item in projected.history] == ["current"]


def test_lora_context_codec_round_trips_all_portable_state() -> None:
    context = LoraContext(
        session_id="session-1",
        case_id="chat",
        case_run_id="run-1",
        run_dir="C:/tmp/run-1",
        turn_id="turn-1",
        messages=(UserMessage(content="summary"),),
        committed_messages=(UserMessage(content="original"),),
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


def test_native_context_restore_uses_the_checkpoint_projection() -> None:
    session_history = (
        UserMessage(content="old user"),
        AIMessage(content="old answer"),
        UserMessage(content="recent user"),
        AIMessage(content="recent answer"),
    )
    context = LoraContext(
        session_id="session-1",
    )
    history = [message_to_dict(message) for message in session_history]
    checkpoint = context_to_dict(
        LoraContext(
            session_id="session-1",
            system_prompt="rendered system",
            messages=(UserMessage(content="durable summary"),),
            committed_messages=session_history[:2],
            compression_count=3,
            input_token_scale_ppm=1_250_000,
            last_input_tokens=4096,
            projection_revision=9,
        ),
        registry=LORA_CONTEXT_CODECS,
    )

    restored, compacted = _initial_lora_context(
        context=context,
        history=history,
        checkpoint=checkpoint,
    )

    assert compacted is True
    assert restored.committed_messages == ()
    assert [message.content for message in restored.messages] == ["durable summary"]
    assert restored.compression_count == 3
    assert restored.input_token_scale_ppm == 1_250_000
    assert restored.last_input_tokens == 4096
    assert restored.projection_revision == 9


def test_pre_0_3_3_checkpoint_rebuilds_from_authoritative_session_history() -> None:
    context = LoraContext(session_id="session-1")
    checkpoint = context_to_dict(context, registry=LORA_CONTEXT_CODECS)
    checkpoint["version"] = 5
    history = [message_to_dict(UserMessage(content="durable user"))]

    restored, compacted = _initial_lora_context(
        context=context,
        history=history,
        checkpoint=checkpoint,
    )

    assert compacted is False
    assert [message.content for message in restored.messages] == ["durable user"]
    assert restored.committed_messages == ()


def test_eternal_bootstrap_restores_history_instead_of_empty_checkpoint() -> None:
    context = LoraContext(eternal_memory_enabled=True)
    checkpoint = context_to_dict(context, registry=LORA_CONTEXT_CODECS)
    history = [message_to_dict(UserMessage(content="keep this requirement")),
               message_to_dict(AIMessage(content="agreed plan"))]
    restored, compacted = _initial_lora_context(
        context=context, history=history, checkpoint=checkpoint
    )
    assert [item.content for item in restored.messages] == [
        "keep this requirement", "agreed plan"
    ]
    assert not compacted
    assert restored.committed_messages == ()


def test_uncovered_memory_suffix_keeps_tool_pairs_and_all_later_turns() -> None:
    from pygent import ToolCall, ToolMessage, ToolResult
    from lora.runtime.service import _uncovered_conversation_messages

    messages = [
        UserMessage(content="covered turn"), AIMessage(content="covered answer"),
        UserMessage(content="inspect"),
        AIMessage(tool_calls=(ToolCall(call_id="read-1", name="read", arguments={}),)),
        ToolMessage(results=(ToolResult(call_id="read-1", name="read", output="data", status="succeeded"),)),
        AIMessage(content="inspection finished"),
        UserMessage(content="another requirement"), AIMessage(content="noted"),
    ]
    history = [message_to_dict(item) for item in messages]
    # Snapshot stops after the tool call; keeping only the suffix would orphan its result.
    result = _uncovered_conversation_messages(history, 4)
    assert result == tuple(messages[2:])
    assert _uncovered_conversation_messages(history, 2) == tuple(messages[2:])
    assert _uncovered_conversation_messages(history, len(history)) == tuple(messages[-2:])
    assert _uncovered_conversation_messages([], 0) == ()
