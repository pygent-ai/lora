# Deferred File Effects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Lora workspace file-effect tracking out of the synchronous tool-call path so bash results and the next chat turn are not blocked by full workspace snapshots.

**Architecture:** Add a `src/lora/runtime/file_effects.py` module that owns deferred jobs, pending state, baseline persistence, and a session/workspace scoped background worker. Keep `FileEffectTracker` in `src/lora/runtime/tools.py`, but teach `ToolInterceptor` to collect deferred jobs when requested. Wire `LoraAgent.stream()` to enqueue those jobs after the final assistant output, and make `DiffTool` report pending deferred work.

**Tech Stack:** Python 3.13, asyncio, unittest `IsolatedAsyncioTestCase`, existing `EventStore`, existing `FileEffectTracker`, existing `DiffTool`.

---

## File Structure

- Create `src/lora/runtime/file_effects.py`
  - Owns `DeferredFileEffectJob`, `DeferredFileEffectBatch`, `FileEffectPendingStore`, `FileEffectBaselineStore`, `FileEffectBackgroundWorker`, worker registry helpers, and pending wait/query helpers.
  - Avoids top-level import of `FileEffectTracker` to prevent circular imports. Worker methods import `FileEffectTracker` inside processing methods.
- Modify `src/lora/runtime/tools.py`
  - Imports `DeferredFileEffectJob`.
  - Adds `defer_file_effects` option to `ToolInterceptor`.
  - Keeps current synchronous behavior when `defer_file_effects=False`.
  - Collects deferred jobs and exposes `drain_file_effect_jobs()`.
- Modify `src/lora/runtime/agent.py`
  - Imports `DeferredFileEffectBatch` and `get_file_effect_worker`.
  - Passes `defer_file_effects=True` to `ToolInterceptor`.
  - Enqueues drained jobs before returning from final assistant output.
- Modify `src/lora/tracing/diffing.py`
  - Lazily imports pending helpers inside `DiffTool.forward()` to avoid an import cycle through `lora.runtime.__init__`.
  - Adds `pending_wait_seconds` constructor argument.
  - Adds `pending` metadata to summary, patch, and json responses when pending work remains.
- Add `tests/unit/test_file_effects.py`
  - Unit coverage for pending state, baseline persistence, worker processing, worker failure isolation, and pending queries.
- Modify `tests/unit/test_tools.py`
  - Unit coverage for deferred `ToolInterceptor` behavior.
- Modify `tests/unit/test_runtime_adapter.py`
  - Unit coverage that `LoraAgent.stream()` enqueues after final output without waiting.
- Modify `tests/scenario/test_file_effect_tracking_flow.py`
  - Keep existing synchronous scenario coverage, and add a deferred worker scenario.

## Decisions Locked For This Implementation

- Pending state is persisted only in `state/file_effects_pending.json`; no new `file.effects.*` event types are added in this pass.
- `DiffTool` waits up to `pending_wait_seconds=0.5` before returning `pending: true`.
- Baseline snapshots are persisted in `state/file_effects_baseline.json`.
- Agent uses deferred tracking. Direct `ToolInterceptor(..., track_file_effects=True)` keeps synchronous behavior unless `defer_file_effects=True` is passed.
- When a deferred batch has multiple jobs, observed snapshot changes are attributed to the last non-read job in the batch, falling back to the last job. Declared effects keep their original per-tool attribution.

---

### Task 1: Add Deferred State Types And Stores

**Files:**
- Create: `src/lora/runtime/file_effects.py`
- Test: `tests/unit/test_file_effects.py`

- [ ] **Step 1: Write failing tests for pending state and baseline persistence**

Create `tests/unit/test_file_effects.py` with:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lora.runtime.file_effects import (
    DeferredFileEffectBatch,
    DeferredFileEffectJob,
    FileEffectBaselineStore,
    FileEffectPendingStore,
    pending_file_effect_batches,
)
from lora.runtime.tools import FileSnapshot
from lora.schema import CaseRunRef


class FileEffectStateTests(unittest.IsolatedAsyncioTestCase):
    def test_pending_store_tracks_batch_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            run_dir.mkdir(parents=True)
            run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=run_dir)
            store = FileEffectPendingStore(session_dir)

            store.mark_queued(
                batch_id="batch-1",
                case_run_ref=run,
                turn_id="turn-0001",
                tool_call_ids=["tool-1"],
            )
            store.mark_running("batch-1")
            store.mark_completed("batch-1")

            state = store.read()
            self.assertEqual(state["batches"]["batch-1"]["status"], "completed")
            self.assertEqual(state["batches"]["batch-1"]["case_run_id"], "r1")
            self.assertEqual(pending_file_effect_batches(run, scope="run"), [])

    def test_pending_query_filters_run_session_and_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            first_run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            second_run_dir = session_dir / "cases" / "chat" / "runs" / "r2"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            first_run_dir.mkdir(parents=True)
            second_run_dir.mkdir(parents=True)
            first = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=first_run_dir)
            second = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r2", run_dir=second_run_dir)
            store = FileEffectPendingStore(session_dir)

            store.mark_queued(batch_id="first", case_run_ref=first, turn_id="turn-0001", tool_call_ids=["tool-1"])
            store.mark_queued(batch_id="second", case_run_ref=second, turn_id="turn-0002", tool_call_ids=["tool-2"])

            self.assertEqual([row["batch_id"] for row in pending_file_effect_batches(first, scope="run")], ["first"])
            self.assertEqual(
                [row["batch_id"] for row in pending_file_effect_batches(first, scope="turn", turn_id="turn-0001")],
                ["first"],
            )
            self.assertEqual(
                [row["batch_id"] for row in pending_file_effect_batches(first, scope="session")],
                ["first", "second"],
            )

    def test_baseline_store_round_trips_file_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            store = FileEffectBaselineStore(session_dir)
            snapshot = FileSnapshot(
                path=str((Path(tmp) / "workspace" / "demo.txt").resolve()),
                exists=True,
                kind="file",
                size=4,
                mtime_ns=123,
                content_hash="hash-1",
                content="demo",
                content_available=True,
            )

            store.save({snapshot.path: snapshot})

            loaded = store.load()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded[snapshot.path].content_hash, "hash-1")
            self.assertEqual(loaded[snapshot.path].content, "demo")

    def test_batch_create_records_tool_call_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=Path(tmp) / "run")
            job = DeferredFileEffectJob(
                tool_call_id="tool-1",
                tool_name="bash",
                args={"command": "echo hi"},
                turn_id="turn-0001",
                declared=[],
            )

            batch = DeferredFileEffectBatch.create(case_run_ref=run, workspace_root=Path(tmp), jobs=[job])

            self.assertEqual(batch.case_run_ref.case_run_id, "r1")
            self.assertEqual(batch.turn_id, "turn-0001")
            self.assertEqual(batch.tool_call_ids, ["tool-1"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify they fail because the module does not exist**

Run:

```powershell
uv run python -m pytest tests/unit/test_file_effects.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'lora.runtime.file_effects'`.

- [ ] **Step 3: Add the state module**

Create `src/lora/runtime/file_effects.py` with:

```python
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
    ) -> "DeferredFileEffectBatch":
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

    def pending(self, *, case_run_ref: CaseRunRef, scope: str, turn_id: str | None = None) -> list[dict[str, Any]]:
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
        return {str(path): FileSnapshot(**snapshot) for path, snapshot in raw_snapshots.items() if isinstance(snapshot, dict)}

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
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```powershell
uv run python -m pytest tests/unit/test_file_effects.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- src/lora/runtime/file_effects.py tests/unit/test_file_effects.py
git commit -m "Add deferred file effect state stores"
```

---

### Task 2: Add Background Worker Processing

**Files:**
- Modify: `src/lora/runtime/file_effects.py`
- Test: `tests/unit/test_file_effects.py`

- [ ] **Step 1: Add failing worker tests**

Append these tests to `FileEffectStateTests` in `tests/unit/test_file_effects.py`:

```python
    async def test_worker_processes_batch_and_records_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            workspace = Path(tmp) / "workspace"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            workspace.mkdir()
            path = workspace / "demo.txt"
            path.write_text("old\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=run_dir)

            from lora.runtime.file_effects import FileEffectBackgroundWorker
            from lora.runtime.tools import FileEffectTracker
            from lora.tracing import EventStore

            tracker = FileEffectTracker(workspace_root=workspace, store=EventStore(run))
            FileEffectBaselineStore(session_dir).save(tracker.snapshot_workspace())
            path.write_text("new\n", encoding="utf-8")
            worker = FileEffectBackgroundWorker(session_dir=session_dir, workspace_root=workspace)
            job = DeferredFileEffectJob(
                tool_call_id="tool-bash",
                tool_name="bash",
                args={"command": "rewrite demo"},
                turn_id="turn-0001",
                declared=[],
            )

            worker.enqueue(DeferredFileEffectBatch.create(case_run_ref=run, workspace_root=workspace, jobs=[job]))
            await worker.wait_idle()

            file_events = list(EventStore.iter_jsonl(run_dir / "file_events.jsonl"))
            diff_events = list(EventStore.iter_jsonl(run_dir / "diffs" / "diff_events.jsonl"))
            self.assertEqual([event["type"] for event in file_events], ["file.edit"])
            self.assertEqual(file_events[0]["payload"]["tool_call_id"], "tool-bash")
            self.assertEqual(diff_events[0]["change_type"], "edit")
            self.assertEqual(pending_file_effect_batches(run, scope="run"), [])

    async def test_worker_marks_failure_without_raising_to_caller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            workspace = Path(tmp) / "workspace"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=run_dir)

            from lora.runtime.file_effects import FileEffectBackgroundWorker

            worker = FileEffectBackgroundWorker(session_dir=session_dir, workspace_root=workspace, snapshot_timeout_seconds=0.001)
            worker._snapshot_workspace = _raise_snapshot_failure
            job = DeferredFileEffectJob(
                tool_call_id="tool-bash",
                tool_name="bash",
                args={"command": "fail tracking"},
                turn_id="turn-0001",
                declared=[],
            )

            worker.enqueue(DeferredFileEffectBatch.create(case_run_ref=run, workspace_root=workspace, jobs=[job]))
            await worker.wait_idle()

            state = FileEffectPendingStore(session_dir).read()
            row = next(iter(state["batches"].values()))
            self.assertEqual(row["status"], "failed")
            self.assertIn("snapshot exploded", row["error"])
```

Add this helper at module level:

```python
async def _raise_snapshot_failure(tracker):
    raise RuntimeError("snapshot exploded")
```

- [ ] **Step 2: Run worker tests and verify they fail because the worker class is missing**

Run:

```powershell
uv run python -m pytest tests/unit/test_file_effects.py -q
```

Expected: FAIL with `ImportError` or `AttributeError` for `FileEffectBackgroundWorker`.

- [ ] **Step 3: Implement worker and registry**

Append this code to `src/lora/runtime/file_effects.py`:

```python
class FileEffectBackgroundWorker:
    def __init__(
        self,
        *,
        session_dir: str | Path | None,
        workspace_root: str | Path,
        snapshot_timeout_seconds: float = 30.0,
    ):
        self.session_dir = Path(session_dir).expanduser().resolve() if session_dir is not None else None
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.snapshot_timeout_seconds = snapshot_timeout_seconds
        self.pending_store = FileEffectPendingStore(self.session_dir)
        self.baseline_store = FileEffectBaselineStore(self.session_dir)
        self._queue: asyncio.Queue[DeferredFileEffectBatch] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    def enqueue(self, batch: DeferredFileEffectBatch) -> None:
        if not batch.jobs:
            return
        self.pending_store.mark_queued(
            batch_id=batch.batch_id,
            case_run_ref=batch.case_run_ref,
            turn_id=batch.turn_id,
            tool_call_ids=batch.tool_call_ids,
        )
        self._queue.put_nowait(batch)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def wait_idle(self) -> None:
        await self._queue.join()
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        while not self._queue.empty():
            batch = await self._queue.get()
            try:
                await self._process_batch(batch)
            finally:
                self._queue.task_done()

    async def _process_batch(self, batch: DeferredFileEffectBatch) -> None:
        self.pending_store.mark_running(batch.batch_id)
        try:
            from .tools import FileEffectTracker

            store = EventStore(batch.case_run_ref)
            tracker = FileEffectTracker(workspace_root=self.workspace_root, store=store)
            baseline = self.baseline_store.load()
            current = await self._snapshot_workspace(tracker)
            declared = [effect for job in batch.jobs if job.include_declared for effect in job.declared]
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

    async def _snapshot_workspace(self, tracker: Any) -> dict[str, Any]:
        return await asyncio.wait_for(
            asyncio.to_thread(tracker.snapshot_workspace),
            timeout=max(0.001, self.snapshot_timeout_seconds),
        )

    @staticmethod
    def _primary_job(batch: DeferredFileEffectBatch) -> DeferredFileEffectJob:
        for job in reversed(batch.jobs):
            if job.tool_name != "read":
                return job
        return batch.jobs[-1]


_WORKERS: dict[tuple[str, str], FileEffectBackgroundWorker] = {}


def get_file_effect_worker(
    *,
    session_dir: str | Path | None,
    workspace_root: str | Path,
) -> FileEffectBackgroundWorker:
    session_key = str(Path(session_dir).expanduser().resolve()) if session_dir is not None else ""
    workspace_key = str(Path(workspace_root).expanduser().resolve())
    key = (session_key, workspace_key)
    worker = _WORKERS.get(key)
    if worker is None:
        worker = FileEffectBackgroundWorker(session_dir=session_dir, workspace_root=workspace_root)
        _WORKERS[key] = worker
    return worker
```

- [ ] **Step 4: Run worker tests and verify they pass**

Run:

```powershell
uv run python -m pytest tests/unit/test_file_effects.py -q
```

Expected: `6 passed`.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- src/lora/runtime/file_effects.py tests/unit/test_file_effects.py
git commit -m "Add deferred file effect background worker"
```

---

### Task 3: Add Deferred Mode To ToolInterceptor

**Files:**
- Modify: `src/lora/runtime/tools.py`
- Test: `tests/unit/test_tools.py`

- [ ] **Step 1: Add failing tests for deferred interception**

Add these tests to `ToolTests` in `tests/unit/test_tools.py`:

```python
    async def test_tool_interceptor_deferred_mode_does_not_snapshot_before_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(
                EventStore(run),
                workspace_root=workspace,
                track_file_effects=True,
                defer_file_effects=True,
            )
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            with unittest.mock.patch(
                "lora.runtime.tools.FileEffectTracker.snapshot_workspace",
                side_effect=AssertionError("snapshot should be deferred"),
            ):
                result = await interceptor.call_tool("bash", {"command": "echo hi"}, ctx, lambda command: "ok")

            self.assertEqual(result.status, "success")
            tool_results = list(EventStore.iter_jsonl(Path(run.run_dir) / "tool_results.jsonl"))
            self.assertEqual(tool_results[0]["status"], "success")
            self.assertFalse((Path(run.run_dir) / "file_events.jsonl").exists())

    async def test_tool_interceptor_drain_file_effect_jobs_returns_declared_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            path = workspace / "demo.txt"
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=Path(tmp) / "run")
            interceptor = ToolInterceptor(
                EventStore(run),
                workspace_root=workspace,
                track_file_effects=True,
                defer_file_effects=True,
            )
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            await interceptor.call_tool(
                "write",
                {"file_path": str(path), "content": "hello\n"},
                ctx,
                lambda file_path, content: Path(file_path).write_text(content, encoding="utf-8"),
            )

            jobs = interceptor.drain_file_effect_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].tool_name, "write")
            self.assertEqual(jobs[0].turn_id, "turn-0001")
            self.assertEqual(jobs[0].declared[0].type, "file.write")
            self.assertEqual(interceptor.drain_file_effect_jobs(), [])
```

Add `import unittest.mock` near the top of `tests/unit/test_tools.py`.

- [ ] **Step 2: Run targeted tests and verify they fail**

Run:

```powershell
uv run python -m pytest tests/unit/test_tools.py::ToolTests::test_tool_interceptor_deferred_mode_does_not_snapshot_before_result tests/unit/test_tools.py::ToolTests::test_tool_interceptor_drain_file_effect_jobs_returns_declared_metadata -q
```

Expected: FAIL with `TypeError: ToolInterceptor.__init__() got an unexpected keyword argument 'defer_file_effects'`.

- [ ] **Step 3: Implement deferred interception**

Modify imports in `src/lora/runtime/tools.py`:

```python
from .file_effects import DeferredFileEffectJob
```

Modify `ToolInterceptor.__init__`:

```python
        defer_file_effects: bool = False,
```

Inside `__init__`, after `self.bash_full_output_allowlist`:

```python
        self.defer_file_effects = defer_file_effects
        self._file_effect_jobs: list[DeferredFileEffectJob] = []
```

Add this method to `ToolInterceptor`:

```python
    def drain_file_effect_jobs(self) -> list[DeferredFileEffectJob]:
        jobs = list(self._file_effect_jobs)
        self._file_effect_jobs.clear()
        return jobs
```

Replace the pre-tool tracking block in `call_tool()`:

```python
        before: dict[str, FileSnapshot] | None = None
        declared: list[FileEffect] = []
        if self.file_effect_tracker is not None:
            declared = self.file_effect_tracker.declared_effects(name, args, call_id)
            if not self.defer_file_effects:
                before = self.file_effect_tracker.snapshot_workspace()
```

In the exception branch, wrap `_append_file_effects_after_tool(...)`:

```python
            if self.defer_file_effects:
                self._file_effect_jobs.append(
                    DeferredFileEffectJob(
                        tool_call_id=call_id,
                        tool_name=name,
                        args=dict(args),
                        turn_id=ctx.turn_id,
                        declared=declared,
                    )
                )
            else:
                self._append_file_effects_after_tool(
                    before,
                    declared,
                    tool_name=name,
                    tool_call_id=call_id,
                    turn_id=ctx.turn_id,
                )
```

In the structured tool error branch, use `include_declared=False`:

```python
            if self.defer_file_effects:
                self._file_effect_jobs.append(
                    DeferredFileEffectJob(
                        tool_call_id=call_id,
                        tool_name=name,
                        args=dict(args),
                        turn_id=ctx.turn_id,
                        declared=declared,
                        include_declared=False,
                    )
                )
            else:
                self._append_file_effects_after_tool(
                    before,
                    declared,
                    tool_name=name,
                    tool_call_id=call_id,
                    turn_id=ctx.turn_id,
                    include_declared=False,
                )
```

In the success branch, replace the unconditional `_append_file_effects_after_tool(...)` with:

```python
        if self.defer_file_effects:
            self._file_effect_jobs.append(
                DeferredFileEffectJob(
                    tool_call_id=call_id,
                    tool_name=name,
                    args=dict(args),
                    turn_id=ctx.turn_id,
                    declared=declared,
                )
            )
        else:
            self._append_file_effects_after_tool(
                before,
                declared,
                tool_name=name,
                tool_call_id=call_id,
                turn_id=ctx.turn_id,
            )
```

- [ ] **Step 4: Run targeted deferred tests**

Run:

```powershell
uv run python -m pytest tests/unit/test_tools.py::ToolTests::test_tool_interceptor_deferred_mode_does_not_snapshot_before_result tests/unit/test_tools.py::ToolTests::test_tool_interceptor_drain_file_effect_jobs_returns_declared_metadata -q
```

Expected: `2 passed`.

- [ ] **Step 5: Run existing tool tests to preserve synchronous behavior**

Run:

```powershell
uv run python -m pytest tests/unit/test_tools.py tests/scenario/test_file_effect_tracking_flow.py -q
```

Expected: all tests pass. Existing scenario tests should keep passing because they do not pass `defer_file_effects=True`.

- [ ] **Step 6: Commit Task 3**

```powershell
git add -- src/lora/runtime/tools.py tests/unit/test_tools.py
git commit -m "Add deferred file effect jobs to tool interceptor"
```

---

### Task 4: Enqueue Deferred Jobs After LoraAgent Final Output

**Files:**
- Modify: `src/lora/runtime/agent.py`
- Test: `tests/unit/test_runtime_adapter.py`

- [ ] **Step 1: Add failing agent test**

Add this test to `AgentRuntimeAdapterTests` in `tests/unit/test_runtime_adapter.py`:

```python
    def test_lora_agent_enqueues_file_effect_jobs_after_final_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = RunConfig(workspace_root=workspace, lora_root=workspace / ".lora", max_steps=3)
            manager = SessionManager(config)
            ref = manager.create("chat", mode="chat")
            run = manager.start_case_run(ref.session_id, "chat")
            session = manager.load(ref.session_id)
            agent = FakeBashLoraAgent(config)
            agent.llm = BashThenAnswerLLM()
            recording_worker = _RecordingFileEffectWorker()

            with patch("lora.runtime.agent.get_file_effect_worker", return_value=recording_worker):
                result = asyncio.run(
                    AgentRuntimeAdapter(agent=agent, config=config, session_manager=manager).run_turn(
                        session=session,
                        user_input="run bash",
                        case_run_ref=run,
                        turn_id="turn-0001",
                    )
                )

            self.assertEqual(result.final_answer, "noticed")
            self.assertEqual(len(recording_worker.batches), 1)
            self.assertEqual(recording_worker.batches[0].jobs[0].tool_name, "bash")
            self.assertEqual(recording_worker.batches[0].turn_id, "turn-0001")
```

Add this helper near the other fake classes:

```python
class _RecordingFileEffectWorker:
    def __init__(self) -> None:
        self.batches: list[Any] = []

    def enqueue(self, batch: Any) -> None:
        self.batches.append(batch)
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```powershell
uv run python -m pytest tests/unit/test_runtime_adapter.py::AgentRuntimeAdapterTests::test_lora_agent_enqueues_file_effect_jobs_after_final_output -q
```

Expected: FAIL with `AttributeError` from patching missing `get_file_effect_worker` in `lora.runtime.agent`, or assertion that no batch was enqueued.

- [ ] **Step 3: Wire agent to deferred worker**

Modify imports in `src/lora/runtime/agent.py`:

```python
from .file_effects import DeferredFileEffectBatch, get_file_effect_worker
```

In `LoraAgent.stream()`, pass deferred mode:

```python
            track_file_effects=True,
            defer_file_effects=True,
```

Replace the final no-tool return block:

```python
            if not tool_calls:
                self._enqueue_deferred_file_effects(interceptor)
                return
```

Add this method to `LoraAgent` near `_call_context_compression_model()`:

```python
    def _enqueue_deferred_file_effects(self, interceptor: ToolInterceptor) -> None:
        if self.context_manager is None or self.case_run_ref is None:
            return
        jobs = interceptor.drain_file_effect_jobs()
        if not jobs:
            return
        batch = DeferredFileEffectBatch.create(
            case_run_ref=self.case_run_ref,
            workspace_root=self.workspace_root,
            jobs=jobs,
        )
        get_file_effect_worker(
            session_dir=self.context_manager.session_dir,
            workspace_root=self.workspace_root,
        ).enqueue(batch)
```

Also handle the `max_steps` exhausted path by enqueuing before raising:

```python
        self._enqueue_deferred_file_effects(interceptor)
        raise RuntimeError(f"Agent stopped after max_steps={max_steps}")
```

- [ ] **Step 4: Run the agent test**

Run:

```powershell
uv run python -m pytest tests/unit/test_runtime_adapter.py::AgentRuntimeAdapterTests::test_lora_agent_enqueues_file_effect_jobs_after_final_output -q
```

Expected: `1 passed`.

- [ ] **Step 5: Run focused runtime adapter tests**

Run:

```powershell
uv run python -m pytest tests/unit/test_runtime_adapter.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 4**

```powershell
git add -- src/lora/runtime/agent.py tests/unit/test_runtime_adapter.py
git commit -m "Enqueue deferred file effects after agent output"
```

---

### Task 5: Make DiffTool Pending-Aware

**Files:**
- Modify: `src/lora/tracing/diffing.py`
- Test: `tests/unit/test_file_effects.py`

- [ ] **Step 1: Add failing pending diff test**

Append this test to `FileEffectStateTests` in `tests/unit/test_file_effects.py`:

```python
    async def test_diff_tool_reports_pending_file_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "chat" / "runs" / "r1"
            workspace = Path(tmp) / "workspace"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            workspace.mkdir()
            run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=run_dir)
            FileEffectPendingStore(session_dir).mark_queued(
                batch_id="batch-pending",
                case_run_ref=run,
                turn_id="turn-0001",
                tool_call_ids=["tool-1"],
            )

            from lora.tracing import DiffTool

            result = await DiffTool(
                case_run_ref=run,
                workspace_root=workspace,
                turn_id="turn-0001",
                pending_wait_seconds=0,
            ).forward(scope="turn", format="summary")

            self.assertEqual(result["pending"], True)
            self.assertEqual(result["pending_batches"][0]["batch_id"], "batch-pending")
```

- [ ] **Step 2: Run the pending diff test and verify it fails**

Run:

```powershell
uv run python -m pytest tests/unit/test_file_effects.py::FileEffectStateTests::test_diff_tool_reports_pending_file_effects -q
```

Expected: FAIL with `TypeError: DiffTool.__init__() got an unexpected keyword argument 'pending_wait_seconds'`.

- [ ] **Step 3: Implement pending-aware diff responses**

Modify `DiffTool.__init__`:

```python
        pending_wait_seconds: float = 0.5,
```

Set:

```python
        self.pending_wait_seconds = pending_wait_seconds
```

At the start of `DiffTool.forward()`:

```python
        from lora.runtime.file_effects import wait_for_pending_file_effects

        pending_batches = await wait_for_pending_file_effects(
            self.case_run_ref,
            scope=scope,
            turn_id=self.turn_id if scope == "turn" else None,
            timeout_seconds=self.pending_wait_seconds,
        )
```

In the json branch, return:

```python
        if format == "json":
            return self._with_pending(
                {"scope": scope, "count": len(records), "diffs": records},
                pending_batches,
            )
```

In the patch branch, return:

```python
        if format == "patch":
            return self._with_pending(self._patch_result(scope, records), pending_batches)
```

In the summary branch, wrap the existing dictionary:

```python
        return self._with_pending(
            {
                "scope": scope,
                "count": len(records),
                "diffs": [
                    {
                        "diff_id": record.get("diff_id"),
                        "change_type": record.get("change_type"),
                        "path": record.get("relative_path") or record.get("path"),
                        "tool_name": record.get("tool_name"),
                        "tool_call_id": record.get("tool_call_id"),
                        "patch_available": record.get("patch_available"),
                        "patch_path": record.get("patch_path"),
                    }
                    for record in records
                ],
            },
            pending_batches,
        )
```

Add helper method to `DiffTool`:

```python
    @staticmethod
    def _with_pending(result: dict[str, Any], pending_batches: list[dict[str, Any]]) -> dict[str, Any]:
        result["pending"] = bool(pending_batches)
        if pending_batches:
            result["pending_batches"] = pending_batches
            result["message"] = "File effect tracking is still running; diff results may be incomplete."
        return result
```

- [ ] **Step 4: Run pending diff tests**

Run:

```powershell
uv run python -m pytest tests/unit/test_file_effects.py::FileEffectStateTests::test_diff_tool_reports_pending_file_effects tests/unit/test_tools.py::FileEffectTrackerSpecTests::test_file_effect_tracker_persists_patch_for_edit_and_diff_tool_returns_it -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit Task 5**

```powershell
git add -- src/lora/tracing/diffing.py tests/unit/test_file_effects.py
git commit -m "Report pending file effects from diff tool"
```

---

### Task 6: Add Deferred End-To-End Scenario And Verification

**Files:**
- Modify: `tests/scenario/test_file_effect_tracking_flow.py`
- Test: existing test suite slices

- [ ] **Step 1: Add deferred scenario coverage**

Append this test to `FileEffectTrackingScenarioTests` in `tests/scenario/test_file_effect_tracking_flow.py`:

```python
    async def test_deferred_worker_records_effects_after_tool_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / ".lora" / "sessions" / "s1"
            run_dir = session_dir / "cases" / "c1" / "runs" / "r1"
            workspace = Path(tmp) / "workspace"
            session_dir.mkdir(parents=True)
            (session_dir / "session.json").write_text("{}", encoding="utf-8")
            workspace.mkdir()
            edited = workspace / "edited.txt"
            edited.write_text("old\n", encoding="utf-8")
            run = CaseRunRef(session_id="s1", case_id="c1", case_run_id="r1", run_dir=run_dir)

            from lora.runtime.file_effects import DeferredFileEffectBatch, FileEffectBackgroundWorker, FileEffectBaselineStore
            from lora.runtime.tools import FileEffectTracker

            store = EventStore(run)
            tracker = FileEffectTracker(workspace_root=workspace, store=store)
            FileEffectBaselineStore(session_dir).save(tracker.snapshot_workspace())
            interceptor = ToolInterceptor(
                EventStore(run),
                workspace_root=workspace,
                track_file_effects=True,
                defer_file_effects=True,
            )
            ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")

            result = await interceptor.call_tool(
                "bash",
                {"command": "simulate edit"},
                ctx,
                lambda command: edited.write_text("new\n", encoding="utf-8"),
            )
            tool_results_before_worker = list(EventStore.iter_jsonl(run_dir / "tool_results.jsonl"))
            self.assertEqual(result.status, "success")
            self.assertEqual(tool_results_before_worker[0]["status"], "success")
            self.assertFalse((run_dir / "file_events.jsonl").exists())

            worker = FileEffectBackgroundWorker(session_dir=session_dir, workspace_root=workspace)
            worker.enqueue(DeferredFileEffectBatch.create(case_run_ref=run, workspace_root=workspace, jobs=interceptor.drain_file_effect_jobs()))
            await worker.wait_idle()

            file_events = list(EventStore.iter_jsonl(run_dir / "file_events.jsonl"))
            self.assertEqual([event["type"] for event in file_events], ["file.edit"])
            self.assertEqual(file_events[0]["payload"]["tool_call_id"], result.tool_call_id)
```

- [ ] **Step 2: Run scenario test**

Run:

```powershell
uv run python -m pytest tests/scenario/test_file_effect_tracking_flow.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run focused regression suite**

Run:

```powershell
uv run python -m pytest tests/unit/test_file_effects.py tests/unit/test_tools.py tests/unit/test_runtime_adapter.py tests/scenario/test_file_effect_tracking_flow.py -q
```

Expected: all tests pass.

- [ ] **Step 4: Inspect diff for accidental unrelated edits**

Run:

```powershell
git diff -- src/lora/runtime/file_effects.py src/lora/runtime/tools.py src/lora/runtime/agent.py src/lora/tracing/diffing.py tests/unit/test_file_effects.py tests/unit/test_tools.py tests/unit/test_runtime_adapter.py tests/scenario/test_file_effect_tracking_flow.py
```

Expected: diff only contains deferred file-effect implementation and tests.

- [ ] **Step 5: Commit Task 6**

```powershell
git add -- src/lora/runtime/file_effects.py src/lora/runtime/tools.py src/lora/runtime/agent.py src/lora/tracing/diffing.py tests/unit/test_file_effects.py tests/unit/test_tools.py tests/unit/test_runtime_adapter.py tests/scenario/test_file_effect_tracking_flow.py
git commit -m "Verify deferred file effect tracking flow"
```

---

## Final Verification

- [ ] **Step 1: Run all file-effect related tests**

```powershell
uv run python -m pytest tests/unit/test_file_effects.py tests/unit/test_tools.py tests/scenario/test_file_effect_tracking_flow.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run runtime adapter tests**

```powershell
uv run python -m pytest tests/unit/test_runtime_adapter.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Manually verify the original performance symptom shape**

Run a small script that uses deferred mode and patches `snapshot_workspace` to fail if called synchronously:

```powershell
uv run python - <<'PY'
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

from lora.runtime import ToolContext, ToolInterceptor
from lora.schema import CaseRunRef
from lora.tracing import EventStore

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir()
        run = CaseRunRef(session_id="s1", case_id="chat", case_run_id="r1", run_dir=Path(tmp) / "run")
        interceptor = ToolInterceptor(EventStore(run), workspace_root=workspace, track_file_effects=True, defer_file_effects=True)
        ctx = ToolContext(case_run_ref=run, turn_id="turn-0001")
        with patch("lora.runtime.tools.FileEffectTracker.snapshot_workspace", side_effect=AssertionError("sync snapshot")):
            result = await interceptor.call_tool("bash", {"command": "echo hi"}, ctx, lambda command: "ok")
        print(result.status)
        print(len(interceptor.drain_file_effect_jobs()))

asyncio.run(main())
PY
```

Expected output:

```text
success
1
```

- [ ] **Step 4: Check working tree**

```powershell
git status --short
```

Expected: only pre-existing unrelated user changes remain, or a clean tree if those were handled separately.
