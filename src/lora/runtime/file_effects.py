from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from lora.core.io import read_json, utc_now, write_json
from lora.schema import CaseRunRef
from lora.tracing.events import EventStore

PENDING_FILE_EFFECT_STATUSES = frozenset({"queued", "running"})
DEFAULT_DIFF_PENDING_WAIT_SECONDS = 0.5

FileEffectBatchStatus = Literal["queued", "running", "completed", "failed", "skipped"]


@dataclass(slots=True)
class DeferredFileEffectJob:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    turn_id: str | None
    declared: list[Any]
    include_declared: bool = True


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
        return cls(
            batch_id=f"filefx-{uuid.uuid4().hex}",
            case_run_ref=case_run_ref,
            workspace_root=str(Path(workspace_root).expanduser().resolve()),
            jobs=list(jobs),
            turn_id=turn_id,
        )

    @property
    def tool_call_ids(self) -> list[str]:
        return [job.tool_call_id for job in self.jobs]


class FileEffectPendingStore:
    def __init__(self, session_dir: str | Path | None):
        self.session_dir = Path(session_dir).expanduser().resolve() if session_dir is not None else None
        self.path = self.session_dir / "state" / "file_effects_pending.json" if self.session_dir is not None else None

    def read(self) -> dict[str, Any]:
        if self.path is None or not self.path.exists():
            return {"batches": {}}
        data = read_json(self.path, default={"batches": {}})
        batches = data.get("batches")
        return {"batches": batches if isinstance(batches, dict) else {}}

    def mark_queued(
        self,
        *,
        batch_id: str,
        case_run_ref: CaseRunRef,
        turn_id: str | None,
        tool_call_ids: list[str],
    ) -> None:
        self._upsert(
            batch_id,
            {
                "batch_id": batch_id,
                "session_id": case_run_ref.session_id,
                "case_id": case_run_ref.case_id,
                "case_run_id": case_run_ref.case_run_id,
                "turn_id": turn_id,
                "status": "queued",
                "queued_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "tool_call_ids": list(tool_call_ids),
                "error": None,
            },
        )

    def mark_running(self, batch_id: str) -> None:
        self._upsert(batch_id, {"status": "running", "started_at": utc_now(), "error": None})

    def mark_completed(self, batch_id: str) -> None:
        self._upsert(batch_id, {"status": "completed", "finished_at": utc_now(), "error": None})

    def mark_failed(self, batch_id: str, error: str) -> None:
        self._upsert(batch_id, {"status": "failed", "finished_at": utc_now(), "error": error})

    def mark_skipped(self, batch_id: str, reason: str) -> None:
        self._upsert(batch_id, {"status": "skipped", "finished_at": utc_now(), "error": reason})

    def pending(
        self,
        *,
        case_run_ref: CaseRunRef,
        scope: Literal["turn", "run", "session"],
        turn_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = []
        for row in self.read()["batches"].values():
            if row.get("status") not in PENDING_FILE_EFFECT_STATUSES:
                continue
            if row.get("session_id") != case_run_ref.session_id:
                continue
            if scope in {"run", "turn"} and row.get("case_run_id") != case_run_ref.case_run_id:
                continue
            if scope == "turn" and row.get("turn_id") != turn_id:
                continue
            rows.append(dict(row))
        return sorted(rows, key=lambda row: str(row.get("queued_at") or ""))

    def _upsert(self, batch_id: str, fields: dict[str, Any]) -> None:
        if self.path is None:
            return
        state = self.read()
        current = dict(state["batches"].get(batch_id) or {})
        current.update(fields)
        state["batches"][batch_id] = current
        write_json(self.path, state)


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


def pending_file_effect_batches(
    case_run_ref: CaseRunRef,
    *,
    scope: Literal["turn", "run", "session"] = "run",
    turn_id: str | None = None,
) -> list[dict[str, Any]]:
    session_dir = EventStore(case_run_ref).session_dir
    return FileEffectPendingStore(session_dir).pending(case_run_ref=case_run_ref, scope=scope, turn_id=turn_id)


async def wait_for_pending_file_effects(
    case_run_ref: CaseRunRef,
    *,
    scope: Literal["turn", "run", "session"] = "run",
    turn_id: str | None = None,
    timeout_seconds: float = DEFAULT_DIFF_PENDING_WAIT_SECONDS,
) -> list[dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + max(0.0, timeout_seconds)
    pending = pending_file_effect_batches(case_run_ref, scope=scope, turn_id=turn_id)
    while pending and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
        pending = pending_file_effect_batches(case_run_ref, scope=scope, turn_id=turn_id)
    return pending
