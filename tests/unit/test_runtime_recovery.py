from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from pygent.runtime.codec import context_from_dict

from lora.runtime.context import LORA_CONTEXT_CODECS, LoraContext
from lora.runtime.context_snapshots import ContextSnapshotStore
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

        config, service = recovery_service(
            workspace,
            force_compression=force_compression,
        )
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
        expected_roles = [
            "user",
            "assistant",
            "tool",
            "assistant",
        ]
        if force_compression:
            expected_roles = ["user", "assistant", *expected_roles]
        assert [message["role"] for message in restored.history] == expected_roles
        assert restored.history[-1]["tool_calls"] == []
        if force_compression:
            agent_context = context_from_dict(
                restored.metadata["agent_context"],
                registry=LORA_CONTEXT_CODECS,
            )
            assert isinstance(agent_context, LoraContext)
            assert agent_context.compression_count >= 1
            assert agent_context.projection_revision >= 1
            session_dir = Path(restored.session_dir)
            snapshots = ContextSnapshotStore(session_dir).list()
            assert snapshots
            assert max(item["compression_version"] for item in snapshots) >= 1
            assert not (session_dir / "model_context.json").exists()
            assert not (session_dir / "transcript.jsonl").exists()
            assert not (session_dir / "compactions.jsonl").exists()
            assert "pygent_agent_state" not in restored.metadata
