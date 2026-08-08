from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pygent import IdempotencyPolicy, ToolDefinition, ToolSideEffect, ToolSpec, thaw_json
from pygent.tool.executors import SandboxExecutorSupport, ToolExecutionContext

from lora.core.io import read_json, utc_now, write_json
from lora.schema import CaseRunRef
from lora.tracing.events import EventStore

@dataclass(slots=True)
class DeferredFileEffectJob:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    turn_id: str | None
    declared: list[Any]
    include_declared: bool = True
    requires_snapshot: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "args": self.args,
            "turn_id": self.turn_id,
            "declared": [_file_effect_to_dict(effect) for effect in self.declared],
            "include_declared": self.include_declared,
            "requires_snapshot": self.requires_snapshot,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeferredFileEffectJob":
        return cls(
            tool_call_id=str(data["tool_call_id"]),
            tool_name=str(data["tool_name"]),
            args=dict(data.get("args") or {}),
            turn_id=data.get("turn_id"),
            declared=[_file_effect_from_dict(effect) for effect in data.get("declared", [])],
            include_declared=bool(data.get("include_declared", True)),
            requires_snapshot=bool(data.get("requires_snapshot", True)),
        )


@dataclass(slots=True)
class DeferredFileEffectBatch:
    batch_id: str
    case_run_ref: CaseRunRef
    workspace_root: str
    jobs: list[DeferredFileEffectJob]
    turn_id: str | None

    @classmethod
    def create(
        cls,
        *,
        case_run_ref: CaseRunRef,
        workspace_root: str | Path,
        jobs: list[DeferredFileEffectJob],
    ) -> DeferredFileEffectBatch:
        turn_id = jobs[0].turn_id if jobs else None
        identity = "\0".join(job.tool_call_id for job in jobs) or uuid.uuid4().hex
        import hashlib

        return cls(
            batch_id=f"filefx-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}",
            case_run_ref=case_run_ref,
            workspace_root=str(Path(workspace_root).expanduser().resolve()),
            jobs=list(jobs),
            turn_id=turn_id,
        )

    @property
    def tool_call_ids(self) -> list[str]:
        return [job.tool_call_id for job in self.jobs]

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "case_run_ref": self.case_run_ref.to_dict(),
            "workspace_root": self.workspace_root,
            "jobs": [job.to_dict() for job in self.jobs],
            "turn_id": self.turn_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeferredFileEffectBatch":
        return cls(
            batch_id=str(data["batch_id"]),
            case_run_ref=CaseRunRef.from_dict(dict(data["case_run_ref"])),
            workspace_root=str(data["workspace_root"]),
            jobs=[DeferredFileEffectJob.from_dict(job) for job in data.get("jobs", [])],
            turn_id=data.get("turn_id"),
        )


class FileEffectBaselineStore:
    def __init__(self, session_dir: str | Path | None):
        self.session_dir = Path(session_dir).expanduser().resolve() if session_dir is not None else None
        self.path = self.session_dir / "state" / "file_effects_baseline.json" if self.session_dir is not None else None

    def load(self) -> dict[str, Any] | None:
        if self.path is None or not self.path.exists():
            return None
        from .tools import FileSnapshot

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

    from .tools import FileEffectTracker

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


def _file_effect_to_dict(effect: Any) -> dict[str, Any]:
    if isinstance(effect, dict):
        return dict(effect)
    return asdict(effect)


def _file_effect_from_dict(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    from .tools import FileEffect

    return FileEffect(**data)
