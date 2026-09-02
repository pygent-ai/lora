from __future__ import annotations

from pathlib import Path

import pytest
from pygent import AIMessage, Module, ToolCall, ToolMessage, ToolResult, UserMessage

from lora.runtime.agent.pipeline import (
    ContextSnapshotModelModule,
    _context_snapshot_payload,
    checkpoint_context_snapshot,
)
from lora.runtime.context import LoraContext
from lora.runtime.context_snapshots import ContextSnapshotStore


def _session_run(tmp_path: Path) -> tuple[Path, Path]:
    session_dir = tmp_path / ".lora" / "sessions" / "s1"
    run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text("{}", encoding="utf-8")
    return session_dir, run_dir


def test_context_snapshot_payload_matches_the_model_visible_projection(
    tmp_path: Path,
) -> None:
    _, run_dir = _session_run(tmp_path)
    assistant = AIMessage(
        content="checking",
        tool_calls=(
            ToolCall(
                call_id="call-1", name="read", arguments={"file_path": "README.md"}
            ),
        ),
    )
    current = ToolMessage(
        results=(
            ToolResult(
                call_id="call-1", name="read", status="succeeded", output="contents"
            ),
        )
    )
    context = LoraContext(
        session_id="s1",
        case_id="chat",
        case_run_id="r1",
        run_dir=str(run_dir),
        turn_id="turn-1",
        system_prompt="effective system",
        messages=(UserMessage(content="hello"), assistant),
        compression_count=2,
        projection_revision=7,
    )

    snapshot = _context_snapshot_payload(context, current)

    assert snapshot["compression_version"] == 2
    assert snapshot["projection_revision"] == 7
    assert snapshot["system_prompt"] == "effective system"
    assert [message["role"] for message in snapshot["messages"]] == [
        "user",
        "assistant",
        "tool",
    ]
    assert snapshot["messages"][-1]["source"] == "current"
    assert snapshot["messages"][1]["tool_calls"][0]["name"] == "read"


def test_context_response_snapshot_includes_the_model_answer(tmp_path: Path) -> None:
    _, run_dir = _session_run(tmp_path)
    context = LoraContext(
        session_id="s1",
        case_id="chat",
        case_run_id="r1",
        run_dir=str(run_dir),
        turn_id="turn-1",
        system_prompt="effective system",
        messages=(UserMessage(content="history"),),
    )

    snapshot = _context_snapshot_payload(
        context,
        UserMessage(content="current"),
        phase="response",
        response=AIMessage(content="answer"),
    )

    assert snapshot["schema_version"] == 2
    assert snapshot["phase"] == "response"
    assert [message["role"] for message in snapshot["messages"]] == [
        "user",
        "user",
        "assistant",
    ]
    assert snapshot["messages"][-1]["source"] == "response"
    assert snapshot["messages"][-1]["content"] == "answer"


class _AnswerModel(Module):
    async def forward(self, message, context):
        del message
        return AIMessage(content="final answer"), context


@pytest.mark.asyncio
async def test_context_snapshot_model_module_persists_request_and_response(
    tmp_path: Path,
) -> None:
    session_dir, run_dir = _session_run(tmp_path)
    context = LoraContext(
        session_id="s1",
        case_id="chat",
        case_run_id="r1",
        run_dir=str(run_dir),
        turn_id="turn-1",
        system_prompt="effective system",
    )
    model = ContextSnapshotModelModule(_AnswerModel())

    await model.invoke(UserMessage(content="hello"), context)
    stored = ContextSnapshotStore(session_dir).list()

    assert [snapshot["phase"] for snapshot in stored] == ["request", "response"]
    assert [message["role"] for message in stored[-1]["messages"]] == [
        "user",
        "assistant",
    ]
    assert stored[-1]["messages"][-1]["content"] == "final answer"


@pytest.mark.asyncio
async def test_context_snapshot_checkpoint_is_idempotent_and_redacted(
    tmp_path: Path,
) -> None:
    session_dir, run_dir = _session_run(tmp_path)
    context = LoraContext(
        session_id="s1",
        case_id="chat",
        case_run_id="r1",
        run_dir=str(run_dir),
        turn_id="turn-1",
        system_prompt="OPENAI_API_KEY=sk-1234567890abcdef1234567890abcdef",
        compression_count=1,
    )
    current = UserMessage(content="hello")

    first = await checkpoint_context_snapshot(context, current)
    second = await checkpoint_context_snapshot(context, current)
    stored = ContextSnapshotStore(session_dir).list()

    assert first["snapshot_id"] == second["snapshot_id"]
    assert len(stored) == 1
    assert "sk-1234567890" not in stored[0]["system_prompt"]
    assert "[REDACTED]" in stored[0]["system_prompt"]
