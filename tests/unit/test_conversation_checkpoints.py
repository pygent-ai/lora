from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pytest
from pygent import AIMessage, ToolCall, ToolMessage, ToolResult, UserMessage
from pygent.runtime.codec import message_to_dict

from lora.config import load_run_config
from lora.runtime.agent.pipeline import checkpoint_conversation_message
from lora.runtime.context import LoraContext
from lora.runtime.service import LoraRuntimeService
from lora.schema import AgentSession, CaseDefinition
from lora.sessions import SessionManager


@pytest.mark.asyncio
async def test_interrupted_turn_recovers_completed_conversation_boundaries() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = load_run_config(workspace_root=Path(tmp))
        manager = SessionManager(config)
        session_ref = manager.create(case_id="chat", mode="chat")
        run_ref = manager.start_case_run(session_ref.session_id, "chat", run_config=config)
        context = LoraContext(
            session_id=run_ref.session_id,
            case_id=run_ref.case_id,
            case_run_id=run_ref.case_run_id,
            run_dir=run_ref.run_dir,
            turn_id="turn-interrupted",
        )
        user = UserMessage(content="inspect the workspace")
        assistant = AIMessage(
            content="I will inspect it.",
            tool_calls=(
                ToolCall(
                    call_id="read-1",
                    name="read",
                    arguments={"file_path": "README.md"},
                    tool_id="lora.tool.read",
                    tool_version="1",
                ),
            ),
        )
        tool = ToolMessage(
            results=(
                ToolResult(
                    call_id="read-1",
                    name="read",
                    status="succeeded",
                    output="workspace contents",
                    side_effect_committed=False,
                ),
            ),
        )

        await checkpoint_conversation_message(config, context, user, boundary="user-input")
        await checkpoint_conversation_message(config, context, assistant, boundary="model-step-1")
        await checkpoint_conversation_message(config, context, tool, boundary="tool-step-1")
        await checkpoint_conversation_message(config, context, tool, boundary="tool-step-1")

        restored = manager.load(session_ref.session_id)
        assert [message["role"] for message in restored.history] == [
            "user",
            "assistant",
            "tool",
        ]
        assert restored.history[1]["tool_calls"][0]["call_id"] == "read-1"
        assert restored.history[2]["results"][0]["output"] == "workspace contents"

        history_path = Path(session_ref.session_dir) / "context" / "history.jsonl"
        rows = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 3
        assert all(row.get("checkpoint_id") for row in rows)
        assert all(isinstance(row.get("message"), dict) for row in rows)

        checkpoint_path = (
            Path(session_ref.session_dir)
            / "context"
            / "conversation-checkpoints.sqlite3"
        )
        with closing(sqlite3.connect(checkpoint_path)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM conversation_checkpoints"
            ).fetchone() == (3,)

        next_run = manager.start_case_run(session_ref.session_id, "chat", run_config=config)
        service = LoraRuntimeService(config)
        agent = service.new_agent(interactive_approvals=False)
        _, next_context = service._prepare_turn(
            agent=agent,
            manager=manager,
            run_ref=next_run,
            message="continue",
            config=config,
            turn_id="turn-next",
        )
        assert [message.role for message in next_context.full_history] == [
            "user",
            "assistant",
            "tool",
        ]


def test_session_load_replays_raw_checkpoint_written_before_session_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = load_run_config(workspace_root=Path(tmp))
        manager = SessionManager(config)
        session_ref = manager.create(case_id="chat", mode="chat")
        run_ref = manager.start_case_run(session_ref.session_id, "chat", run_config=config)
        message = message_to_dict(AIMessage(content="durable boundary"))
        assert manager.append_history_checkpoint(
            run_ref,
            turn_id="turn",
            checkpoint_id="run:turn:model-1",
            message=message,
        )

        restored = manager.load(session_ref.session_id)
        assert restored.history == [message]
        manager.save(restored)
        assert manager.load(session_ref.session_id).history == [message]


@pytest.mark.asyncio
async def test_recovery_keeps_raw_secret_while_audit_history_is_redacted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = load_run_config(workspace_root=Path(tmp))
        manager = SessionManager(config)
        session_ref = manager.create(case_id="chat", mode="chat")
        run_ref = manager.start_case_run(session_ref.session_id, "chat", run_config=config)
        context = LoraContext(
            session_id=run_ref.session_id,
            case_id=run_ref.case_id,
            case_run_id=run_ref.case_run_id,
            run_dir=run_ref.run_dir,
            turn_id="turn-secret",
        )

        await checkpoint_conversation_message(
            config,
            context,
            UserMessage(content="password=hunter2"),
            boundary="user-input",
        )

        assert manager.load(session_ref.session_id).history[0]["content"] == "password=hunter2"
        audit = (
            Path(session_ref.session_dir) / "context" / "history.jsonl"
        ).read_text(encoding="utf-8")
        assert "hunter2" not in audit
        assert "[REDACTED]" in audit


@pytest.mark.asyncio
async def test_transient_checkpoints_do_not_enter_session_history() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = load_run_config(workspace_root=Path(tmp))
        manager = SessionManager(config)
        session_ref = manager.create(case_id="chat", mode="chat")
        run_ref = manager.start_case_run(session_ref.session_id, "chat", run_config=config)
        context = LoraContext(
            session_id=run_ref.session_id,
            case_id=run_ref.case_id,
            case_run_id=run_ref.case_run_id,
            run_dir=run_ref.run_dir,
            turn_id="turn-transient",
            metadata={"persist_conversation_history": False},
        )

        await checkpoint_conversation_message(
            config,
            context,
            UserMessage(content="temporary"),
            boundary="user-input",
        )

        restored = manager.load(session_ref.session_id)
        assert restored.history == []
        manager.save(restored)
        assert manager.load(session_ref.session_id).history == []
        assert restored.metadata["history_checkpoint_seq"] == 1


def test_agent_session_rejects_unknown_snapshot_version() -> None:
    with pytest.raises(ValueError, match="unsupported AgentSession version: 2.0"):
        AgentSession.from_dict(
            {
                "version": "2.0",
                "session_id": "session",
                "workspace_root": str(Path.cwd()),
                "session_dir": str(Path.cwd()),
                "created_at": "now",
                "updated_at": "now",
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("carry_context", [True, False])
async def test_execute_case_handles_multi_message_context_policy(
    carry_context: bool,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = load_run_config(workspace_root=Path(tmp))
        assert config.resolved_agent is not None
        for route in config.resolved_agent.routes:
            route.api_key = None
            route.api_key_source = "missing"
        manager = SessionManager(config)
        session_ref = manager.create(case_id="multi", mode="case")
        session = manager.load(session_ref.session_id)
        session.history.append(message_to_dict(UserMessage(content="existing")))
        manager.save(session)
        run_ref = manager.start_case_run(
            session_ref.session_id, "multi", run_config=config
        )
        case = CaseDefinition(
            id="multi",
            title="multi-message context",
            type="e2e",
            session={"carry_context": carry_context},
            input={
                "messages": [
                    {"role": "user", "content": "first"},
                    {"role": "user", "content": "second"},
                ]
            },
        )
        service = LoraRuntimeService(config)
        try:
            await service.execute_case(
                manager=manager,
                session=manager.load(session_ref.session_id),
                case=case,
                run_ref=run_ref,
            )
        finally:
            await service.close()

        restored = manager.load(session_ref.session_id)
        if carry_context:
            assert [message["role"] for message in restored.history] == [
                "user",
                "user",
                "user",
                "assistant",
            ]
            assert restored.history[0]["content"] == "existing"
            assert restored.history[1]["content"] == "first"
            assert restored.history[2]["data"]["raw_content"] == "second"
        else:
            assert restored.history == [message_to_dict(UserMessage(content="existing"))]
