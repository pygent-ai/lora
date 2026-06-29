from __future__ import annotations

import asyncio
import inspect
import queue
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal

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


class FileEffectBatchStore:
    def __init__(self, session_dir: str | Path | None):
        self.session_dir = Path(session_dir).expanduser().resolve() if session_dir is not None else None
        self.dir = self.session_dir / "state" / "file_effect_batches" if self.session_dir is not None else None

    def save(self, batch: DeferredFileEffectBatch) -> Path | None:
        if self.dir is None:
            return None
        path = self.dir / f"{batch.batch_id}.json"
        write_json(path, batch.to_dict())
        return path

    @staticmethod
    def load(path: str | Path) -> DeferredFileEffectBatch:
        return DeferredFileEffectBatch.from_dict(read_json(Path(path), default={}))

    @staticmethod
    def delete(path: str | Path) -> None:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


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


class FileEffectBackgroundWorker:
    def __init__(
        self,
        *,
        session_dir: str | Path | None,
        workspace_root: str | Path,
        snapshot_timeout_seconds: float = 30.0,
        execution_mode: Literal["thread", "process"] = "thread",
        process_launcher: Callable[[Path], None] | None = None,
    ):
        self.session_dir = Path(session_dir).expanduser().resolve() if session_dir is not None else None
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.snapshot_timeout_seconds = snapshot_timeout_seconds
        self.execution_mode = execution_mode
        self.process_launcher = process_launcher or start_detached_file_effect_worker
        self.pending_store = FileEffectPendingStore(self.session_dir)
        self.baseline_store = FileEffectBaselineStore(self.session_dir)
        self.batch_store = FileEffectBatchStore(self.session_dir)
        self._queue: queue.Queue[DeferredFileEffectBatch] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def enqueue(self, batch: DeferredFileEffectBatch) -> None:
        if not batch.jobs:
            return
        self.pending_store.mark_queued(
            batch_id=batch.batch_id,
            case_run_ref=batch.case_run_ref,
            turn_id=batch.turn_id,
            tool_call_ids=batch.tool_call_ids,
        )
        if not any(job.requires_snapshot for job in batch.jobs):
            self._process_batch(batch)
            return
        if self.execution_mode == "process":
            batch_file = self.batch_store.save(batch)
            if batch_file is None:
                self.pending_store.mark_failed(batch.batch_id, "cannot persist file effect batch without session_dir")
                return
            try:
                self.process_launcher(batch_file)
            except Exception as exc:  # noqa: BLE001 - tracking launch failure must not fail agent execution.
                self.pending_store.mark_failed(batch.batch_id, str(exc))
            return
        self._queue.put(batch)
        self._ensure_thread()

    async def wait_idle(self) -> None:
        await asyncio.to_thread(self._queue.join)
        thread = self._thread
        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join, 0.1)

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run,
                name="lora-file-effect-worker",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                batch = self._queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._thread = None
                        return
                continue
            try:
                self._process_batch(batch)
            finally:
                self._queue.task_done()

    def _process_batch(self, batch: DeferredFileEffectBatch) -> None:
        self.pending_store.mark_running(batch.batch_id)
        try:
            from .tools import FileEffectTracker

            store = EventStore(batch.case_run_ref)
            tracker = FileEffectTracker(workspace_root=self.workspace_root, store=store)
            declared = [effect for job in batch.jobs if job.include_declared for effect in job.declared]
            if not any(job.requires_snapshot for job in batch.jobs):
                tracker.append_effects(declared, turn_id=batch.turn_id)
                self.pending_store.mark_completed(batch.batch_id)
                return

            baseline = self.baseline_store.load()
            current = self._snapshot_workspace(tracker)
            if inspect.isawaitable(current):
                current = asyncio.run(current)
            if baseline is None:
                tracker.append_effects(declared, turn_id=batch.turn_id)
                self.baseline_store.save(current)
                self.pending_store.mark_completed(batch.batch_id)
                return

            primary = self._primary_job(batch)
            observed = tracker.observed_effects(
                baseline,
                current,
                tool_name=primary.tool_name,
                tool_call_id=primary.tool_call_id,
            )
            tracker.append_effects(tracker.merge_effects(declared, observed), turn_id=batch.turn_id)
            self.baseline_store.save(current)
            self.pending_store.mark_completed(batch.batch_id)
        except Exception as exc:  # noqa: BLE001 - background tracking must not fail agent execution.
            self.pending_store.mark_failed(batch.batch_id, str(exc))

    def _snapshot_workspace(self, tracker: Any) -> dict[str, Any]:
        return tracker.snapshot_workspace()

    @staticmethod
    def _primary_job(batch: DeferredFileEffectBatch) -> DeferredFileEffectJob:
        for job in reversed(batch.jobs):
            if job.requires_snapshot and job.tool_name != "read":
                return job
        for job in reversed(batch.jobs):
            if job.requires_snapshot:
                return job
        return batch.jobs[-1]


_WORKERS: dict[tuple[str, str, str], FileEffectBackgroundWorker] = {}


def get_file_effect_worker(
    *,
    session_dir: str | Path | None,
    workspace_root: str | Path,
    execution_mode: Literal["thread", "process"] = "thread",
) -> FileEffectBackgroundWorker:
    session_key = str(Path(session_dir).expanduser().resolve()) if session_dir is not None else ""
    workspace_key = str(Path(workspace_root).expanduser().resolve())
    key = (session_key, workspace_key, execution_mode)
    worker = _WORKERS.get(key)
    if worker is None:
        worker = FileEffectBackgroundWorker(
            session_dir=session_dir,
            workspace_root=workspace_root,
            execution_mode=execution_mode,
        )
        _WORKERS[key] = worker
    return worker


def process_file_effect_batch_file(batch_file: str | Path) -> None:
    path = Path(batch_file).expanduser().resolve()
    batch = FileEffectBatchStore.load(path)
    session_dir = _session_dir_from_batch_file(path)
    worker = FileEffectBackgroundWorker(
        session_dir=session_dir,
        workspace_root=batch.workspace_root,
        execution_mode="thread",
    )
    worker._process_batch(batch)
    row = FileEffectPendingStore(session_dir).read()["batches"].get(batch.batch_id, {})
    if row.get("status") in {"completed", "skipped"}:
        FileEffectBatchStore.delete(path)


def start_detached_file_effect_worker(batch_file: Path) -> None:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(  # noqa: S603 - argv is fixed; only the batch file path is data.
        [sys.executable, "-m", "lora.runtime.file_effects_worker", "--batch-file", str(batch_file)],
        **kwargs,
    )


def _file_effect_to_dict(effect: Any) -> dict[str, Any]:
    if isinstance(effect, dict):
        return dict(effect)
    return asdict(effect)


def _file_effect_from_dict(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    from .tools import FileEffect

    return FileEffect(**data)


def _session_dir_from_batch_file(path: Path) -> Path:
    return path.parent.parent.parent
