from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lora.sessions import SessionManager
from tests.runtime_recovery_support import recovery_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("boundary", "force_compression"),
    [
        ("user", False),
        ("assistant", False),
        ("tool", False),
        ("assistant", True),
    ],
)
async def test_pygent_recovers_lora_turn_from_durable_message_boundary(
    boundary: str,
    force_compression: bool,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        execution_path = workspace / "execution-id.txt"
        reached_path = workspace / "reached.txt"
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.runtime_recovery_support",
                str(workspace),
                boundary,
                str(execution_path),
                str(reached_path),
                "on" if force_compression else "off",
            ],
            cwd=Path(__file__).parents[2],
            timeout=30,
            check=False,
        )
        assert process.returncode == 91
        assert reached_path.read_text(encoding="utf-8") == boundary
        execution_id = execution_path.read_text(encoding="utf-8")

        config, service = recovery_service(workspace)
        manager = SessionManager(config)
        run_ref = await service.recovery_case_run(execution_id)
        interrupted = manager.load(run_ref.session_id)
        assert interrupted.history[-1]["role"] == boundary
        output_path = workspace / "recovery-output.txt"
        interrupted_mtime = output_path.stat().st_mtime_ns if output_path.exists() else None

        try:
            handle = await service.recover_turn(
                execution_id,
                deadline=asyncio.get_running_loop().time() + 30,
            )
            output, _ = await handle.result()
        finally:
            await service.close()

        restored = manager.load(run_ref.session_id)
        assert output.content == "recovered final answer"
        assert output_path.read_text(encoding="utf-8") == "durable tool output"
        if boundary == "tool":
            assert output_path.stat().st_mtime_ns == interrupted_mtime
        assert [message["role"] for message in restored.history] == [
            "user",
            "assistant",
            "tool",
            "assistant",
        ]
        assert restored.history[-1]["tool_calls"] == []
