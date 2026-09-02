from __future__ import annotations

import argparse
import asyncio
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lora.cli import _chat_message_payload, main
from lora.cli.main import _chat_async
from lora.schema import CaseRunRef, RunConfig


def test_main_prints_json_without_escaping_non_ascii() -> None:
    parser = argparse.ArgumentParser()
    parser.set_defaults(handler=lambda args: {"final_answer": "开发指南"})
    stdout = io.StringIO()
    with patch("lora.cli.main.build_parser", return_value=parser), patch("sys.stdout", stdout):
        assert main([]) == 0
    assert '"final_answer": "开发指南"' in stdout.getvalue()


def test_chat_message_payload_is_the_cli_result_contract() -> None:
    run_ref = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir="runs/r1")
    assert _chat_message_payload(
        run_ref,
        {"status": "passed", "final_answer": "agent answer", "error": None},
    ) == {
        "final_answer": "agent answer",
        "session_id": "s1",
        "case_run_id": "r1",
        "run_dir": str(Path("runs/r1").resolve()),
    }


def test_interactive_chat_creates_one_case_run_per_turn(tmp_path: Path) -> None:
    class EmptyEvents:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class Handle:
        def __init__(self, execution_id: str) -> None:
            self.execution_id = execution_id

        def subscribe(self):
            return EmptyEvents()

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

    class Runtime:
        def __init__(self) -> None:
            self.run_refs = []
            self.reminders = SimpleNamespace(prewarm_session=lambda _session_id: None)

        async def initialize(self) -> None:
            return None

        async def start_turn(self, *, run_ref, **_kwargs):
            self.run_refs.append(run_ref)
            return Handle(f"execution-{len(self.run_refs)}")

        async def close(self, *, cancel: bool) -> None:
            assert cancel is True

    config = RunConfig(workspace_root=tmp_path, lora_root=tmp_path / ".lora")
    runtime = Runtime()
    args = SimpleNamespace(
        workspace_root=str(tmp_path),
        session_id=None,
        agent_alias=None,
        max_steps=None,
        new=True,
        message=None,
    )

    with (
        patch("lora.cli.main.load_run_config", return_value=config),
        patch("lora.cli.main.LoraRuntimeService", return_value=runtime),
        patch("builtins.input", side_effect=["first", "second", "/exit"]),
    ):
        assert asyncio.run(_chat_async(args)) is None

    assert len(runtime.run_refs) == 2
    assert runtime.run_refs[0].case_run_id != runtime.run_refs[1].case_run_id
