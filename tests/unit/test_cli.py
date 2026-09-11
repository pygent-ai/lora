from __future__ import annotations

import argparse
import asyncio
import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

from lora.cli import build_parser, main
from lora.cli.sessions import (
    _interactive_session_chat,
    _spawn_collaboration_worker,
    _turn_result_payload,
)
from lora.schema import CaseRunRef, RunConfig, default_cli_bash_presets


def test_main_prints_json_without_escaping_non_ascii() -> None:
    parser = argparse.ArgumentParser()
    parser.set_defaults(handler=lambda args: {"final_answer": "开发指南"})
    stdout = io.StringIO()
    with (
        patch("lora.cli.main.build_parser", return_value=parser),
        patch("sys.stdout", stdout),
    ):
        assert main([]) == 0
    assert '"final_answer": "开发指南"' in stdout.getvalue()


def test_session_run_is_the_only_noninteractive_chat_entry() -> None:
    parser = build_parser()
    args = parser.parse_args(["session", "run", "--new", "-m", "hello"])
    assert args.command == "session"
    assert args.session_command == "run"
    assert args.new is True
    assert args.message == "hello"

    with pytest.raises(SystemExit) as error:
        parser.parse_args(["chat", "--new", "-m", "hello"])
    assert error.value.code == 2

    with pytest.raises(SystemExit) as error:
        parser.parse_args(["session", "resume", "session-1"])
    assert error.value.code == 2


def test_default_cli_context_exposes_only_current_session_commands() -> None:
    presets = {preset.name: preset.command for preset in default_cli_bash_presets()}

    assert presets["lora-session"] == "uv run lora session --help"
    assert presets["lora-automation"] == "uv run lora automation --help"
    assert "lora-chat" not in presets
    assert all("lora chat" not in command for command in presets.values())


def test_session_collaboration_commands_are_registered() -> None:
    parser = build_parser()

    send = parser.parse_args(
        ["session", "send", "target-1", "-m", "hello", "--submission-id", "stable-1"]
    )
    start = parser.parse_args(["session", "start", "-m", "background task"])
    status = parser.parse_args(["session", "status", "op-1"])
    wait = parser.parse_args(["session", "wait", "op-1"])
    listing = parser.parse_args(["session", "list", "--mode", "agent"])

    assert (send.session_id, send.submission_id) == ("target-1", "stable-1")
    assert start.message == "background task"
    assert status.collaboration_id == wait.collaboration_id == "op-1"
    assert listing.mode == "agent"


def test_collaboration_worker_is_detached_with_explicit_runtime_context(
    tmp_path,
) -> None:
    args = argparse.Namespace(workspace_root=None, max_steps=7)
    config = RunConfig(
        workspace_root=str(tmp_path),
        lora_root=str(tmp_path / ".lora"),
        agent_alias="dev",
    )

    with patch("lora.cli.sessions.subprocess.Popen") as popen:
        _spawn_collaboration_worker(args, "op-1", config)

    command = popen.call_args.args[0]
    options = popen.call_args.kwargs
    assert command == [
        sys.executable,
        "-m",
        "lora.cli.collaboration_worker",
        "--workspace-root",
        str(tmp_path.resolve()),
        "--agent",
        "dev",
        "--max-steps",
        "7",
        "op-1",
    ]
    assert options["stdin"] is subprocess.DEVNULL
    assert options["stdout"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL


def test_turn_result_payload_is_the_cli_result_contract() -> None:
    run_ref = CaseRunRef(
        session_id="s1",
        case_id="chat",
        case_run_id="r1",
        run_dir="runs/r1",
    )
    turn = SimpleNamespace(
        run_ref=run_ref,
        execution_id="execution-1",
        status="passed",
    )
    output = SimpleNamespace(
        data={
            "result": {
                "status": "passed",
                "final_answer": "agent answer",
                "error": None,
            }
        }
    )

    assert _turn_result_payload(cast(Any, turn), output) == {
        "session_id": "s1",
        "case_run_id": "r1",
        "execution_id": "execution-1",
        "status": "passed",
        "final_answer": "agent answer",
        "run_dir": str(Path("runs/r1").resolve()),
    }


def test_interactive_session_chat_submits_one_managed_turn_per_input(
    tmp_path: Path,
) -> None:
    submitted: list[dict[str, object]] = []
    prewarmed: list[str] = []

    class Turn:
        execution_handle = None

        async def wait_ready(self) -> None:
            return None

        async def result(self):
            return (
                SimpleNamespace(
                    data={
                        "result": {
                            "status": "passed",
                            "final_answer": "ok",
                            "error": None,
                        }
                    }
                ),
                None,
            )

    class Turns:
        async def prewarm_session(self, *, session_id: str, **_kwargs) -> None:
            prewarmed.append(session_id)

        async def submit(self, **kwargs):
            submitted.append(kwargs)
            return Turn()

    class Host:
        turns = Turns()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    config = RunConfig(workspace_root=str(tmp_path), lora_root=str(tmp_path / ".lora"))
    args = argparse.Namespace(
        workspace_root=str(tmp_path),
        session_id=None,
        agent_alias=None,
        max_steps=None,
        new=True,
    )

    with (
        patch("lora.cli.sessions.load_run_config", return_value=config),
        patch("lora.cli.sessions.LocalExecutionHost", return_value=Host()),
        patch("builtins.input", side_effect=["first", "second", "/exit"]),
    ):
        assert asyncio.run(_interactive_session_chat(args)) is None

    assert len(submitted) == 2
    assert submitted[0]["turn_id"] == "turn-0001"
    assert submitted[1]["turn_id"] == "turn-0002"
    assert submitted[0]["session_id"] == submitted[1]["session_id"]
    assert prewarmed == [submitted[0]["session_id"]]
