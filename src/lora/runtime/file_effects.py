from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pygent import IdempotencyPolicy, ToolDefinition, ToolSideEffect, ToolSpec, thaw_json
from pygent.tool.executors import SandboxExecutorSupport, ToolExecutionContext

from lora.core.io import read_json, utc_now, write_json
from lora.tracing.events import EventStore

from .file_effect_models import (
    DeferredFileEffectBatch,
    DeferredFileEffectJob,
    FileSnapshot,
)
from .tools import FileEffectTracker

class FileEffectBaselineStore:
    def __init__(self, session_dir: str | Path | None):
        self.session_dir = Path(session_dir).expanduser().resolve() if session_dir is not None else None
        self.path = self.session_dir / "state" / "file_effects_baseline.json" if self.session_dir is not None else None

    def load(self) -> dict[str, Any] | None:
        if self.path is None or not self.path.exists():
            return None
        data = read_json(self.path, default={"snapshots": {}})
        raw_snapshots = data.get("snapshots")
        if not isinstance(raw_snapshots, dict):
            return None
        return {
            str(path): FileSnapshot(**snapshot)
            for path, snapshot in raw_snapshots.items()
            if isinstance(snapshot, dict)
        }

    def save(self, snapshots: dict[str, Any]) -> None:
        if self.path is None:
            return
        write_json(
            self.path,
            {
                "updated_at": utc_now(),
                "snapshots": {path: asdict(snapshot) for path, snapshot in snapshots.items()},
            },
        )


FILE_EFFECT_TOOL_SPEC = ToolSpec(
    tool_id="lora.internal.persist_file_effects",
    version="1",
    definition=ToolDefinition(
        name="lora_persist_file_effects",
        description="Persist an internal batch of observed workspace file effects.",
        parameters={
            "type": "object",
            "properties": {"batch": {"type": "object"}},
            "required": ["batch"],
            "additionalProperties": False,
        },
    ),
    side_effect=ToolSideEffect.WRITE,
    idempotency=IdempotencyPolicy.REQUIRES_KEY,
    resource_key="workspace",
    sandbox_profile="workspace-write",
)


class FileEffectToolExecutor:
    sandbox_support = SandboxExecutorSupport(
        profiles=("workspace-write",),
        durable_reconnect=True,
        deployment_fingerprint="lora:file-effects:v1",
    )

    async def execute(
        self, spec: ToolSpec, call: Any, context: ToolExecutionContext
    ) -> object:
        del spec, context
        arguments = thaw_json(call.arguments)
        batch = DeferredFileEffectBatch.from_dict(dict(arguments["batch"]))
        await asyncio.to_thread(process_file_effect_batch, batch)
        return {"batch_id": batch.batch_id, "status": "completed"}


def process_file_effect_batch(batch: DeferredFileEffectBatch) -> None:
    """Execute one idempotently admitted diff batch; scheduling belongs to Pygent."""

    session_dir = EventStore(batch.case_run_ref).session_dir
    baseline_store = FileEffectBaselineStore(session_dir)
    tracker = FileEffectTracker(workspace_root=batch.workspace_root, store=EventStore(batch.case_run_ref))
    declared = [effect for job in batch.jobs if job.include_declared for effect in job.declared]
    if not any(job.requires_snapshot for job in batch.jobs):
        tracker.append_effects(declared, turn_id=batch.turn_id)
        return
    baseline = baseline_store.load()
    current = tracker.snapshot_workspace()
    if baseline is None:
        tracker.append_effects(declared, turn_id=batch.turn_id)
    else:
        primary = _primary_job(batch)
        observed = tracker.observed_effects(
            baseline,
            current,
            tool_name=primary.tool_name,
            tool_call_id=primary.tool_call_id,
        )
        tracker.append_effects(tracker.merge_effects(declared, observed), turn_id=batch.turn_id)
    baseline_store.save(current)


def _primary_job(batch: DeferredFileEffectBatch) -> DeferredFileEffectJob:
    for job in reversed(batch.jobs):
        if job.requires_snapshot and job.tool_name != "read":
            return job
    for job in reversed(batch.jobs):
        if job.requires_snapshot:
            return job
    return batch.jobs[-1]


__all__ = [
    "DeferredFileEffectBatch",
    "DeferredFileEffectJob",
    "FILE_EFFECT_TOOL_SPEC",
    "FileEffectBaselineStore",
    "FileEffectToolExecutor",
    "FileSnapshot",
    "process_file_effect_batch",
]
